import { loadEnv } from "./env.js";
import { buildLogger } from "./log.js";
import { defaultLoadAuth } from "./auth.js";
import { createSocket } from "./socket.js";
import { fileInboxAppender } from "./inbox.js";
import { makeBaileysSender, drainOutbox } from "./sender.js";
import { startServer } from "./http.js";

export type RunDeps = {
  env?: ReturnType<typeof loadEnv>;
  loadAuth?: typeof defaultLoadAuth;
  logger?: ReturnType<typeof buildLogger>;
  inboxAppender?: ReturnType<typeof fileInboxAppender>;
  drainOutboxImpl?: typeof drainOutbox;
  startHttp?: typeof startServer;
  createSocketImpl?: typeof createSocket;
};

export type RunningBridge = {
  stop: (signal: NodeJS.Signals) => Promise<void>;
};

export async function run(deps: RunDeps = {}): Promise<RunningBridge> {
  const env = deps.env ?? loadEnv();
  const logger = deps.logger ?? buildLogger();
  const loadAuth = deps.loadAuth ?? defaultLoadAuth;
  const inboxAppender = deps.inboxAppender ?? fileInboxAppender({ inboxPath: env.INBOX_PATH });
  const drainOutboxImpl =
    deps.drainOutboxImpl ?? ((opts) => drainOutbox(opts));
  const createSocketImpl = deps.createSocketImpl ?? createSocket;
  const startHttp = deps.startHttp ?? startServer;

  const auth = await loadAuth(env.WA_AUTH_DIR);

  const printQR = (qr: string): void => {
    if (process.stdout.isTTY) {
      process.stdout.write(`\n[wa-bridge] scan this QR with WhatsApp > Linked Devices:\n${qr}\n\n`);
    } else {
      process.stdout.write(`[wa-bridge] qr=${qr}\n`);
    }
  };

  const socket = createSocketImpl({
    auth,
    logger,
    appendInbox: inboxAppender,
    printQR,
    maxBackoffMs: env.WA_RECONNECT_MAX_BACKOFF_MS,
    giveupQrCount: env.WA_RECONNECT_GIVEUP_QR,
  });

  const sender = makeBaileysSender(async (jid, content) => {
    const result = await socket.send(jid, content);
    return result as { key?: { id?: string | null } } | undefined;
  });

  const drain = () =>
    drainOutboxImpl({
      outboxPath: env.OUTBOX_PATH,
      sender,
    });

  const http = await startHttp(
    {
      token: env.WA_BRIDGE_TOKEN,
      socket: socket as unknown as Parameters<typeof startHttp>[0]["socket"],
      sender,
      drainOutbox: drain,
      logger,
    },
    env.WA_BRIDGE_PORT
  );

  await socket.start();
  logger.info({ port: env.WA_BRIDGE_PORT }, "wa-bridge started");

  let stopping = false;
  const stop = async (signal: NodeJS.Signals): Promise<void> => {
    if (stopping) return;
    stopping = true;
    logger.info({ signal }, "wa-bridge stopping");
    try {
      const drainResult = await drain();
      logger.info(drainResult, "outbox drained on shutdown");
    } catch (e) {
      logger.warn({ err: String(e) }, "outbox drain on shutdown failed");
    }
    try {
      await socket.stop();
    } catch (e) {
      logger.warn({ err: String(e) }, "socket stop failed");
    }
    try {
      await http.close();
    } catch (e) {
      logger.warn({ err: String(e) }, "http close failed");
    }
    logger.info("wa-bridge stopped");
  };

  return { stop };
}

const isDirectRun = (() => {
  if (!process.argv[1]) return false;
  const url = new URL(`file://${process.argv[1]}`).href;
  const thisUrl = new URL(import.meta.url).href;
  return url === thisUrl;
})();

if (isDirectRun) {
  run()
    .then((bridge) => {
      const onSignal = (sig: NodeJS.Signals) => {
        void bridge.stop(sig).then(
          () => process.exit(0),
          (e) => {
            process.stderr.write(`shutdown error: ${String(e)}\n`);
            process.exit(1);
          }
        );
      };
      process.on("SIGTERM", () => onSignal("SIGTERM"));
      process.on("SIGINT", () => onSignal("SIGINT"));
    })
    .catch((e) => {
      process.stderr.write(`fatal: ${String(e)}\n`);
      process.exit(1);
    });
}
