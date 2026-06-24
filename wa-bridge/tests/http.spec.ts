import { describe, it, expect, vi } from "vitest";
import { buildApp } from "../src/http.js";
import type { SocketController, SocketStatus } from "../src/socket.js";
import type { Sender, OutboxDrainResult } from "../src/sender.js";
import { pino } from "pino";

function makeFakeSocket(session: "ok" | "qr_needed" | "connecting" = "ok", last_message_at: string | null = null): SocketController {
  const status: SocketStatus = {
    session,
    last_message_at,
    reconnecting: false,
    attempt: 0,
    qr_needed_count: 0,
  };
  return {
    start: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    getStatus: () => status,
    send: vi.fn(),
    session: () => session,
  };
}

describe("HTTP API", () => {
  const token = "secret-123";
  const logger = pino({ level: "silent" });

  describe("GET /health", () => {
    it("returns 200 {ok:true, session}", async () => {
      const app = buildApp({
        token,
        socket: makeFakeSocket("ok"),
        sender: vi.fn() as unknown as Sender,
        drainOutbox: vi.fn().mockResolvedValue({ drained: 0, failed: 0, remaining: 0 } satisfies OutboxDrainResult),
        logger,
      });
      const server = app.listen(0);
      try {
        const addr = server.address();
        const port = typeof addr === "object" && addr ? addr.port : 0;
        const res = await fetch(`http://127.0.0.1:${port}/health`);
        expect(res.status).toBe(200);
        const body = await res.json();
        expect(body).toEqual({ ok: true, session: "ok" });
      } finally {
        await new Promise<void>((r) => server.close(() => r()));
      }
    });

    it("reports qr_needed when session not open", async () => {
      const app = buildApp({
        token,
        socket: makeFakeSocket("qr_needed"),
        sender: vi.fn() as unknown as Sender,
        drainOutbox: vi.fn().mockResolvedValue({ drained: 0, failed: 0, remaining: 0 } satisfies OutboxDrainResult),
        logger,
      });
      const server = app.listen(0);
      try {
        const addr = server.address();
        const port = typeof addr === "object" && addr ? addr.port : 0;
        const res = await fetch(`http://127.0.0.1:${port}/health`);
        const body = (await res.json()) as { session: string };
        expect(body.session).toBe("qr_needed");
      } finally {
        await new Promise<void>((r) => server.close(() => r()));
      }
    });
  });

  describe("GET /status", () => {
    it("returns session, last_message_at, queued_send", async () => {
      const app = buildApp({
        token,
        socket: makeFakeSocket("ok", "2024-06-01T00:00:00.000Z"),
        sender: vi.fn() as unknown as Sender,
        drainOutbox: vi.fn().mockResolvedValue({ drained: 0, failed: 0, remaining: 3 } satisfies OutboxDrainResult),
        logger,
      });
      const server = app.listen(0);
      try {
        const addr = server.address();
        const port = typeof addr === "object" && addr ? addr.port : 0;
        const res = await fetch(`http://127.0.0.1:${port}/status`);
        expect(res.status).toBe(200);
        const body = await res.json();
        expect(body).toMatchObject({
          session: "ok",
          last_message_at: "2024-06-01T00:00:00.000Z",
          queued_send: 3,
        });
      } finally {
        await new Promise<void>((r) => server.close(() => r()));
      }
    });
  });

  describe("POST /send", () => {
    it("happy path: 200 {message_id}", async () => {
      const sender = vi.fn().mockResolvedValue({ ok: true, message_id: "OUT-XYZ" });
      const app = buildApp({
        token,
        socket: makeFakeSocket(),
        sender: sender as unknown as Sender,
        drainOutbox: vi.fn().mockResolvedValue({ drained: 0, failed: 0, remaining: 0 } satisfies OutboxDrainResult),
        logger,
      });
      const server = app.listen(0);
      try {
        const addr = server.address();
        const port = typeof addr === "object" && addr ? addr.port : 0;
        const res = await fetch(`http://127.0.0.1:${port}/send`, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
          body: JSON.stringify({ to: "6591234567", text: "hello" }),
        });
        expect(res.status).toBe(200);
        const body = await res.json();
        expect(body).toEqual({ message_id: "OUT-XYZ" });
        expect(sender).toHaveBeenCalledWith({ to: "6591234567", text: "hello" });
      } finally {
        await new Promise<void>((r) => server.close(() => r()));
      }
    });

    it("rejects missing bearer token with 401", async () => {
      const sender = vi.fn();
      const app = buildApp({
        token,
        socket: makeFakeSocket(),
        sender: sender as unknown as Sender,
        drainOutbox: vi.fn().mockResolvedValue({ drained: 0, failed: 0, remaining: 0 } satisfies OutboxDrainResult),
        logger,
      });
      const server = app.listen(0);
      try {
        const addr = server.address();
        const port = typeof addr === "object" && addr ? addr.port : 0;
        const res = await fetch(`http://127.0.0.1:${port}/send`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ to: "1", text: "x" }),
        });
        expect(res.status).toBe(401);
        expect(sender).not.toHaveBeenCalled();
      } finally {
        await new Promise<void>((r) => server.close(() => r()));
      }
    });

    it("rejects wrong bearer token with 401", async () => {
      const app = buildApp({
        token,
        socket: makeFakeSocket(),
        sender: vi.fn() as unknown as Sender,
        drainOutbox: vi.fn().mockResolvedValue({ drained: 0, failed: 0, remaining: 0 } satisfies OutboxDrainResult),
        logger,
      });
      const server = app.listen(0);
      try {
        const addr = server.address();
        const port = typeof addr === "object" && addr ? addr.port : 0;
        const res = await fetch(`http://127.0.0.1:${port}/send`, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: "Bearer wrong" },
          body: JSON.stringify({ to: "1", text: "x" }),
        });
        expect(res.status).toBe(401);
      } finally {
        await new Promise<void>((r) => server.close(() => r()));
      }
    });

    it("returns 502 {error:'send_failed', reason} when sender returns failure", async () => {
      const sender = vi.fn().mockResolvedValue({ ok: false, reason: "auth-missing" });
      const app = buildApp({
        token,
        socket: makeFakeSocket(),
        sender: sender as unknown as Sender,
        drainOutbox: vi.fn().mockResolvedValue({ drained: 0, failed: 0, remaining: 0 } satisfies OutboxDrainResult),
        logger,
      });
      const server = app.listen(0);
      try {
        const addr = server.address();
        const port = typeof addr === "object" && addr ? addr.port : 0;
        const res = await fetch(`http://127.0.0.1:${port}/send`, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
          body: JSON.stringify({ to: "1", text: "x" }),
        });
        expect(res.status).toBe(502);
        const body = await res.json();
        expect(body).toEqual({ error: "send_failed", reason: "auth-missing" });
      } finally {
        await new Promise<void>((r) => server.close(() => r()));
      }
    });

    it("returns 400 {error:'bad_request'} on invalid body", async () => {
      const app = buildApp({
        token,
        socket: makeFakeSocket(),
        sender: vi.fn() as unknown as Sender,
        drainOutbox: vi.fn().mockResolvedValue({ drained: 0, failed: 0, remaining: 0 } satisfies OutboxDrainResult),
        logger,
      });
      const server = app.listen(0);
      try {
        const addr = server.address();
        const port = typeof addr === "object" && addr ? addr.port : 0;
        const res = await fetch(`http://127.0.0.1:${port}/send`, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
          body: JSON.stringify({ to: "", text: "x" }),
        });
        expect(res.status).toBe(400);
        const body = (await res.json()) as { error: string };
        expect(body.error).toBe("bad_request");
      } finally {
        await new Promise<void>((r) => server.close(() => r()));
      }
    });
  });
});
