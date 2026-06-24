#!/usr/bin/env -S npx tsx
import { loadEnv } from "../src/env.js";
import { buildLogger } from "../src/log.js";
import { defaultLoadAuth } from "../src/auth.js";
import { createSocket } from "../src/socket.js";

async function main(): Promise<number> {
  const env = loadEnv();
  const logger = buildLogger().child({ mod: "auth" });
  const auth = await defaultLoadAuth(env.WA_AUTH_DIR);

  let lastQr: string | null = null;
  let resolved = false;

  const socket = createSocket({
    auth,
    logger,
    appendInbox: async () => {
      throw new Error("auth CLI does not append to inbox");
    },
    maxBackoffMs: env.WA_RECONNECT_MAX_BACKOFF_MS,
    giveupQrCount: env.WA_RECONNECT_GIVEUP_QR,
    printQR: (qr) => {
      lastQr = qr;
      process.stdout.write(`\n[wa-bridge/auth] scan this QR with WhatsApp > Linked Devices:\n${qr}\n`);
    },
  });

  socket.getStatus;
  await socket.start();

  const startedAt = Date.now();
  const timeoutMs = 5 * 60 * 1000;

  await new Promise<void>((resolve) => {
    const interval = setInterval(() => {
      const status = socket.getStatus();
      if (status.session === "ok") {
        resolved = true;
        clearInterval(interval);
        process.stdout.write("[wa-bridge/auth] authenticated\n");
        resolve();
        return;
      }
      if (Date.now() - startedAt > timeoutMs) {
        clearInterval(interval);
        process.stdout.write("[wa-bridge/auth] timeout waiting for sync\n");
        resolve();
        return;
      }
      if (lastQr === null && status.attempt > 0) {
        process.stdout.write("[wa-bridge/auth] waiting for QR…\n");
      }
    }, 1000);
  });

  await socket.stop();
  await new Promise((r) => setTimeout(r, 200));
  void logger;
  if (resolved) {
    process.stdout.write("[wa-bridge/auth] creds saved to " + env.WA_AUTH_DIR + "\n");
    return 0;
  }
  process.stdout.write("[wa-bridge/auth] did not sync within timeout\n");
  return 1;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    process.stderr.write(`auth error: ${String(e)}\n`);
    process.exit(1);
  }
);
