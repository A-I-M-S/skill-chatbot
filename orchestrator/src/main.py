"""Orchestrator v0 — NDJSON tail + RAG echo + reply loop.

Boot sequence:

1. Load :class:`Settings` (env + ``.env``).
2. Open the SQLite state (WAL + flock). Fail fast if another orchestrator
   already holds the lock (plan risk #6).
3. Start the HTTP server (``/health``) on ``ORCHESTRATOR_PORT``.
4. Tail ``INBOX_PATH`` (NDJSON, contract v1 — see note below) line by line.
5. For each new line: skip if ``message_id`` already processed, otherwise call
   :func:`src.rag.ask`, POST the reply to ``WA_BRIDGE_URL/send`` with Bearer
   auth, mark the message_id processed.

NDJSON contract v1 (from wa-bridge issue #2; no upstream doc yet — pinned here):

    {"message_id": "...", "from": "<digits-only-phone>", "text": "...",
     "image": null | {...}, "timestamp": "..."}

This is intentionally bare — no LLM router, no flows, no booking (issues
#4-#8). The only goal of v0 is to prove the loop closes end-to-end.
"""

from __future__ import annotations

import logging
import logging.config
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pythonjsonlogger.json import JsonFormatter

from . import http_server as http_srv
from . import rag
from .settings import Settings
from .state import State, StateLockedError
from .tail import Tailer

NDJSON_CONTRACT_VERSION = "v1"
REQUIRED_NDJSON_FIELDS = ("message_id", "from", "text")

logger = logging.getLogger("orchestrator")


def configure_logging(settings: Settings) -> None:
    """JSON logging to stdout, optionally also to ``ORCHESTRATOR_LOG``."""
    handlers: dict[str, dict[str, Any]] = {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": sys.stdout,
            "formatter": "json",
        },
    }
    if settings.orchestrator_log is not None:
        settings.orchestrator_log.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.WatchedFileHandler",
            "filename": str(settings.orchestrator_log),
            "formatter": "json",
        }
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": handlers,
            "root": {"level": settings.log_level, "handlers": list(handlers)},
        }
    )


def post_reply(
    client: httpx.Client, bridge_url: str, token: str, message_id: str, reply: str
) -> None:
    """POST the orchestrator's reply to ``<bridge_url>/send``.

    Caller is expected to catch ``httpx.HTTPError`` and decide retry policy.
    """
    resp = client.post(
        f"{bridge_url.rstrip('/')}/send",
        headers={"Authorization": f"Bearer {token}"},
        json={"message_id": message_id, "text": reply},
        timeout=10.0,
    )
    resp.raise_for_status()


def handle_message(
    state: State,
    client: httpx.Client,
    settings: Settings,
    message_id: str,
    sender: str,
    text: str,
) -> None:
    """End-to-end: RAG ask -> POST reply -> mark processed.

    Idempotent via ``state.is_processed``.
    """
    if state.is_processed(message_id):
        logger.info("skip already-processed message_id=%s", message_id)
        return
    logger.info("processing message_id=%s sender=%s", message_id, sender)
    reply = rag.ask(text or "")
    post_reply(client, str(settings.wa_bridge_url), settings.wa_bridge_token, message_id, reply)
    state.mark_processed(message_id)
    logger.info("reply sent message_id=%s", message_id)


def offset_file_for(state_db: Path) -> Path:
    return state_db.with_suffix(state_db.suffix + ".offset")


def run_loop(
    settings: Settings,
    state: State,
    tailer: Tailer,
    stop: threading.Event,
    *,
    client_factory: Callable[[], httpx.Client] = lambda: httpx.Client(),
    poll_interval: float = 0.5,
    idle_log_interval: float = 30.0,
) -> None:
    """The tail loop body — reusable so tests can drive it with their own
    stop event (no signal handlers)."""
    last_poll_log = 0.0
    client = client_factory()
    try:
        while not stop.is_set():
            advanced = False
            for msg in tailer.iter_lines():
                advanced = True
                handle_message(
                    state,
                    client,
                    settings,
                    message_id=msg.message_id,
                    sender=msg.sender,
                    text=msg.text,
                )
                if stop.is_set():
                    break
            tailer.update_offset()
            if not advanced:
                now = time.monotonic()
                if now - last_poll_log > idle_log_interval:
                    last_poll_log = now
                    logger.debug("tail idle (last_processed=%s)", state.last_processed_message_id())
                stop.wait(timeout=poll_interval)
    finally:
        client.close()


def run_forever_until_stopped(
    settings: Settings,
    stop: threading.Event | None = None,
) -> int:
    """Boot + tail loop. Returns the process exit code.

    When called from ``main()`` (i.e. as a CLI), ``stop`` is None and SIGINT/
    SIGTERM handlers flip an internal event. When called from tests, callers
    pass their own ``stop`` event.
    """
    configure_logging(settings)
    logger.info(
        "orchestrator booting inbox=%s db=%s bridge=%s port=%d contract=%s",
        settings.inbox_path,
        settings.orchestrator_db,
        settings.wa_bridge_url,
        settings.orchestrator_port,
        NDJSON_CONTRACT_VERSION,
    )

    try:
        state = State(settings.orchestrator_db)
    except StateLockedError as exc:
        logger.error("cannot start: %s", exc)
        return 2

    server = http_srv.start_server(state, host="0.0.0.0", port=settings.orchestrator_port)

    own_stop = stop is None
    stop_evt = stop if stop is not None else threading.Event()

    if own_stop:

        def _on_signal(signum: int, _frame: Any) -> None:
            logger.info("received signal %d, shutting down", signum)
            stop_evt.set()

        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

    tailer = Tailer(settings.inbox_path, offset_file_for(settings.orchestrator_db))
    try:
        run_loop(settings, state, tailer, stop_evt)
    finally:
        http_srv.stop_server(server)
        state.close()
    logger.info("orchestrator stopped cleanly")
    return 0


def run_forever(settings: Settings) -> int:
    """CLI entry: install signal handlers and run."""
    return run_forever_until_stopped(settings)


def main(argv: list[str] | None = None) -> int:
    """Entry point: load settings (from ``.env``) and run forever."""
    try:
        settings = Settings.from_env()
    except KeyError as exc:
        sys.stderr.write(f"orchestrator: missing required env var: {exc.args[0]}\n")
        return 2
    except ValueError as exc:
        sys.stderr.write(f"orchestrator: invalid env: {exc}\n")
        return 2
    return run_forever(settings)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
