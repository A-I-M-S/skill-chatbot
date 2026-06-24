import express, { type Express, type Request, type Response, type NextFunction } from "express";
import type { Logger } from "pino";
import { z } from "zod";
import { SendRequestSchema, type Sender, type OutboxDrainResult } from "./sender.js";
import type { SocketController } from "./socket.js";

export type HttpDeps = {
  token: string;
  socket: SocketController;
  sender: Sender;
  drainOutbox: () => Promise<OutboxDrainResult>;
  logger: Logger;
};

export type ServerHandle = {
  app: Express;
  close: () => Promise<void>;
  listen: (port: number) => Promise<{ port: number; close: () => Promise<void> }>;
};

const BearerSendRequest = SendRequestSchema;

function bearerAuth(deps: HttpDeps) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const header = req.header("authorization") ?? "";
    const match = /^Bearer\s+(.+)$/i.exec(header);
    if (!match || match[1] !== deps.token) {
      deps.logger.warn({ path: req.path }, "bearer auth rejected");
      res.status(401).json({ error: "unauthorized" });
      return;
    }
    next();
  };
}

export function buildApp(deps: HttpDeps): Express {
  const app = express();
  app.use(express.json({ limit: "64kb" }));

  app.get("/health", (_req, res) => {
    const session = deps.socket.session();
    res.status(200).json({ ok: true, session });
  });

  app.get("/status", async (_req, res) => {
    const status = deps.socket.getStatus();
    let queued_send = 0;
    try {
      const drain = await deps.drainOutbox();
      queued_send = drain.remaining + drain.failed;
    } catch (e) {
      deps.logger.warn({ err: String(e) }, "drainOutbox failed during /status");
    }
    res.status(200).json({
      session: status.session,
      last_message_at: status.last_message_at,
      queued_send,
      reconnecting: status.reconnecting,
      attempt: status.attempt,
    });
  });

  app.post(
    "/send",
    bearerAuth(deps),
    async (req, res) => {
      const parsed = BearerSendRequest.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: "bad_request", reason: parsed.error.message });
        return;
      }
      try {
        const result = await deps.sender(parsed.data);
        if (result.ok) {
          res.status(200).json({ message_id: result.message_id });
          return;
        }
        res.status(502).json({ error: "send_failed", reason: result.reason });
      } catch (e) {
        const reason = e instanceof Error ? e.message : String(e);
        deps.logger.error({ err: reason }, "send threw");
        res.status(502).json({ error: "send_failed", reason });
      }
    }
  );

  app.use((req, res) => {
    res.status(404).json({ error: "not_found", path: req.path });
  });

  app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
    deps.logger.error({ err: err.message }, "http unhandled error");
    if (!res.headersSent) {
      res.status(500).json({ error: "internal_error" });
    }
  });

  return app;
}

export function startServer(deps: HttpDeps, port: number): Promise<{ server: import("http").Server; close: () => Promise<void> }> {
  const app = buildApp(deps);
  return new Promise((resolve, reject) => {
    const server = app.listen(port, () => {
      const addr = server.address();
      const resolvedPort = typeof addr === "object" && addr ? addr.port : port;
      deps.logger.info({ port: resolvedPort }, "http listening");
      resolve({
        server,
        close: () =>
          new Promise<void>((res, rej) => {
            server.close((e) => (e ? rej(e) : res()));
          }),
      });
    });
    server.on("error", reject);
  });
}

export const _schemas = { BearerSendRequest };
export { z };
