#!/usr/bin/env -S npx tsx
/**
 * WhatsApp pairing-code authentication for wa-bridge.
 *
 * The single, canonical way to (re)link the WhatsApp session. No QR.
 *
 * Flow:
 *  - Load the multi-file auth state. If it is already registered, connect
 *    once to confirm the session is healthy and exit 0 — we never request
 *    a new code against a working session.
 *  - Otherwise open ONE socket (quiet config: markOnlineOnConnect:false,
 *    fireInitQueries:false, no history sync, long qrTimeout) and request
 *    exactly ONE pairing code, printed prominently. The code is NOT rotated
 *    while you type it — a code that silently rotated out from under the
 *    user was the historical reason pairing "kept not connecting".
 *  - On `connection === "open"` the phone approved: creds are saved, exit 0.
 *  - WhatsApp emits a benign stream-error 515 ("restart required") right
 *    after the code is accepted; we reconnect once (creds are now
 *    registered) to finish login — without requesting a new code.
 *  - If the window closes before approval, exit non-zero with a clear
 *    "run it again" message instead of silently issuing a new code.
 *
 * The number comes from argv, else WA_PAIR_NUMBER in the env, so the
 * routine can be a single no-arg command on a configured box.
 *
 * Usage:
 *   npm run auth:code -- +65XXXXXXXX      # explicit number
 *   npm run auth:code                     # uses WA_PAIR_NUMBER from env
 *   tsx bin/auth-code.ts +65XXXXXXXX
 */

import {
  makeWASocket,
  DisconnectReason,
  fetchLatestBaileysVersion,
} from "@whiskeysockets/baileys";
import { loadEnv } from "../src/env.js";
import { buildLogger } from "../src/log.js";
import { defaultLoadAuth } from "../src/auth.js";

const E164 = /^\+[1-9]\d{6,14}$/;
// Give the human the full pairing window on one code. Baileys' default
// qrTimeout (60s first, 20s after) fires a watchdog "QR refs attempts
// ended" before a person can type an 8-char code, so we widen it.
const QR_TIMEOUT_MS = 9 * 60 * 1000;
// Delay before requesting the code so the websocket has come up.
const CODE_REQUEST_DELAY_MS = 3_000;
// Hard ceiling on the whole run (covers the one 515 restart reconnect).
const TOTAL_TIMEOUT_MS = 10 * 60 * 1000;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

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
  process.stdout.write("    -> tap \"Link with phone number instead\"\n");
  process.stdout.write(`    -> enter: ${code}\n`);
  process.stdout.write(`  (Issued ${new Date().toISOString()}. This code is NOT rotated —\n`);
  process.stdout.write("   enter it promptly. If it lapses, just run the command again.)\n");
  process.stdout.write("============================================================\n\n");
}

type Outcome = { done: true; code: number } | { reconnect: true };

async function main(): Promise<number> {
  const env = loadEnv();
  const logger = buildLogger().child({ mod: "auth-code" });

  const phoneArg = process.argv[2] ?? env.WA_PAIR_NUMBER ?? "";
  if (!phoneArg) {
    die(
      2,
      "no phone number. pass one (npm run auth:code -- +65XXXXXXXX) or set WA_PAIR_NUMBER in the env.",
    );
  }
  if (!E164.test(phoneArg)) {
    die(2, `invalid phone number "${phoneArg}". expected E.164 like +6591234567 (no spaces).`);
  }
  // Baileys requestPairingCode takes digits only, no leading '+'.
  const phoneDigits = phoneArg.replace(/^\+/, "");

  // Baileys writes creds into this dir on creds.update; the running bridge
  // picks them up on next launch.
  const auth = await defaultLoadAuth(env.WA_AUTH_DIR);

  if (auth.state.creds?.registered) {
    logger.info("auth state already registered — verifying the session instead of pairing");
    process.stdout.write(
      "[wa-bridge/auth-code] this number is already linked; verifying the session (no new code needed)…\n",
    );
  }

  // WhatsApp rejects a stale hardcoded version array (405 Method Not
  // Allowed). Pull the live version; fall back to a recent 3-tuple.
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
  // One code for the whole run. Once issued we never issue another — a
  // rotating code is what broke pairing before.
  let codeIssued = false;

  // The loop only re-iterates for the benign 515 restart (or the verify of
  // an already-registered session). It never re-issues a pairing code.
  for (;;) {
    if (Date.now() - startedAt > TOTAL_TIMEOUT_MS) {
      process.stderr.write(
        `[wa-bridge/auth-code] overall timeout (${TOTAL_TIMEOUT_MS / 1000}s) with no pairing. run the command again.\n`,
      );
      return 1;
    }

    const registered = Boolean(auth.state.creds?.registered);
    logger.info({ registered }, "opening socket");

    const sock = makeWASocket({
      auth: auth.state,
      version,
      // CRITICAL: never print QR — pairing is by code only.
      printQRInTerminal: false,
      // Stay quiet until pairing completes. markOnlineOnConnect / init
      // queries before we are a real session make the server treat us as a
      // second concurrent device and race the pairing approval (428).
      markOnlineOnConnect: false,
      fireInitQueries: false,
      shouldSyncHistoryMessage: () => false,
      syncFullHistory: false,
      emitOwnEvents: false,
      qrTimeout: QR_TIMEOUT_MS,
      keepAliveIntervalMs: 30_000,
    });

    const outcome = await new Promise<Outcome>((resolve) => {
      let settled = false;
      const settle = (o: Outcome) => {
        if (settled) return;
        settled = true;
        resolve(o);
      };

      sock.ev.on("creds.update", async () => {
        try {
          await auth.saveCreds();
        } catch (e) {
          logger.error({ err: String(e) }, "saveCreds failed");
        }
      });

      sock.ev.on("connection.update", (u) => {
        // We deliberately ignore u.qr: pairing is by code, not QR.
        if (u.connection === "open") {
          logger.info("connection open, linked");
          process.stdout.write(
            "[wa-bridge/auth-code] paired successfully. creds in " + env.WA_AUTH_DIR + "\n",
          );
          // Let a trailing creds.update flush before teardown.
          setTimeout(() => {
            try {
              sock.end(undefined);
            } catch {
              /* ignore */
            }
            settle({ done: true, code: 0 });
          }, 500);
          return;
        }

        if (u.connection === "close") {
          const err = u.lastDisconnect?.error as
            | (Error & { output?: { statusCode?: number } })
            | undefined;
          const statusCode = err?.output?.statusCode;
          const loggedOut = statusCode === DisconnectReason.loggedOut;
          const nowRegistered = Boolean(auth.state.creds?.registered);
          logger.warn(
            { statusCode, loggedOut, nowRegistered, err: err?.message },
            "connection closed",
          );
          try {
            sock.end(undefined);
          } catch {
            /* ignore */
          }
          if (loggedOut) {
            settle({ done: true, code: 1 });
            return;
          }
          // Pairing was accepted (creds now registered) and the server
          // asked us to restart (515) — reconnect to finish login. Also
          // covers the verify path for an already-registered session.
          if (nowRegistered) {
            settle({ reconnect: true });
            return;
          }
          // Closed before approval. Do NOT rotate the code — bail out and
          // let the operator re-run with a fresh code.
          settle({ done: true, code: 1 });
        }
      });

      // Request exactly one pairing code, after a short delay so the
      // websocket is up. Only when we still need to register.
      if (!registered && !codeIssued) {
        void (async () => {
          try {
            await sleep(CODE_REQUEST_DELAY_MS);
            if (codeIssued) return;
            const code = await sock.requestPairingCode(phoneDigits);
            codeIssued = true;
            printCode(code, phoneArg);
          } catch (e) {
            logger.error({ err: String(e) }, "requestPairingCode failed");
            process.stderr.write(
              `[wa-bridge/auth-code] requestPairingCode failed: ${String(e)}. run the command again.\n`,
            );
            try {
              sock.end(new Error("requestPairingCode failed"));
            } catch {
              /* ignore */
            }
            settle({ done: true, code: 1 });
          }
        })();
      }
    });

    if ("done" in outcome) {
      if (outcome.code !== 0 && !auth.state.creds?.registered) {
        process.stderr.write(
          "[wa-bridge/auth-code] link not completed. run `npm run auth:code` again and enter the fresh code promptly.\n",
        );
      }
      return outcome.code;
    }

    // reconnect (515 restart / verify): finish login without a new code.
    logger.info("reconnecting to finish login…");
    await sleep(1_500);
  }
}

main().then(
  (code) => process.exit(code),
  (e) => {
    process.stderr.write(`auth-code error: ${String(e)}\n`);
    process.exit(1);
  },
);
