import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import { defaultLoadAuth, type LoadAuth } from "../src/auth.js";

let tmp: string;

beforeEach(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), "wa-auth-"));
});

afterEach(async () => {
  await fs.rm(tmp, { recursive: true, force: true });
});

describe("defaultLoadAuth (uses Baileys useMultiFileAuthState)", () => {
  it("creates the auth dir if missing and returns a bundle", async () => {
    const authDir = path.join(tmp, "auth_info");
    const bundle = await defaultLoadAuth(authDir);
    expect(bundle.state).toBeTruthy();
    expect(bundle.state.creds).toBeTruthy();
    expect(typeof bundle.saveCreds).toBe("function");
    const stat = await fs.stat(authDir);
    expect(stat.isDirectory()).toBe(true);
  });

  it("reuses existing auth dir on second call (no QR needed when creds exist)", async () => {
    const authDir = path.join(tmp, "auth_info");
    const first = await defaultLoadAuth(authDir);
    const fakeMe = { id: "6591234567:1@s.whatsapp.net", name: "Test" } as unknown as NonNullable<typeof first.state.creds.me>;
    first.state.creds.me = fakeMe;
    await first.saveCreds();
    const second = await defaultLoadAuth(authDir);
    expect(second.state.creds.me?.id).toBe("6591234567:1@s.whatsapp.net");
  });
});

describe("loadAuth injection (for tests + bin/auth.ts)", () => {
  it("custom loadAuth is honoured by callers", async () => {
    const fakeBundle = {
      state: {
        creds: { me: { id: "fake:0@s.whatsapp.net", name: "Fake" } } as never,
        keys: {} as never,
      },
      saveCreds: vi.fn().mockResolvedValue(undefined),
    };
    const load: LoadAuth = vi.fn().mockResolvedValue(fakeBundle);
    const result = await load("/tmp");
    expect(load).toHaveBeenCalled();
    expect(result.state.creds.me?.id).toBe("fake:0@s.whatsapp.net");
    expect(typeof result.saveCreds).toBe("function");
  });
});
