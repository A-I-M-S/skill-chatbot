import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileInboxAppender, buildInboxLine, shouldProcessMessage, type InboxLine } from "../src/inbox.js";
import type { WAMessage } from "@whiskeysockets/baileys";

let tmp: string;

beforeEach(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "wa-inbox-"));
});

afterEach(async () => {
  await fs.rm(tmp, { recursive: true, force: true });
});

function makeMsg(overrides: Partial<WAMessage> = {}): WAMessage {
  return {
    key: {
      remoteJid: "6591234567@s.whatsapp.net",
      fromMe: false,
      id: "ABCDEF1234567890",
      ...(overrides.key ?? {}),
    },
    messageTimestamp: 1_700_000_000,
    message: {
      conversation: "hello",
    },
    ...overrides,
  } as WAMessage;
}

describe("buildInboxLine", () => {
  it("extracts digits-only phone from @s.whatsapp.net", () => {
    const line = buildInboxLine({ msg: makeMsg() });
    expect(line.from).toBe("6591234567");
  });

  it("strips @c.us suffix the same way", () => {
    const line = buildInboxLine({
      msg: makeMsg({ key: { remoteJid: "6591234567@c.us", fromMe: false, id: "x" } }),
    });
    expect(line.from).toBe("6591234567");
  });

  it("renders text from conversation", () => {
    const line = buildInboxLine({ msg: makeMsg() });
    expect(line.text).toBe("hello");
  });

  it("renders text from extendedTextMessage", () => {
    const line = buildInboxLine({
      msg: makeMsg({ message: { extendedTextMessage: { text: "hi there" } } }),
    });
    expect(line.text).toBe("hi there");
  });

  it("renders image caption from imageMessage", () => {
    const line = buildInboxLine({
      msg: makeMsg({ message: { imageMessage: { caption: "look at this", mimetype: "image/jpeg" } } }),
    });
    expect(line.text).toBe("look at this");
    expect(line.image).toEqual({ path: "", sha256: "", filename: "image.jpg" });
  });

  it("image is null when there is no image", () => {
    const line = buildInboxLine({ msg: makeMsg() });
    expect(line.image).toBeNull();
  });

  it("timestamp is ISO 8601", () => {
    const line = buildInboxLine({ msg: makeMsg() });
    expect(line.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
  });

  it("message_id comes from key.id", () => {
    const line = buildInboxLine({ msg: makeMsg() });
    expect(line.message_id).toBe("ABCDEF1234567890");
  });
});

describe("shouldProcessMessage", () => {
  it("skips fromMe", () => {
    expect(shouldProcessMessage(makeMsg({ key: { remoteJid: "1@s.whatsapp.net", fromMe: true, id: "x" } }))).toBe(false);
  });
  it("skips status@broadcast", () => {
    expect(shouldProcessMessage(makeMsg({ key: { remoteJid: "status@broadcast", fromMe: false, id: "x" } }))).toBe(false);
  });
  it("skips @g.us (groups)", () => {
    expect(shouldProcessMessage(makeMsg({ key: { remoteJid: "123@g.us", fromMe: false, id: "x" } }))).toBe(false);
  });
  it("accepts @s.whatsapp.net", () => {
    expect(shouldProcessMessage(makeMsg())).toBe(true);
  });
  it("accepts @c.us", () => {
    expect(shouldProcessMessage(makeMsg({ key: { remoteJid: "1@c.us", fromMe: false, id: "x" } }))).toBe(true);
  });
});

describe("fileInboxAppender", () => {
  it("appends a single line with trailing newline (atomic write, fsync)", async () => {
    const inboxPath = path.join(tmp, "inbox.ndjson");
    const appender = fileInboxAppender({ inboxPath });
    const line: InboxLine = {
      message_id: "1",
      from: "6591234567",
      text: "hi",
      image: null,
      timestamp: "2024-01-01T00:00:00.000Z",
    };
    await appender(line);
    await appender({ ...line, message_id: "2", text: "second" });
    const raw = await fs.readFile(inboxPath, "utf8");
    expect(raw.endsWith("\n")).toBe(true);
    const lines = raw.split("\n").filter((l) => l.length > 0);
    expect(lines).toHaveLength(2);
    expect(JSON.parse(lines[0]!)).toEqual(line);
    expect(JSON.parse(lines[1]!)).toMatchObject({ message_id: "2", text: "second" });
  });

  it("creates the parent directory if missing", async () => {
    const inboxPath = path.join(tmp, "nested", "subdir", "inbox.ndjson");
    const appender = fileInboxAppender({ inboxPath });
    await appender({
      message_id: "1",
      from: "1",
      text: "x",
      image: null,
      timestamp: "2024-01-01T00:00:00.000Z",
    });
    const stat = await fs.stat(inboxPath);
    expect(stat.isFile()).toBe(true);
  });
});

describe("integration: socket + inbox appender (mocked)", () => {
  it("emits inbox line on messages.upsert (notify) for an @s.whatsapp.net message", async () => {
    const inboxPath = path.join(tmp, "inbox.ndjson");
    const appender = fileInboxAppender({ inboxPath });
    const { defaultLoadAuth } = await import("../src/auth.js");
    const { createSocket } = await import("../src/socket.js");
    const pino = (await import("pino")).default;

    const authDir = path.join(tmp, "auth");
    const auth = await defaultLoadAuth(authDir);
    const appended: InboxLine[] = [];
    const realAppend = appender;
    const wrapped: typeof appender = async (line) => {
      appended.push(line);
      await realAppend(line);
    };

    const fakeSock = {
      ev: {
        on: vi.fn(),
        removeAllListeners: vi.fn(),
      },
      end: vi.fn(),
    };

    const logger = pino({ level: "silent" });
    const ctrl = createSocket({
      auth,
      logger,
      appendInbox: wrapped,
      // @ts-expect-error -- fake has only the methods this test calls
      socketFactory: () => fakeSock,
    });
    await ctrl.start();

    const onCall = fakeSock.ev.on.mock.calls.find((c) => c[0] === "messages.upsert");
    expect(onCall).toBeTruthy();
    const handler = onCall![1] as (data: { messages: WAMessage[]; type: "notify" }) => Promise<void>;
    await handler({ messages: [makeMsg()], type: "notify" });

    expect(appended).toHaveLength(1);
    expect(appended[0]!.from).toBe("6591234567");
    expect(appended[0]!.text).toBe("hello");

    const raw = await fs.readFile(inboxPath, "utf8");
    expect(raw.split("\n").filter((l) => l.trim().length > 0)).toHaveLength(1);

    await ctrl.stop();
  });
});
