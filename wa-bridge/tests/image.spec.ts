import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import {
  DEFAULT_MAX_IMAGE_BYTES,
  downloadImageFromMessage,
  extForMimetype,
  filenameForMessage,
  inferDeclaredSize,
  sha256OfBytes,
} from "../src/image.js";
import { buildInboxLine, type InboxLine } from "../src/inbox.js";
import type { WAMessage } from "@whiskeysockets/baileys";

let tmp: string;

beforeEach(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "wa-image-"));
});

afterEach(async () => {
  await fs.rm(tmp, { recursive: true, force: true });
});

function makeImageMsg(overrides: Partial<WAMessage> = {}, image: Partial<NonNullable<WAMessage["message"]>["imageMessage"]> = {}): WAMessage {
  const imageMessage = {
    mimetype: "image/jpeg",
    fileLength: 1024,
    ...image,
  };
  return {
    key: {
      remoteJid: "6591234567@s.whatsapp.net",
      fromMe: false,
      id: "IMG-1",
      ...(overrides.key ?? {}),
    },
    messageTimestamp: 1_700_000_000,
    message: {
      imageMessage,
      ...(overrides.message ?? {}),
    },
    ...overrides,
  } as WAMessage;
}

describe("extForMimetype / filenameForMessage", () => {
  it("maps jpeg -> jpg", () => {
    expect(extForMimetype("image/jpeg")).toBe("jpg");
    expect(filenameForMessage("image/jpeg")).toBe("inbound.jpg");
  });
  it("maps png -> png", () => {
    expect(extForMimetype("image/png")).toBe("png");
    expect(filenameForMessage("image/png")).toBe("inbound.png");
  });
  it("maps webp -> webp", () => {
    expect(extForMimetype("image/webp")).toBe("webp");
  });
  it("returns null for unsupported mimetypes", () => {
    expect(extForMimetype("application/pdf")).toBeNull();
  });
  it("returns 'img' for missing mimetype in filenameForMessage", () => {
    expect(filenameForMessage(undefined)).toBe("inbound.img");
  });
});

describe("inferDeclaredSize", () => {
  it("returns the fileLength number", () => {
    const msg = makeImageMsg({}, { mimetype: "image/png", fileLength: 4096 });
    expect(inferDeclaredSize(msg)).toBe(4096);
  });
  it("returns the .low field of a Long-like fileLength", () => {
    const msg = makeImageMsg({}, { mimetype: "image/png", fileLength: { low: 2048 } as never });
    expect(inferDeclaredSize(msg)).toBe(2048);
  });
  it("returns null when no image message", () => {
    const msg = {
      key: { remoteJid: "1@s.whatsapp.net", fromMe: false, id: "x" },
      message: { conversation: "hi" },
      messageTimestamp: 1,
    } as unknown as WAMessage;
    expect(inferDeclaredSize(msg)).toBeNull();
  });
});

describe("sha256OfBytes", () => {
  it("returns the sha256 hex of arbitrary bytes", async () => {
    const buf = Buffer.from("hello world");
    const expected = (await import("node:crypto")).createHash("sha256").update(buf).digest("hex");
    expect(await sha256OfBytes(buf)).toBe(expected);
  });
});

describe("downloadImageFromMessage", () => {
  it("rejects oversize images declared in the WAMessage", async () => {
    const msg = makeImageMsg({}, { mimetype: "image/jpeg", fileLength: DEFAULT_MAX_IMAGE_BYTES + 1 });
    const r = await downloadImageFromMessage({ msg, photosDir: tmp, maxBytes: DEFAULT_MAX_IMAGE_BYTES });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("oversize");
  });

  it("rejects oversize images by actual byte length", async () => {
    const small = Buffer.alloc(64, 1);
    const declared = small.length;
    const huge = Buffer.alloc(DEFAULT_MAX_IMAGE_BYTES + 1, 1);
    const msg = makeImageMsg({}, { mimetype: "image/jpeg", fileLength: declared });
    const r = await downloadImageFromMessage({
      msg,
      photosDir: tmp,
      maxBytes: DEFAULT_MAX_IMAGE_BYTES,
      downloader: async () => huge,
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("oversize");
  });

  it("rejects when downloader throws", async () => {
    const msg = makeImageMsg({}, { mimetype: "image/jpeg", fileLength: 128 });
    const r = await downloadImageFromMessage({
      msg,
      photosDir: tmp,
      maxBytes: DEFAULT_MAX_IMAGE_BYTES,
      downloader: async () => {
        throw new Error("network gone");
      },
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("download_failed");
  });

  it("rejects when downloader returns empty bytes", async () => {
    const msg = makeImageMsg({}, { mimetype: "image/jpeg", fileLength: 128 });
    const r = await downloadImageFromMessage({
      msg,
      photosDir: tmp,
      maxBytes: DEFAULT_MAX_IMAGE_BYTES,
      downloader: async () => Buffer.alloc(0),
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("download_failed");
  });

  it("saves a small jpeg and returns path + sha256", async () => {
    const bytes = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    const msg = makeImageMsg({}, { mimetype: "image/jpeg", fileLength: bytes.length });
    const r = await downloadImageFromMessage({
      msg,
      photosDir: tmp,
      maxBytes: DEFAULT_MAX_IMAGE_BYTES,
      downloader: async () => bytes,
    });
    expect(r.ok).toBe(true);
    if (!r.ok) throw new Error("expected ok");
    const expectedSha = (await import("node:crypto")).createHash("sha256").update(bytes).digest("hex");
    expect(r.meta.sha256).toBe(expectedSha);
    expect(r.meta.path).toBe(path.join(tmp, "inbound", `${expectedSha.slice(0, 16)}.jpg`));
    expect(r.meta.filename).toBe("inbound.jpg");
    const onDisk = await fs.readFile(r.meta.path);
    expect(onDisk.equals(bytes)).toBe(true);
  });

  it("saves a small png with .png extension", async () => {
    const bytes = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 1, 2, 3, 4]);
    const msg = makeImageMsg({}, { mimetype: "image/png", fileLength: bytes.length });
    const r = await downloadImageFromMessage({
      msg,
      photosDir: tmp,
      maxBytes: DEFAULT_MAX_IMAGE_BYTES,
      downloader: async () => bytes,
    });
    expect(r.ok).toBe(true);
    if (!r.ok) throw new Error("expected ok");
    expect(r.meta.path.endsWith(".png")).toBe(true);
    expect(r.meta.filename).toBe("inbound.png");
  });

  it("returns missing_mimetype for non-image WAMessages", async () => {
    const msg = {
      key: { remoteJid: "1@s.whatsapp.net", fromMe: false, id: "x" },
      message: { conversation: "hi" },
      messageTimestamp: 1,
    } as unknown as WAMessage;
    const r = await downloadImageFromMessage({ msg, photosDir: tmp });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("missing_mimetype");
  });

  it("dedupes by content: same bytes writes one file, second call returns same path", async () => {
    const bytes = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 5, 6]);
    const msg1 = makeImageMsg({}, { mimetype: "image/jpeg", fileLength: bytes.length });
    const msg2 = makeImageMsg({ key: { remoteJid: "6599999999@s.whatsapp.net", fromMe: false, id: "z" } }, {
      mimetype: "image/jpeg",
      fileLength: bytes.length,
    });
    const r1 = await downloadImageFromMessage({
      msg: msg1,
      photosDir: tmp,
      maxBytes: DEFAULT_MAX_IMAGE_BYTES,
      downloader: async () => bytes,
    });
    const r2 = await downloadImageFromMessage({
      msg: msg2,
      photosDir: tmp,
      maxBytes: DEFAULT_MAX_IMAGE_BYTES,
      downloader: async () => bytes,
    });
    expect(r1.ok && r2.ok).toBe(true);
    if (!(r1.ok && r2.ok)) throw new Error("expected ok");
    expect(r1.meta.path).toBe(r2.meta.path);
    expect(r1.meta.sha256).toBe(r2.meta.sha256);
    const inboundDir = path.join(tmp, "inbound");
    const files = await fs.readdir(inboundDir);
    expect(files).toHaveLength(1);
  });
});

describe("buildInboxLine (image-shape regression)", () => {
  it("populates image.filename from mimetype, leaves path/sha256 blank pre-download", () => {
    const line = buildInboxLine({
      msg: makeImageMsg({}, { mimetype: "image/png", caption: "look" }),
    });
    expect(line.text).toBe("look");
    expect(line.image).toEqual({ path: "", sha256: "", filename: "inbound.png" });
  });

  it("image is null when no imageMessage", () => {
    const line = buildInboxLine({
      msg: {
        key: { remoteJid: "1@s.whatsapp.net", fromMe: false, id: "x" },
        message: { conversation: "hi" },
        messageTimestamp: 1,
      } as WAMessage,
    });
    expect(line.image).toBeNull();
  });
});

describe("integration: socket wires image downloader into inbox line", () => {
  it("downloads the image, populates line.image.{path,sha256}, appends to inbox", async () => {
    const bytes = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    const msg = makeImageMsg({ key: { remoteJid: "6591234567@s.whatsapp.net", fromMe: false, id: "IMG-77" } }, {
      mimetype: "image/jpeg",
      fileLength: bytes.length,
      caption: "see this",
    });
    const inboxPath = path.join(tmp, "inbox.ndjson");
    const { createSocket } = await import("../src/socket.js");
    const pino = (await import("pino")).default;
    const { defaultLoadAuth } = await import("../src/auth.js");

    const authDir = path.join(tmp, "auth");
    const auth = await defaultLoadAuth(authDir);
    const appended: InboxLine[] = [];
    const appender = async (l: InboxLine): Promise<void> => {
      appended.push(l);
      await fs.appendFile(inboxPath, JSON.stringify(l) + "\n", "utf8");
    };
    const fakeSock = { ev: { on: vi.fn(), removeAllListeners: vi.fn() }, end: vi.fn() };
    const logger = pino({ level: "silent" });
    const imageDownloader = vi.fn(async () => bytes);
    const ctrl = createSocket({
      auth,
      logger,
      appendInbox: appender,
      photosDir: tmp,
      maxImageBytes: DEFAULT_MAX_IMAGE_BYTES,
      imageDownloader,
      socketFactory: (() => fakeSock) as never,
    });
    await ctrl.start();
    const onCall = fakeSock.ev.on.mock.calls.find((c) => c[0] === "messages.upsert");
    expect(onCall).toBeTruthy();
    const handler = onCall![1] as (data: { messages: WAMessage[]; type: "notify" }) => Promise<void>;
    await handler({ messages: [msg], type: "notify" });

    expect(appended).toHaveLength(1);
    const line = appended[0]!;
    expect(line.text).toBe("see this");
    expect(line.image).toBeTruthy();
    expect(line.image!.path).toBe(
      path.join(tmp, "inbound", `${(await sha256OfBytes(bytes)).slice(0, 16)}.jpg`)
    );
    expect(line.image!.sha256).toBe(await sha256OfBytes(bytes));
    expect(line.image!.filename).toBe("inbound.jpg");
    await ctrl.stop();
  });

  it("drops the image from the line when the declared size is over the limit", async () => {
    const msg = makeImageMsg({ key: { remoteJid: "6591234567@s.whatsapp.net", fromMe: false, id: "BIG" } }, {
      mimetype: "image/jpeg",
      fileLength: DEFAULT_MAX_IMAGE_BYTES + 1,
      caption: "huge pic",
    });
    const inboxPath = path.join(tmp, "inbox.ndjson");
    const { createSocket } = await import("../src/socket.js");
    const pino = (await import("pino")).default;
    const { defaultLoadAuth } = await import("../src/auth.js");

    const authDir = path.join(tmp, "auth");
    const auth = await defaultLoadAuth(authDir);
    const appended: InboxLine[] = [];
    const appender = async (l: InboxLine): Promise<void> => {
      appended.push(l);
      await fs.appendFile(inboxPath, JSON.stringify(l) + "\n", "utf8");
    };
    const fakeSock = { ev: { on: vi.fn(), removeAllListeners: vi.fn() }, end: vi.fn() };
    const logger = pino({ level: "silent" });
    const imageDownloader = vi.fn(async () => Buffer.from([1, 2, 3]));
    const onImageRejected = vi.fn();
    const ctrl = createSocket({
      auth,
      logger,
      appendInbox: appender,
      photosDir: tmp,
      maxImageBytes: DEFAULT_MAX_IMAGE_BYTES,
      imageDownloader,
      onImageRejected,
      socketFactory: (() => fakeSock) as never,
    });
    await ctrl.start();
    const onCall = fakeSock.ev.on.mock.calls.find((c) => c[0] === "messages.upsert");
    const handler = onCall![1] as (data: { messages: WAMessage[]; type: "notify" }) => Promise<void>;
    await handler({ messages: [msg], type: "notify" });

    expect(appended).toHaveLength(1);
    expect(appended[0]!.text).toBe("huge pic");
    expect(appended[0]!.image).toBeNull();
    expect(onImageRejected).toHaveBeenCalledWith(msg, "oversize");
    expect(imageDownloader).not.toHaveBeenCalled();
    await ctrl.stop();
  });

  it("drops the image when the actual downloaded bytes exceed the limit", async () => {
    const declared = 100;
    const msg = makeImageMsg({ key: { remoteJid: "6591234567@s.whatsapp.net", fromMe: false, id: "REAL-BIG" } }, {
      mimetype: "image/jpeg",
      fileLength: declared,
      caption: "huge actual",
    });
    const inboxPath = path.join(tmp, "inbox.ndjson");
    const { createSocket } = await import("../src/socket.js");
    const pino = (await import("pino")).default;
    const { defaultLoadAuth } = await import("../src/auth.js");
    const authDir = path.join(tmp, "auth");
    const auth = await defaultLoadAuth(authDir);
    const appended: InboxLine[] = [];
    const appender = async (l: InboxLine): Promise<void> => {
      appended.push(l);
      await fs.appendFile(inboxPath, JSON.stringify(l) + "\n", "utf8");
    };
    const fakeSock = { ev: { on: vi.fn(), removeAllListeners: vi.fn() }, end: vi.fn() };
    const logger = pino({ level: "silent" });
    const imageDownloader = vi.fn(async () => Buffer.alloc(DEFAULT_MAX_IMAGE_BYTES + 1, 1));
    const onImageRejected = vi.fn();
    const ctrl = createSocket({
      auth,
      logger,
      appendInbox: appender,
      photosDir: tmp,
      maxImageBytes: DEFAULT_MAX_IMAGE_BYTES,
      imageDownloader,
      onImageRejected,
      socketFactory: (() => fakeSock) as never,
    });
    await ctrl.start();
    const onCall = fakeSock.ev.on.mock.calls.find((c) => c[0] === "messages.upsert");
    const handler = onCall![1] as (data: { messages: WAMessage[]; type: "notify" }) => Promise<void>;
    await handler({ messages: [msg], type: "notify" });

    expect(appended).toHaveLength(1);
    expect(appended[0]!.text).toBe("huge actual");
    expect(appended[0]!.image).toBeNull();
    expect(onImageRejected).toHaveBeenCalledWith(msg, "oversize");
    await ctrl.stop();
  });
});
