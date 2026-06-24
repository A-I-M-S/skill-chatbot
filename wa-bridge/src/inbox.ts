import { promises as fs } from "node:fs";
import path from "node:path";
import { WAMessage } from "@whiskeysockets/baileys";

export type InboxImage = {
  path: string;
  sha256: string;
  filename: string;
};

export type InboxLine = {
  message_id: string;
  from: string;
  text: string;
  image: InboxImage | null;
  timestamp: string;
};

export type InboxAppender = (line: InboxLine) => Promise<void>;

function digitsOnly(jid: string): string {
  const at = jid.indexOf("@");
  const raw = at === -1 ? jid : jid.slice(0, at);
  return raw.replace(/\D+/g, "");
}

function extractText(msg: WAMessage): string {
  const m = msg.message;
  if (!m) return "";
  const conv = m.conversation;
  if (typeof conv === "string") return conv;
  const ext = m.extendedTextMessage?.text;
  if (typeof ext === "string") return ext;
  const img = m.imageMessage?.caption;
  if (typeof img === "string") return img;
  const vid = m.videoMessage?.caption;
  if (typeof vid === "string") return vid;
  const doc = m.documentMessage?.caption;
  if (typeof doc === "string") return doc;
  return "";
}

function extractImage(msg: WAMessage): InboxImage | null {
  const m = msg.message;
  if (!m) return null;
  const img = m.imageMessage;
  if (!img) return null;
  return {
    path: "",
    sha256: "",
    filename: "image.jpg",
  };
}

function timestampToIso(ts: unknown): string {
  if (typeof ts === "number" && Number.isFinite(ts)) {
    const ms = ts > 1e12 ? ts : ts * 1000;
    return new Date(ms).toISOString();
  }
  if (ts && typeof (ts as { toNumber?: () => number }).toNumber === "function") {
    try {
      const n = (ts as { toNumber: () => number }).toNumber();
      const ms = n > 1e12 ? n : n * 1000;
      return new Date(ms).toISOString();
    } catch {
      return new Date().toISOString();
    }
  }
  return new Date().toISOString();
}

export type BuildLineInput = {
  msg: WAMessage;
};

export function buildInboxLine({ msg }: BuildLineInput): InboxLine {
  const remoteJid = msg.key?.remoteJid ?? "";
  const messageId = msg.key?.id ?? "";
  return {
    message_id: messageId,
    from: digitsOnly(remoteJid),
    text: extractText(msg),
    image: extractImage(msg),
    timestamp: timestampToIso(msg.messageTimestamp),
  };
}

export type CreateInboxAppenderOptions = {
  inboxPath: string;
};

export const fileInboxAppender = ({ inboxPath }: CreateInboxAppenderOptions): InboxAppender => {
  return async (line: InboxLine): Promise<void> => {
    const dir = path.dirname(inboxPath);
    await fs.mkdir(dir, { recursive: true });
    const payload = JSON.stringify(line) + "\n";
    const handle = await fs.open(inboxPath, "a");
    try {
      await handle.writeFile(payload, { encoding: "utf8" });
      await handle.sync();
    } finally {
      await handle.close();
    }
  };
};

export function isPrivateJid(jid: string): boolean {
  if (!jid) return false;
  if (jid.endsWith("@g.us")) return false;
  if (jid.endsWith("@broadcast")) return false;
  if (jid === "status@broadcast") return false;
  return jid.endsWith("@s.whatsapp.net") || jid.endsWith("@c.us") || jid.endsWith("@lid");
}

export function isStatusBroadcast(jid: string): boolean {
  return jid === "status@broadcast";
}

export function shouldProcessMessage(msg: WAMessage): boolean {
  if (msg.key?.fromMe === true) return false;
  const jid = msg.key?.remoteJid ?? "";
  if (isStatusBroadcast(jid)) return false;
  if (!isPrivateJid(jid)) return false;
  return true;
}

export const _testing = { digitsOnly, extractText, extractImage, timestampToIso };
