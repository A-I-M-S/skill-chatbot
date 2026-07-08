import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  type WASocket,
  type UserFacingSocketConfig,
  type ConnectionState,
  type WAMessage,
  type MessageUpsertType,
} from "@whiskeysockets/baileys";
import type { Logger } from "pino";
import type { AuthBundle } from "./auth.js";
import {
  type InboxAppender,
  type InboxLine,
  buildInboxLine,
  shouldProcessMessage,
} from "./inbox.js";
import { downloadImageFromMessage, type DownloadImageResult } from "./image.js";

export type SessionState = "ok" | "qr_needed" | "connecting";

export type SocketDeps = {
  auth: AuthBundle;
  logger: Logger;
  appendInbox: InboxAppender;
  printQR?: (qr: string) => void;
  onState?: (s: SessionState) => void;
  onMessageId?: (id: string) => void;
  onLastMessageAt?: (ts: string) => void;
  photosDir?: string;
  maxImageBytes?: number;
  imageDownloader?: (msg: WAMessage) => Promise<Buffer | undefined>;
  onImageRejected?: (msg: WAMessage, reason: "oversize" | "missing_mimetype" | "download_failed" | "write_failed" | "no_image") => void;
};

export type SocketFactory = (cfg: UserFacingSocketConfig) => WASocket;

export const defaultSocketFactory: SocketFactory = (cfg) => makeWASocket(cfg);

export type ConnectOptions = SocketDeps & {
  socketFactory?: SocketFactory;
  maxBackoffMs?: number;
  giveupQrCount?: number;
  onResocket?: (start: () => void) => void;
};

export type SocketStatus = {
  session: SessionState;
  last_message_at: string | null;
  reconnecting: boolean;
  attempt: number;
  qr_needed_count: number;
};

export type SocketController = {
  start: () => Promise<void>;
  stop: () => Promise<void>;
  getStatus: () => SocketStatus;
  send: (jid: string, content: { text: string }) => Promise<unknown>;
  session: () => SessionState;
};

const backoffSteps = [1000, 2000, 5000, 10000, 20000, 40000, 60000];

export function nextBackoffMs(attempt: number, maxMs: number): number {
  const capped = Math.min(Math.max(attempt, 1), backoffSteps.length) - 1;
  return Math.min(backoffSteps[capped] ?? maxMs, maxMs);
}

export function createSocket(opts: ConnectOptions): SocketController {
  const factory = opts.socketFactory ?? defaultSocketFactory;
  const logger = opts.logger.child({ mod: "socket" });
  const maxBackoff = opts.maxBackoffMs ?? 60_000;
  const giveupQrCount = opts.giveupQrCount ?? 4;

  let session: SessionState = "connecting";
  let lastMessageAt: string | null = null;
  let attempt = 0;
  let qrNeededCount = 0;
  let stopped = false;
  let reconnecting = false;
  let currentSock: WASocket | null = null;
  let reconnectTimer: NodeJS.Timeout | null = null;

  const setSession = (s: SessionState) => {
    if (session === s) return;
    session = s;
    opts.onState?.(s);
  };

  const wireSocket = (sock: WASocket) => {
    const onConnectionUpdate = (u: Partial<ConnectionState>) => {
      if (u.qr) {
        qrNeededCount += 1;
        setSession("qr_needed");
        logger.info({ qrLen: u.qr.length, qrNeededCount }, "qr received");
        opts.printQR?.(u.qr);
        if (qrNeededCount >= giveupQrCount) {
          logger.error(
            { qrNeededCount, giveupQrCount },
            "too many QR cycles — give up, run `npm run auth` to relink"
          );
        }
      }
      if (u.connection === "open") {
        attempt = 0;
        qrNeededCount = 0;
        setSession("ok");
        logger.info("connection open");
      }
      if (u.connection === "connecting") {
        setSession("connecting");
      }
      if (u.connection === "close") {
        const err = u.lastDisconnect?.error as Error | undefined;
        const boomish = err as (Error & { output?: { statusCode?: number } }) | undefined;
        const statusCode = boomish?.output?.statusCode;
        const loggedOut = statusCode === DisconnectReason.loggedOut;
        logger.warn({ statusCode, loggedOut, err: err?.message }, "connection closed");
        setSession("qr_needed");
        if (loggedOut) {
          logger.error("logged out — manual relink required via `npm run auth`");
          return;
        }
        if (stopped) return;
        scheduleReconnect();
      }
    };

    const onCredsUpdate = async () => {
      try {
        await opts.auth.saveCreds();
      } catch (e) {
        logger.error({ err: String(e) }, "saveCreds failed");
      }
    };

    const onMessagesUpsert = async (data: { messages: WAMessage[]; type: MessageUpsertType }) => {
      for (const msg of data.messages) {
        if (!shouldProcessMessage(msg)) continue;
        let line: InboxLine;
        try {
          line = buildInboxLine({ msg });
        } catch (e) {
          logger.warn({ err: String(e) }, "buildInboxLine failed");
          continue;
        }
        if (line.image && opts.photosDir) {
          const result = await downloadImageFromMessage({
            msg,
            photosDir: opts.photosDir,
            maxBytes: opts.maxImageBytes,
            logger: opts.logger,
            downloader: opts.imageDownloader
              ?? (currentSock
                ? async (m) => {
                    const r = await downloadMediaMessage(
                      m,
                      "buffer",
                      {},
                      {
                        logger: opts.logger as never,
                        reuploadRequest: currentSock!.updateMediaMessage as never,
                      }
                    );
                    return r as Buffer;
                  }
                : undefined),
          });
          if (result.ok) {
            line.image = {
              ...line.image,
              path: result.meta.path,
              sha256: result.meta.sha256,
            };
          } else {
            opts.onImageRejected?.(msg, result.reason);
            logger.warn(
              { from: line.from, message_id: line.message_id, reason: result.reason },
              "image rejected, dropping image"
            );
            line.image = null;
          }
        }
        try {
          await opts.appendInbox(line);
          lastMessageAt = line.timestamp;
          opts.onMessageId?.(line.message_id);
          opts.onLastMessageAt?.(line.timestamp);
          logger.info(
            { from: line.from, message_id: line.message_id, type: data.type },
            "inbox line written"
          );
        } catch (e) {
          logger.error({ err: String(e) }, "inbox append failed");
        }
      }
    };

    sock.ev.on("connection.update", onConnectionUpdate);
    sock.ev.on("creds.update", onCredsUpdate);
    sock.ev.on("messages.upsert", onMessagesUpsert);
  };

  const closeCurrent = async () => {
    if (!currentSock) return;
    const sock = currentSock;
    currentSock = null;
    try {
      sock.ev.removeAllListeners("connection.update");
      sock.ev.removeAllListeners("creds.update");
      sock.ev.removeAllListeners("messages.upsert");
    } catch {
      /* ignore */
    }
    try {
      sock.end(undefined);
    } catch {
      /* ignore */
    }
  };

  const openSocket = async () => {
    if (stopped) return;
    await closeCurrent();
    setSession("connecting");
    const sock: WASocket = factory({
      auth: opts.auth.state,
      printQRInTerminal: false,
      markOnlineOnConnect: true,
      syncFullHistory: false,
      emitOwnEvents: false,
      // Pairing is done out-of-band via `npm run auth:code`, so the bridge
      // never needs a fast-cycling QR. Baileys' default qrTimeout (~20s
      // after the first) churns the journal with fresh QRs when a session
      // has dropped; widen it so a dropped session logs `qr_needed` once
      // and waits quietly for the operator to re-pair.
      qrTimeout: 10 * 60 * 1000,
      connectTimeoutMs: 60 * 1000,
    });
    currentSock = sock;
    wireSocket(sock);
    opts.onResocket?.(openSocket);
  };

  const scheduleReconnect = () => {
    if (stopped || reconnecting) return;
    reconnecting = true;
    attempt += 1;
    const wait = nextBackoffMs(attempt, maxBackoff);
    logger.warn({ attempt, wait }, "reconnecting after backoff");
    reconnectTimer = setTimeout(() => {
      reconnecting = false;
      if (stopped) return;
      void openSocket();
    }, wait);
  };

  const start = async (): Promise<void> => {
    stopped = false;
    await openSocket();
  };

  const stop = async (): Promise<void> => {
    stopped = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    await closeCurrent();
  };

  const send = async (jid: string, content: { text: string }) => {
    if (!currentSock) {
      throw new Error("socket not connected");
    }
    return currentSock.sendMessage(jid, content);
  };

  const getStatus = (): SocketStatus => ({
    session,
    last_message_at: lastMessageAt,
    reconnecting,
    attempt,
    qr_needed_count: qrNeededCount,
  });

  return {
    start,
    stop,
    getStatus,
    send,
    session: () => session,
  };
}

export const _testing = { backoffSteps };
