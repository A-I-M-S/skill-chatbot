#!/usr/bin/env -S npx tsx
/**
 * WhatsApp pairing-code authentication for wa-bridge.
 *
 * - Never prints a QR code. Wires NO QR callbacks and uses
 *   `printQRInTerminal: false`.
 * - On every fresh `connection.update` with a `qr` payload, immediately
 *   calls `sock.requestPairingCode(phoneNumber)` and prints the 8-char
 *   Crockford code.
 * - On `connection === "open"` (user has approved on phone) saves creds,
 *   stops the socket, and exits 0.
 * - When the server drops the socket (status 428, common after ~3.5 min
 *   when no approval lands), tears down and creates a fresh socket so a
 *   new pairing code is issued. Repeats until approved or the global
 *   timeout elapses.
 * - Hard 5-minute total timeout — exits 1 if no auth happens by then.
 *
 * Baileys socket is configured like the skill-whatsapp reference:
 * markOnlineOnConnect:false, fireInitQueries:false,
 * shouldSyncHistoryMessage:()=>false. Phone number is normalised to
 * digits-only before being passed to `requestPairingCode`.
 *
 * Usage:
 *   npm run auth:code -- +65XXXXXXXX
 *   tsx bin/auth-code.ts +65XXXXXXXX
 */

import {
  makeWASocket,
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import { loadEnv } from "../src/env.js";
import { buildLogger } from "../src/log.js";
import { defaultLoadAuth } from "../src/auth.js";

const E164 = /^\+[1-9]\d{6,14}$/;
const CODE_COOLDOWN_MS = 5_000;
const TOTAL_TIMEOUT_MS = 10 * 60 * 1000; // 10 min overall

function die(code: number, msg: string): never {
  process.stderr.write(`[wa-bridge/auth-code] ${msg}\n`);
  process.exit(code);
}

function printCode(code: string, phone: string): void {
  process.stdout.write("\n");
  process.stdout.write("============================================================\n");
  process.stdout.write(`  WhatsApp pairing code: ${code}\n`);
  process.stdout.write("============================================================\n");
  process.stdout.write(`  On the phone registered as ${phone}:\n`);
  process.stdout.write("    WhatsApp -> Settings -> Linked Devices -> Link a Device\n");
  process.stdout.write(`    When prompted, enter: ${code}\n`);
  process.stdout.write(`  (Issued at ${new Date().toISOString()};\n`);
  process.stdout.write(`   if the server drops the link, a new code will appear here.)\n`);
  process.stdout.write("============================================================\n\n");
}

async function main(): Promise<number> {
  const phoneArg = process.argv[2];
  if (!phoneArg) die(2, "missing phone number. usage: npm run auth:code -- +65XXXXXXXX");
  if (!E164.test(phoneArg)) {
    die(2, `invalid phone number "${phoneArg}". expected E.164 like +6591234567 (no spaces).`);
  }
  // Baileys requestPairingCode takes digits only, no leading '+'.
  const phoneDigits = phoneArg.replace(/^\+/, "");

  const env = loadEnv();
  const logger = buildLogger().child({ mod: "auth-code" });

  // Fresh auth state for pairing. Baileys writes creds into this dir on
  // creds.update; the running bridge picks them up on next launch.
  const auth = await defaultLoadAuth(env.WA_AUTH_DIR);

  // WhatsApp rejects the hardcoded version array (405 Method Not Allowed
  // on stale fingerprints). Pull the live version from wa.me.
  let version: [number, number, number];
  try {
    const { version: latest } = await fetchLatestBaileysVersion();
    version = latest;
    logger.info({ version }, "fetched latest baileys version");
  } catch (e) {
    logger.warn({ err: String(e) }, "fetchLatestBaileysVersion failed, using 3-tuple fallback");
    version = [2, 3000, 1019707846];
  }

  const startedAt = Date.now();
  let resolved = false;
  let lastCodeAt = 0;
  let attempt = 0;
  // Honor the owner's preference: 2026-07-07 rule — do not auto-rotate a
  // new pairing code if the previous one expires. Set NO_RETRY=1 to run
  // single-shot (one code, one socket, exit on close). Default (NO_RETRY
  // unset) keeps the historical auto-rotate for scripted flows.
  const singleShot = process.env.NO_RETRY === "1";

  while (!resolved) {
    if (Date.now() - startedAt > TOTAL_TIMEOUT_MS) {
      process.stderr.write(
        `[wa-bridge/auth-code] overall timeout (${TOTAL_TIMEOUT_MS / 1000}s) reached; no approval landed.\n`,
      );
      return 1;
    }

    if (singleShot && attempt > 0) {
      process.stderr.write(
        "[wa-bridge/auth-code] NO_RETRY=1 set — refusing to rotate to a new code. Stopping. Run again to retry.\n",
      );
      return 2;
    }

    attempt += 1;
    logger.info({ attempt }, "opening socket");

    const sock = makeWASocket({
      auth: auth.state,
      version,
      // CRITICAL: never print QR to terminal.
      printQRInTerminal: false,
      // skill-whatsapp reference: stay quiet until the user completes
      // pairing. Connecting with `markOnlineOnConnect: true` causes
      // Baileys to emit presence updates that the server treats as a
      // second concurrent device handshake and races against the
      // pairing approval.
      markOnlineOnConnect: false,
      // skill-whatsapp reference: do not fire the init query burst on
      // connect. Same rationale — we are not a real session yet, so
      // any IQ we send before pairing completes can confuse the
      // server-side state machine and trigger the 428 Connection
      // Terminated response before the user has typed the code.
      fireInitQueries: false,
      // skill-whatsapp reference: ignore history-sync notifications.
      shouldSyncHistoryMessage: () => false,
      syncFullHistory: false,
      emitOwnEvents: false,
      // Baileys' default qrTimeout (60s first, 20s after) fires a watchdog
      // "QR refs attempts ended" before a human can approve a pairing code.
      qrTimeout: 9 * 60 * 1000,
      keepAliveIntervalMs: 30_000,
      // The per-socket logger is set from buildLogger() in main(); env
      // LOG_LEVEL=trace will dump raw IQ XML in both directions.
    });

    const closedPromise = new Promise<{
      statusCode: number | undefined;
      loggedOut: boolean;
      message: string;
    }>((resolveClose) => {
      sock.ev.on("connection.update", async (u) => {
        if (u.qr) {
          const now = Date.now();
          if (now - lastCodeAt < CODE_COOLDOWN_MS) {
            logger.debug("qr event within cooldown, skipping new code request");
            return;
          }
          lastCodeAt = now;
          logger.info(
            { qrLen: u.qr.length },
            "qr event received (discarded), requesting pairing code",
          );
          try {
            const code = await sock.requestPairingCode(phoneDigits);
            printCode(code, phoneArg);
          } catch (e) {
            logger.error({ err: String(e) }, "requestPairingCode failed");
            process.stderr.write(`[wa-bridge/auth-code] requestPairingCode failed: ${String(e)}\n`);
            resolveClose({ statusCode: undefined, loggedOut: false, message: String(e) });
          }
        }

        if (u.connection === "open") {
          resolved = true;
          logger.info("connection open, paired");
          process.stdout.write(
            "[wa-bridge/auth-code] paired successfully. creds in " + env.WA_AUTH_DIR + "\n",
          );
          // Allow creds.update to flush before teardown.
          setTimeout(() => {
            try {
              sock.end(undefined);
            } catch {
              /* ignore */
            }
            resolveClose({ statusCode: undefined, loggedOut: false, message: "open" });
          }, 500);
        }

        if (u.connection === "close") {
          const err = u.lastDisconnect?.error as
            | (Error & { output?: { statusCode?: number } })
            | undefined;
          const statusCode = err?.output?.statusCode;
          const loggedOut = statusCode === DisconnectReason.loggedOut;
          logger.warn(
            { statusCode, loggedOut, err: err?.message },
            "connection closed",
          );
          resolveClose({ statusCode, loggedOut, message: err?.message ?? "closed" });
        }
      });
    });

    sock.ev.on("creds.update", async () => {
      try {
        await auth.saveCreds();
      } catch (e) {
        logger.error({ err: String(e) }, "saveCreds failed");
      }
    });

    const closeInfo = await closedPromise;

    // Try to end gracefully; ignore errors if already closed.
    try {
      sock.end(undefined);
    } catch {
      /* ignore */
    }

    if (resolved) return 0;

    if (closeInfo.loggedOut) {
      process.stderr.write(
        "[wa-bridge/auth-code] logged out from server. restart this command to retry.\n",
      );
      return 1;
    }

    logger.info({ attempt, reason: closeInfo.message }, "socket ended, will retry with fresh socket");
    // Brief backoff so we don't hammer the server.
    await new Promise((r) => setTimeout(r, 1_500));
  }

  return 0;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    process.stderr.write(`auth-code error: ${String(e)}\n`);
    process.exit(1);
  },
);