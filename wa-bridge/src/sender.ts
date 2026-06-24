import { promises as fs } from "node:fs";
import path from "node:path";
import { z } from "zod";

export type SendRequest = {
  to: string;
  text: string;
};

export const SendRequestSchema = z.object({
  to: z.string().min(1),
  text: z.string(),
});

export type SendResult =
  | { ok: true; message_id: string }
  | { ok: false; reason: string };

export type Sender = (req: SendRequest) => Promise<SendResult>;

export type SocketSend = (jid: string, content: { text: string }) => Promise<{ key?: { id?: string | null } } | undefined>;

export function makeSocketSender(send: SocketSend): Sender {
  return async (req: SendRequest): Promise<SendResult> => {
    const parsed = SendRequestSchema.safeParse(req);
    if (!parsed.success) {
      return { ok: false, reason: "bad_request" };
    }
    const jid = toJid(parsed.data.to);
    try {
      const result = await send(jid, { text: parsed.data.text });
      const id = result?.key?.id ?? null;
      return { ok: true, message_id: id ?? "unknown" };
    } catch (e) {
      const reason = e instanceof Error ? e.message : String(e);
      return { ok: false, reason };
    }
  };
}

export function makeBaileysSender(send: SocketSend): Sender {
  return makeSocketSender(send);
}

export function toJid(to: string): string {
  if (to.includes("@")) return to;
  const digits = to.replace(/\D+/g, "");
  if (!digits) return to;
  return `${digits}@s.whatsapp.net`;
}

export type OutboxEntry = {
  to: string;
  text: string;
  enqueued_at: string;
};

export type OutboxDrainOptions = {
  outboxPath: string;
  sender: Sender;
  sleep?: (ms: number) => Promise<void>;
};

export type OutboxDrainResult = {
  drained: number;
  failed: number;
  remaining: number;
};

export async function drainOutbox(opts: OutboxDrainOptions): Promise<OutboxDrainResult> {
  const sleep = opts.sleep ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  let raw: string;
  try {
    raw = await fs.readFile(opts.outboxPath, "utf8");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") {
      return { drained: 0, failed: 0, remaining: 0 };
    }
    throw e;
  }
  const lines = raw.split("\n").filter((l) => l.trim().length > 0);
  if (lines.length === 0) {
    return { drained: 0, failed: 0, remaining: 0 };
  }
  const remaining: string[] = [];
  let drained = 0;
  let failed = 0;
  for (const line of lines) {
    let entry: OutboxEntry;
    try {
      entry = JSON.parse(line) as OutboxEntry;
    } catch {
      failed += 1;
      continue;
    }
    const result = await opts.sender({ to: entry.to, text: entry.text });
    if (result.ok) {
      drained += 1;
    } else {
      failed += 1;
      remaining.push(line);
    }
    await sleep(0);
  }
  const dir = path.dirname(opts.outboxPath);
  await fs.mkdir(dir, { recursive: true });
  const next = remaining.length === 0 ? "" : remaining.join("\n") + "\n";
  await fs.writeFile(opts.outboxPath, next, "utf8");
  return { drained, failed, remaining: remaining.length };
}

export const _testing = { toJid };
