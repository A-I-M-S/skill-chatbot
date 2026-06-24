import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";
import type { Logger } from "pino";
import type { WAMessage } from "@whiskeysockets/baileys";
import { downloadMediaMessage } from "@whiskeysockets/baileys";

export const DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024;

export type ImageMeta = {
  path: string;
  sha256: string;
  filename: string;
};

export type DownloadImageResult =
  | { ok: true; meta: ImageMeta }
  | { ok: false; reason: "oversize" | "missing_mimetype" | "download_failed" | "write_failed" };

export type DownloaderLogger = Logger | {
  warn: (obj: object, msg: string) => void;
  info?: (obj: object, msg: string) => void;
};

export type DownloadImageOptions = {
  photosDir: string;
  maxBytes?: number | undefined;
  logger?: DownloaderLogger | undefined;
};

export type DownloadFromMessageOptions = DownloadImageOptions & {
  msg: WAMessage;
  downloader?: ((msg: WAMessage) => Promise<Buffer | undefined>) | undefined;
};

const SUPPORTED_EXTS: Record<string, string> = {
  "image/jpeg": "jpg",
  "image/jpg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
  "image/gif": "gif",
  "image/bmp": "bmp",
  "image/tiff": "tiff",
  "image/heic": "heic",
  "image/heif": "heif",
};

export function extForMimetype(mimetype: string | undefined | null): string | null {
  if (!mimetype) return null;
  const mt = mimetype.toLowerCase().trim();
  return SUPPORTED_EXTS[mt] ?? null;
}

export function filenameForMessage(mimetype: string | undefined | null): string {
  const ext = extForMimetype(mimetype) ?? "img";
  return `inbound.${ext}`;
}

export function inferDeclaredSize(msg: WAMessage): number | null {
  const m = msg.message?.imageMessage;
  if (!m) return null;
  const v = m.fileLength;
  if (typeof v === "number" && Number.isFinite(v) && v >= 0) {
    return v;
  }
  if (typeof v === "object" && v && typeof (v as { low?: unknown }).low === "number") {
    return (v as { low: number }).low;
  }
  return null;
}

export async function sha256OfBytes(bytes: Buffer): Promise<string> {
  return createHash("sha256").update(bytes).digest("hex");
}

export async function ensurePhotosDir(photosDir: string): Promise<void> {
  const inboundDir = path.join(photosDir, "inbound");
  await fs.mkdir(inboundDir, { recursive: true });
}

async function defaultDownloader(msg: WAMessage): Promise<Buffer | undefined> {
  try {
    const buf = await downloadMediaMessage(msg, "buffer", {});
    return buf as Buffer | undefined;
  } catch (e) {
    const reason = e instanceof Error ? e.message : String(e);
    throw new Error(`downloadMediaMessage failed: ${reason}`);
  }
}

export async function downloadImageFromMessage(
  opts: DownloadFromMessageOptions
): Promise<DownloadImageResult> {
  const m = opts.msg.message?.imageMessage;
  if (!m) return { ok: false, reason: "missing_mimetype" };
  const declared = inferDeclaredSize(opts.msg);
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_IMAGE_BYTES;
  if (declared !== null && declared > maxBytes) {
    opts.logger?.warn({ declared, maxBytes }, "image rejected: declared size over limit");
    return { ok: false, reason: "oversize" };
  }
  const dl = opts.downloader ?? defaultDownloader;
  let bytes: Buffer | undefined;
  try {
    bytes = await dl(opts.msg);
  } catch (e) {
    opts.logger?.warn({ err: String(e) }, "image download failed");
    return { ok: false, reason: "download_failed" };
  }
  if (!bytes || bytes.length === 0) {
    return { ok: false, reason: "download_failed" };
  }
  if (bytes.length > maxBytes) {
    opts.logger?.warn({ bytes: bytes.length, maxBytes }, "image rejected: bytes over limit");
    return { ok: false, reason: "oversize" };
  }
  const sha256 = await sha256OfBytes(bytes);
  const short = sha256.slice(0, 16);
  const ext = extForMimetype(m.mimetype) ?? "img";
  const inboundDir = path.join(opts.photosDir, "inbound");
  await ensurePhotosDir(opts.photosDir);
  const finalPath = path.join(inboundDir, `${short}.${ext}`);
  try {
    await fs.writeFile(finalPath, bytes);
    await fs.stat(finalPath);
  } catch (e) {
    opts.logger?.warn({ err: String(e), finalPath }, "image write failed");
    return { ok: false, reason: "write_failed" };
  }
  opts.logger?.info?.({ path: finalPath, sha256, bytes: bytes.length }, "image saved");
  return {
    ok: true,
    meta: {
      path: finalPath,
      sha256,
      filename: filenameForMessage(m.mimetype),
    },
  };
}

export const _testing = { SUPPORTED_EXTS, defaultDownloader };
