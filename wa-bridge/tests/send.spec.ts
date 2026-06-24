import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import { makeSocketSender, drainOutbox, toJid, type OutboxEntry } from "../src/sender.js";

let tmp: string;

beforeEach(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "wa-send-"));
});

afterEach(async () => {
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("toJid", () => {
  it("appends @s.whatsapp.net to bare digits", () => {
    expect(toJid("6591234567")).toBe("6591234567@s.whatsapp.net");
  });

  it("strips non-digits before jid suffixing", () => {
    expect(toJid("+65 9123 4567")).toBe("6591234567@s.whatsapp.net");
  });

  it("passes through an explicit jid", () => {
    expect(toJid("6591234567@c.us")).toBe("6591234567@c.us");
  });

  it("passes through an explicit @s.whatsapp.net jid", () => {
    expect(toJid("6591234567@s.whatsapp.net")).toBe("6591234567@s.whatsapp.net");
  });
});

describe("makeSocketSender (happy path)", () => {
  it("returns {ok: true, message_id} when the socket accepts the message", async () => {
    const send = vi.fn().mockResolvedValue({ key: { id: "OUT-1234" } });
    const sender = makeSocketSender(send);
    const r = await sender({ to: "6591234567", text: "hi" });
    expect(r).toEqual({ ok: true, message_id: "OUT-1234" });
    expect(send).toHaveBeenCalledWith("6591234567@s.whatsapp.net", { text: "hi" });
  });

  it("uses 'unknown' when the socket returns no key.id", async () => {
    const send = vi.fn().mockResolvedValue({ key: {} });
    const sender = makeSocketSender(send);
    const r = await sender({ to: "6591234567", text: "hi" });
    expect(r).toEqual({ ok: true, message_id: "unknown" });
  });
});

describe("makeSocketSender (auth-missing / failure paths)", () => {
  it("returns {ok: false, reason} when the socket send throws", async () => {
    const send = vi.fn().mockRejectedValue(new Error("not connected"));
    const sender = makeSocketSender(send);
    const r = await sender({ to: "6591234567", text: "hi" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toBe("not connected");
    }
  });

  it("rejects malformed request shape with bad_request reason", async () => {
    const send = vi.fn();
    const sender = makeSocketSender(send);
    const r = await sender({ to: "", text: "hi" });
    expect(r).toEqual({ ok: false, reason: "bad_request" });
    expect(send).not.toHaveBeenCalled();
  });

  it("treats an empty-text message as a valid request (sends anyway)", async () => {
    const send = vi.fn().mockResolvedValue({ key: { id: "OK" } });
    const sender = makeSocketSender(send);
    const r = await sender({ to: "6591234567", text: "" });
    expect(r.ok).toBe(true);
    expect(send).toHaveBeenCalled();
  });
});

describe("drainOutbox", () => {
  it("returns zeros when the outbox file does not exist", async () => {
    const outboxPath = path.join(tmp, "missing.jsonl");
    const sender = makeSocketSender(vi.fn().mockResolvedValue({ key: { id: "x" } }));
    const r = await drainOutbox({ outboxPath, sender });
    expect(r).toEqual({ drained: 0, failed: 0, remaining: 0 });
  });

  it("drains successfully sent entries and removes them", async () => {
    const outboxPath = path.join(tmp, "outbound.jsonl");
    const entries: OutboxEntry[] = [
      { to: "1", text: "a", enqueued_at: "2024-01-01T00:00:00.000Z" },
      { to: "2", text: "b", enqueued_at: "2024-01-01T00:00:01.000Z" },
    ];
    await fs.writeFile(outboxPath, entries.map((e) => JSON.stringify(e)).join("\n") + "\n", "utf8");
    const send = vi.fn().mockResolvedValue({ key: { id: "ok" } });
    const r = await drainOutbox({ outboxPath, sender: makeSocketSender(send) });
    expect(r.drained).toBe(2);
    expect(r.failed).toBe(0);
    expect(r.remaining).toBe(0);
    const after = await fs.readFile(outboxPath, "utf8");
    expect(after).toBe("");
  });

  it("keeps failed entries in the outbox for the next drain", async () => {
    const outboxPath = path.join(tmp, "outbound.jsonl");
    const entries: OutboxEntry[] = [
      { to: "1", text: "ok-msg", enqueued_at: "2024-01-01T00:00:00.000Z" },
      { to: "2", text: "bad-msg", enqueued_at: "2024-01-01T00:00:01.000Z" },
    ];
    await fs.writeFile(outboxPath, entries.map((e) => JSON.stringify(e)).join("\n") + "\n", "utf8");
    const send = vi
      .fn()
      .mockImplementationOnce(async () => ({ key: { id: "ok" } }))
      .mockImplementationOnce(async () => {
        throw new Error("auth-missing");
      });
    const r = await drainOutbox({ outboxPath, sender: makeSocketSender(send) });
    expect(r.drained).toBe(1);
    expect(r.failed).toBe(1);
    expect(r.remaining).toBe(1);
    const after = await fs.readFile(outboxPath, "utf8");
    expect(after).toContain("bad-msg");
    expect(after).not.toContain("ok-msg");
  });

  it("skips unparseable lines (counts as failed, does not throw)", async () => {
    const outboxPath = path.join(tmp, "outbound.jsonl");
    await fs.writeFile(outboxPath, "not json\n", "utf8");
    const send = vi.fn().mockResolvedValue({ key: { id: "ok" } });
    const r = await drainOutbox({ outboxPath, sender: makeSocketSender(send) });
    expect(r.failed).toBe(1);
    expect(send).not.toHaveBeenCalled();
  });
});
