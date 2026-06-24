import { useMultiFileAuthState, type AuthenticationState } from "@whiskeysockets/baileys";
import path from "node:path";
import fs from "node:fs/promises";

export type AuthBundle = {
  state: AuthenticationState;
  saveCreds: () => Promise<void>;
};

export type LoadAuth = (authDir: string) => Promise<AuthBundle>;

export const defaultLoadAuth: LoadAuth = async (authDir: string) => {
  await fs.mkdir(authDir, { recursive: true });
  return useMultiFileAuthState(authDir);
};

export function authDirFor(bridgeRoot: string): string {
  return path.resolve(bridgeRoot, "auth_info");
}
