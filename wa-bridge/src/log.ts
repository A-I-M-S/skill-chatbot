import { pino, type Logger } from "pino";
import { loadEnv } from "./env.js";

let cached: Logger | null = null;

export function buildLogger(): Logger {
  if (cached) return cached;
  const env = loadEnv();
  cached = pino({
    level: env.LOG_LEVEL,
    base: { service: "wa-bridge" },
    timestamp: pino.stdTimeFunctions.isoTime,
  });
  return cached;
}

export function resetLoggerForTests(): void {
  cached = null;
}
