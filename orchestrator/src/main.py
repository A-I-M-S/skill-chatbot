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

Image branch (issue #10): when ``image`` is non-null, ``handle_message``
saves the metadata to :class:`src.state.State` and acks
(``Got the photo.`` / ``收到图片了。``). If the caption looks like a
question, the photo context is prepended to the user message and the
router/RAG is called with :func:`src.rag.ask_with_photo`.
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
from . import image_handler, rag
from .router import RouterDecision, route_message
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
    image: dict[str, str] | None = None,
    language: str | None = None,
) -> None:
    """End-to-end: image ack (if any) -> router -> dispatch -> POST reply -> mark processed.

    Idempotent via ``state.is_processed``.

    Routing (issue #4): each inbound message goes through the LLM router
    to pick one of the five tools (faq / book_new / book_edit / book_cancel
    / handoff). For v1 we dispatch directly here; the booking-flow state
    machines (#5 / #6 / #7) will hook into the same dispatcher.
    """
    if state.is_processed(message_id):
        logger.info("skip already-processed message_id=%s", message_id)
        return
    logger.info(
        "processing message_id=%s sender=%s has_image=%s language=%s",
        message_id,
        sender,
        bool(image),
        language or "?",
    )

    # Image ack — separate from the LLM router. The router may still
    # be called for the caption (when the image is image+caption and
    # the caption looks like a question).
    if image is not None:
        image_decision = image_handler.process_inbound_image(
            state=state,
            sender=sender,
            message_id=message_id,
            image=image,
            caption=text,
            language=language,
        )
        post_reply(
            client,
            str(settings.wa_bridge_url),
            settings.wa_bridge_token,
            message_id,
            image_decision["ack"],
        )
        if not image_decision["routed"]:
            state.mark_processed(message_id)
            return

    # Router
    decision = route_message(
        user_text=text or "",
        image=image if image and (text or "").strip() else None,
        language=language or "en",
    )
    logger.info(
        "router decision message_id=%s tool=%s fallback=%s",
        message_id,
        decision.tool,
        decision.fallback,
    )

    reply_text = _dispatch_decision(state, client, settings, decision, sender, message_id, language, text)
    post_reply(
        client,
        str(settings.wa_bridge_url),
        settings.wa_bridge_token,
        message_id,
        reply_text,
    )
    state.mark_processed(message_id)
    logger.info("reply sent message_id=%s tool=%s", message_id, decision.tool)


def _dispatch_decision(
    state: State,
    client: httpx.Client,
    settings: Settings,
    decision: RouterDecision,
    sender: str,
    message_id: str,
    language: str | None,
    user_text: str = "",
) -> str:
    """Dispatch a router decision to the right flow handler.

    v1 handles ``faq`` and ``handoff`` directly. ``book_new`` / ``book_edit``
    / ``book_cancel`` return a placeholder ("booking flow under construction")
    — those will be implemented in #5 / #6 / #7 in the next batch.
    """
    from . import notify  # local import to avoid cycle

    lang = language or decision.language or "en"

    if decision.tool == "faq":
        question = decision.arguments.get("question", "")
        return rag.ask(question)

    if decision.tool == "handoff":
        reason = decision.arguments.get("reason", "other")
        summary = decision.arguments.get("summary", "")
        notify.notify_handoff(sender, reason, summary, decision.fallback)
        admin_contact = str(settings.admin_contact_number or "")
        if reason == "abuse":
            return i18n_t("abusive_msg", lang)
        return i18n_t("handoff_msg", lang, admin_contact=admin_contact)

    # Booking tools
    if decision.tool == "book_new":
        from .flows import booking_new

        # If we're mid-flow in awaiting_confirm, the user's reply is the
        # user_text (router still emits tool=book_new with no args, or the
        # user just types "yes"). We treat any reply while in awaiting_confirm
        # as a confirm reply.
        ps = state.get_phone_state(sender)
        confirm = None
        if ps and ps.get("flow") == "book_new" and ps.get("pending_confirm"):
            confirm = user_text
        return booking_new.handle(
            phone=sender,
            user_text=user_text,
            tool_args=decision.arguments,
            state=state,
            language=lang,
            confirm_reply=confirm,
        )

    if decision.tool == "book_edit":
        from .flows import booking_edit

        ps = state.get_phone_state(sender)
        confirm = None
        if ps and ps.get("flow") == "book_edit" and ps.get("pending_confirm"):
            confirm = user_text
        return booking_edit.handle_edit(
            phone=sender,
            user_text=user_text,
            tool_args=decision.arguments,
            state=state,
            language=lang,
            confirm_reply=confirm,
        )

    if decision.tool == "book_cancel":
        from .flows import booking_edit

        ps = state.get_phone_state(sender)
        confirm = None
        if ps and ps.get("flow") == "book_cancel" and ps.get("pending_confirm"):
            confirm = user_text
        return booking_edit.handle_cancel(
            phone=sender,
            user_text=user_text,
            tool_args=decision.arguments,
            state=state,
            language=lang,
            confirm_reply=confirm,
        )

    # Unknown tool — should never happen because the router validates, but
    # defensively log and ack.
    logger.error("unknown router tool %r (message_id=%s)", decision.tool, message_id)
    return "Sorry, something went wrong. Please try again."


def i18n_t(key: str, language: str, **kwargs: Any) -> str:
    """Local re-export of :func:`src.i18n.t` to keep imports lean."""
    from .i18n import t as _t

    return _t(key, language, **kwargs)


_IMAGE_ACK_EN = "Got the photo."
_IMAGE_ACK_ZH = "收到图片了。"


def _image_ack(language: str) -> str:
    """Localised image ack. v0 ships EN + 中文; default is EN."""
    if language.lower().startswith("zh"):
        return _IMAGE_ACK_ZH
    return _IMAGE_ACK_EN


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
                    image=msg.image,
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
