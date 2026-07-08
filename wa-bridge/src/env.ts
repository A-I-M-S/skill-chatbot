import { config as loadDotenv } from "dotenv";
import path from "node:path";
import { z } from "zod";

loadDotenv({ path: path.resolve(process.cwd(), ".env") });

const EnvSchema = z.object({
  WA_BRIDGE_PORT: z.coerce.number().int().positive().default(7788),
  WA_BRIDGE_TOKEN: z.string().min(1, "WA_BRIDGE_TOKEN is required for HTTP auth"),
  WA_AUTH_DIR: z.string().min(1, "WA_AUTH_DIR is required for Baileys multi-file auth state"),
  // Default WhatsApp number for `npm run auth:code` (E.164, e.g. +6591234567).
  // A CLI arg overrides it; optional so the bridge itself runs without it.
  WA_PAIR_NUMBER: z.string().optional(),
  INBOX_PATH: z.string().min(1, "INBOX_PATH is required for the orchestrator NDJSON tail"),
  OUTBOX_PATH: z.string().min(1, "OUTBOX_PATH is required for the outbound queue"),
  LOG_LEVEL: z
    .enum(["trace", "debug", "info", "warn", "error", "fatal", "silent"])
    .default("info"),
  WA_RECONNECT_MAX_BACKOFF_MS: z.coerce.number().int().positive().default(60_000),
  WA_RECONNECT_GIVEUP_QR: z.coerce.number().int().nonnegative().default(4),
  WA_BRIDGE_LOG: z.string().optional(),
  NODE_ENV: z.string().optional(),
  RAG_PHOTOS_DIR: z.string().min(1).default("/root/rag-photos"),
  MAX_IMAGE_BYTES: z.coerce.number().int().positive().default(10 * 1024 * 1024),
});

export type Env = z.infer<typeof EnvSchema>;

let cached: Env | null = null;

export function loadEnv(overrides: Partial<NodeJS.ProcessEnv> = {}): Env {
  if (cached) return cached;
  const merged: NodeJS.ProcessEnv = { ...process.env, ...overrides };
  const parsed = EnvSchema.safeParse(merged);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((i) => `  - ${i.path.join(".") || "(root)"}: ${i.message}`)
      .join("\n");
    throw new Error(`Invalid wa-bridge environment:\n${issues}`);
  }
  cached = parsed.data;
  return cached;
}

export function resetEnvForTests(): void {
  cached = null;
}
