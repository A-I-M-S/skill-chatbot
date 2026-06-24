"""End-to-end image handling (issue #10).

Covered:
- image-only message -> ack-only, no RAG call
- image + question caption -> ack + routed RAG with photo context prepended
- image + non-question caption -> ack only, no RAG call
- oversize images dropped at the bridge side (wa-bridge test in tests/image.spec.ts);
  the orchestrator never sees them. Verified via inbox line with ``image=None``
  on a 0-byte / oversize bridge result.
- last_image is upserted per-sender and queryable via :func:`State.get_last_image`.
- :func:`src.rag.ask_with_photo` delegates to the (monkeypatched) ``rag_qdrant.ask``
  with the photo context prepended (no real Qdrant call).
- detect-language + question heuristic.

Real Qdrant is NEVER hit — tests monkeypatch :mod:`src.rag` so we cover the
loop end-to-end without infra.
"""  # ruff: noqa: RUF001  (Chinese string literals use fullwidth punctuation on purpose)

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from src import image_handler
from src import main as main_mod
from src import rag as rag_mod
from src.state import open_state


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _append(path: Path, line: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


@pytest.fixture
def fake_rag_with_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[tuple[str, str | None, str]]]:
    """Monkeypatch :mod:`src.rag` so the loop doesn't call Qdrant.

    Yields a list of ``(function_name, photo_path_or_None, question)`` tuples
    so tests can assert that ``ask_with_photo`` was called with the right
    arguments (and that ``ask`` was called for the text path).
    """
    calls: list[tuple[str, str | None, str]] = []

    def _ask(question: str) -> str:
        calls.append(("ask", None, question))
        return f"echo: {question}"

    def _ask_with_photo(question: str, photo_path: str | None = None) -> str:
        calls.append(("ask_with_photo", photo_path, question))
        return f"photo: {photo_path} | {question}"

    monkeypatch.setattr(rag_mod, "ask", _ask)
    monkeypatch.setattr(rag_mod, "ask_with_photo", _ask_with_photo)
    yield calls


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests for image_handler
# ──────────────────────────────────────────────────────────────────────────────


def test_is_question_caption_recognises_en_wh() -> None:
    assert image_handler.is_question_caption("What is this?") is True
    assert image_handler.is_question_caption("which tour") is True
    assert image_handler.is_question_caption("how much?") is True


def test_is_question_caption_recognises_zh() -> None:
    assert image_handler.is_question_caption("这是什么？") is True  # noqa: RUF001
    assert image_handler.is_question_caption("多少钱?") is True
    assert image_handler.is_question_caption("可以看看吗") is True


def test_is_question_caption_rejects_non_questions() -> None:
    assert image_handler.is_question_caption("") is False
    assert image_handler.is_question_caption("see this") is False
    assert image_handler.is_question_caption("thanks") is False
    assert image_handler.is_question_caption("hello world") is False


def test_detect_language_hint_picks_zh_for_cjk() -> None:
    assert image_handler.detect_language_hint("") == "en"
    assert image_handler.detect_language_hint("hello") == "en"
    assert image_handler.detect_language_hint("看这个") == "zh"
    assert image_handler.detect_language_hint("price? 看价格") == "zh"
    assert image_handler.detect_language_hint("plain english sentence") == "en"


def test_build_photo_context_line_en_and_zh() -> None:
    en = image_handler.build_photo_context_line(
        "/root/rag-photos/inbound/abc123.jpg", language="en"
    )
    assert en == "I have a photo at /root/rag-photos/inbound/abc123.jpg from this chat."
    zh = image_handler.build_photo_context_line(
        "/root/rag-photos/inbound/abc123.jpg", language="zh"
    )
    assert zh.startswith("我这边有一张图片")


def test_build_image_user_message_prepends_photo_line() -> None:
    img = {"path": "/x/y.jpg", "sha256": "abc", "filename": "inbound.jpg"}
    out = image_handler.build_image_user_message(img, "what is this?", language="en")
    assert out.startswith("I have a photo at /x/y.jpg from this chat.")
    assert out.endswith("what is this?")


def test_build_image_user_message_handles_empty_caption() -> None:
    img = {"path": "/x/y.jpg", "sha256": "abc", "filename": "inbound.jpg"}
    assert image_handler.build_image_user_message(img, "", language="en") == (
        "I have a photo at /x/y.jpg from this chat."
    )


# ──────────────────────────────────────────────────────────────────────────────
# State: last_image upsert
# ──────────────────────────────────────────────────────────────────────────────


def test_state_set_and_get_last_image(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db) as s:
        assert s.get_last_image("6591234567") is None
        s.set_last_image(
            sender="6591234567",
            message_id="IMG-1",
            path="/root/rag-photos/inbound/abc123.jpg",
            sha256="abc123",
            filename="inbound.jpg",
        )
        row = s.get_last_image("6591234567")
        assert row is not None
        assert row["message_id"] == "IMG-1"
        assert row["path"] == "/root/rag-photos/inbound/abc123.jpg"
        assert row["sha256"] == "abc123"
        assert row["filename"] == "inbound.jpg"


def test_state_set_last_image_upserts_per_sender(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db) as s:
        s.set_last_image("6591", "m1", "/p/1.jpg", "h1", "a.jpg")
        s.set_last_image("6591", "m2", "/p/2.jpg", "h2", "b.jpg")
        row = s.get_last_image("6591")
        assert row is not None
        assert row["message_id"] == "m2"
        assert row["path"] == "/p/2.jpg"


def test_state_last_image_is_per_phone(tmp_state_db: Path) -> None:
    with open_state(tmp_state_db) as s:
        s.set_last_image("6591", "m1", "/p/a.jpg", "h1", "a.jpg")
        s.set_last_image("6592", "m2", "/p/b.jpg", "h2", "b.jpg")
        assert s.get_last_image("6591")["path"] == "/p/a.jpg"
        assert s.get_last_image("6592")["path"] == "/p/b.jpg"


# ──────────────────────────────────────────────────────────────────────────────
# InboxMessage.image accessor
# ──────────────────────────────────────────────────────────────────────────────


def test_inbox_message_image_accessor(tmp_inbox: Path, tmp_state_db: Path) -> None:
    _append(
        tmp_inbox,
        {
            "message_id": "m1",
            "from": "6591234567",
            "text": "see this",
            "image": {
                "path": "/root/rag-photos/inbound/abc123.jpg",
                "sha256": "abc123",
                "filename": "inbound.jpg",
            },
        },
    )
    from src.tail import Tailer

    tailer = Tailer(tmp_inbox, tmp_state_db.with_suffix(".offset"))
    msgs = list(tailer.iter_lines())
    assert msgs[0].has_image is True
    assert msgs[0].image == {
        "path": "/root/rag-photos/inbound/abc123.jpg",
        "sha256": "abc123",
        "filename": "inbound.jpg",
    }


def test_inbox_message_image_is_none_for_text_only(tmp_inbox: Path, tmp_state_db: Path) -> None:
    _append(tmp_inbox, {"message_id": "m1", "from": "1", "text": "hi", "image": None})
    from src.tail import Tailer

    tailer = Tailer(tmp_inbox, tmp_state_db.with_suffix(".offset"))
    msgs = list(tailer.iter_lines())
    assert msgs[0].has_image is False
    assert msgs[0].image is None


def test_inbox_message_image_is_none_for_oversize_drop(tmp_inbox: Path, tmp_state_db: Path) -> None:
    """Bridge drops oversize images, setting ``image=null`` on the line."""
    _append(
        tmp_inbox,
        {
            "message_id": "m1",
            "from": "1",
            "text": "huge pic",
            "image": None,
        },
    )
    from src.tail import Tailer

    tailer = Tailer(tmp_inbox, tmp_state_db.with_suffix(".offset"))
    msgs = list(tailer.iter_lines())
    assert msgs[0].image is None


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end: image-only ack (no RAG call)
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_image_only_sends_ack_no_rag(
    tmp_inbox: Path,
    tmp_state_db: Path,
    bridge_mock: respx.MockRouter,
    fake_rag_with_photo: list,
) -> None:
    port = _free_port()
    bridge_url = "http://bridge.test:7788"
    bridge_mock.post(f"{bridge_url}/send").mock(return_value=httpx.Response(200, json={"ok": True}))

    settings = main_mod.Settings.from_mapping(
        {
            "inbox_path": tmp_inbox,
            "orchestrator_db": tmp_state_db,
            "orchestrator_port": port,
            "wa_bridge_url": bridge_url,
            "wa_bridge_token": "secret",
            "log_level": "DEBUG",
        }
    )

    stop = threading.Event()
    thread = threading.Thread(
        target=lambda: main_mod.run_forever_until_stopped(settings, stop),
        daemon=True,
    )
    thread.start()

    try:
        _append(
            tmp_inbox,
            {
                "message_id": "img-only-1",
                "from": "6591234567",
                "text": "",
                "image": {
                    "path": "/root/rag-photos/inbound/abc123.jpg",
                    "sha256": "abc123",
                    "filename": "inbound.jpg",
                },
                "timestamp": "2026-06-24T10:00:00Z",
            },
        )
        for _ in range(80):
            if bridge_mock.calls:
                break
            time.sleep(0.05)
        # Exactly one /send: the ack.
        assert len(bridge_mock.calls) == 1
        body = json.loads(bridge_mock.calls[0].request.content)
        assert body == {"message_id": "img-only-1", "text": "Got the photo."}
        assert fake_rag_with_photo == []
    finally:
        stop.set()
        thread.join(timeout=5.0)

    with open_state(tmp_state_db) as s:
        assert s.get_last_image("6591234567") is not None
        assert s.get_last_image("6591234567")["path"] == "/root/rag-photos/inbound/abc123.jpg"


def test_e2e_image_chinese_only_sends_zh_ack(
    tmp_inbox: Path,
    tmp_state_db: Path,
    bridge_mock: respx.MockRouter,
    fake_rag_with_photo: list,
) -> None:
    port = _free_port()
    bridge_url = "http://bridge.test:7788"
    bridge_mock.post(f"{bridge_url}/send").mock(return_value=httpx.Response(200, json={"ok": True}))
    settings = main_mod.Settings.from_mapping(
        {
            "inbox_path": tmp_inbox,
            "orchestrator_db": tmp_state_db,
            "orchestrator_port": port,
            "wa_bridge_url": bridge_url,
            "wa_bridge_token": "secret",
            "log_level": "DEBUG",
        }
    )
    stop = threading.Event()
    thread = threading.Thread(
        target=lambda: main_mod.run_forever_until_stopped(settings, stop),
        daemon=True,
    )
    thread.start()
    try:
        _append(
            tmp_inbox,
            {
                "message_id": "img-zh-1",
                "from": "6591234567",
                "text": "看这个",
                "image": {
                    "path": "/root/rag-photos/inbound/abc.jpg",
                    "sha256": "abc",
                    "filename": "inbound.jpg",
                },
                "timestamp": "2026-06-24T10:00:00Z",
            },
        )
        for _ in range(80):
            if bridge_mock.calls:
                break
            time.sleep(0.05)
        assert len(bridge_mock.calls) == 1
        body = json.loads(bridge_mock.calls[0].request.content)
        assert body == {"message_id": "img-zh-1", "text": "收到图片了。"}
        assert fake_rag_with_photo == []
    finally:
        stop.set()
        thread.join(timeout=5.0)


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end: image + question caption -> ack + routed RAG
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_image_with_question_caption_routes_with_photo_context(
    tmp_inbox: Path,
    tmp_state_db: Path,
    bridge_mock: respx.MockRouter,
    fake_rag_with_photo: list,
) -> None:
    port = _free_port()
    bridge_url = "http://bridge.test:7788"
    bridge_mock.post(f"{bridge_url}/send").mock(return_value=httpx.Response(200, json={"ok": True}))
    settings = main_mod.Settings.from_mapping(
        {
            "inbox_path": tmp_inbox,
            "orchestrator_db": tmp_state_db,
            "orchestrator_port": port,
            "wa_bridge_url": bridge_url,
            "wa_bridge_token": "secret",
            "log_level": "DEBUG",
        }
    )
    stop = threading.Event()
    thread = threading.Thread(
        target=lambda: main_mod.run_forever_until_stopped(settings, stop),
        daemon=True,
    )
    thread.start()
    try:
        _append(
            tmp_inbox,
            {
                "message_id": "img-q-1",
                "from": "6591234567",
                "text": "what tour is this?",
                "image": {
                    "path": "/root/rag-photos/inbound/abc.jpg",
                    "sha256": "abc",
                    "filename": "inbound.jpg",
                },
                "timestamp": "2026-06-24T10:00:00Z",
            },
        )
        for _ in range(120):
            if len(bridge_mock.calls) >= 2:
                break
            time.sleep(0.05)
        assert len(bridge_mock.calls) == 2
        ack = json.loads(bridge_mock.calls[0].request.content)
        answer = json.loads(bridge_mock.calls[1].request.content)
        assert ack["text"] == "Got the photo."
        assert answer["message_id"] == "img-q-1"
        assert answer["text"].startswith("photo: /root/rag-photos/inbound/abc.jpg | ")
        # rag.ask_with_photo was called once with the photo path + caption.
        assert len(fake_rag_with_photo) == 1
        fn, photo_path, question = fake_rag_with_photo[0]
        assert fn == "ask_with_photo"
        assert photo_path == "/root/rag-photos/inbound/abc.jpg"
        assert "I have a photo at" in question
        assert "what tour is this?" in question
    finally:
        stop.set()
        thread.join(timeout=5.0)


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end: image + non-question caption -> ack only, no RAG
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_image_with_non_question_caption_sends_ack_only(
    tmp_inbox: Path,
    tmp_state_db: Path,
    bridge_mock: respx.MockRouter,
    fake_rag_with_photo: list,
) -> None:
    port = _free_port()
    bridge_url = "http://bridge.test:7788"
    bridge_mock.post(f"{bridge_url}/send").mock(return_value=httpx.Response(200, json={"ok": True}))
    settings = main_mod.Settings.from_mapping(
        {
            "inbox_path": tmp_inbox,
            "orchestrator_db": tmp_state_db,
            "orchestrator_port": port,
            "wa_bridge_url": bridge_url,
            "wa_bridge_token": "secret",
            "log_level": "DEBUG",
        }
    )
    stop = threading.Event()
    thread = threading.Thread(
        target=lambda: main_mod.run_forever_until_stopped(settings, stop),
        daemon=True,
    )
    thread.start()
    try:
        _append(
            tmp_inbox,
            {
                "message_id": "img-nq-1",
                "from": "6591234567",
                "text": "see this",
                "image": {
                    "path": "/root/rag-photos/inbound/abc.jpg",
                    "sha256": "abc",
                    "filename": "inbound.jpg",
                },
                "timestamp": "2026-06-24T10:00:00Z",
            },
        )
        for _ in range(80):
            if bridge_mock.calls:
                break
            time.sleep(0.05)
        assert len(bridge_mock.calls) == 1
        body = json.loads(bridge_mock.calls[0].request.content)
        assert body == {"message_id": "img-nq-1", "text": "Got the photo."}
        assert fake_rag_with_photo == []
    finally:
        stop.set()
        thread.join(timeout=5.0)


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end: oversize image dropped by bridge (image=None) -> plain text path
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_oversize_image_dropped_at_bridge_treated_as_text(
    tmp_inbox: Path,
    tmp_state_db: Path,
    bridge_mock: respx.MockRouter,
    fake_rag_with_photo: list,
) -> None:
    """Bridge drops oversize images, so the orchestrator sees ``image=None``
    and the caption text goes through the normal RAG path."""
    port = _free_port()
    bridge_url = "http://bridge.test:7788"
    bridge_mock.post(f"{bridge_url}/send").mock(return_value=httpx.Response(200, json={"ok": True}))
    settings = main_mod.Settings.from_mapping(
        {
            "inbox_path": tmp_inbox,
            "orchestrator_db": tmp_state_db,
            "orchestrator_port": port,
            "wa_bridge_url": bridge_url,
            "wa_bridge_token": "secret",
            "log_level": "DEBUG",
        }
    )
    stop = threading.Event()
    thread = threading.Thread(
        target=lambda: main_mod.run_forever_until_stopped(settings, stop),
        daemon=True,
    )
    thread.start()
    try:
        _append(
            tmp_inbox,
            {
                "message_id": "oversize-1",
                "from": "6591234567",
                "text": "huge pic",
                "image": None,
                "timestamp": "2026-06-24T10:00:00Z",
            },
        )
        for _ in range(80):
            if bridge_mock.calls:
                break
            time.sleep(0.05)
        assert len(bridge_mock.calls) == 1
        body = json.loads(bridge_mock.calls[0].request.content)
        assert body == {"message_id": "oversize-1", "text": "echo: huge pic"}
        assert len(fake_rag_with_photo) == 1
        assert fake_rag_with_photo[0][0] == "ask"
    finally:
        stop.set()
        thread.join(timeout=5.0)


# ──────────────────────────────────────────────────────────────────────────────
# Direct unit: handle_message with image
# ──────────────────────────────────────────────────────────────────────────────


def test_handle_message_image_branch_short_circuits_when_processed(
    tmp_state_db: Path, fake_rag_with_photo: list
) -> None:
    from src.settings import Settings

    with open_state(tmp_state_db) as state:
        state.mark_processed("already-img")
        settings = Settings.from_mapping(
            {"wa_bridge_url": "http://bridge.test:7788", "wa_bridge_token": "x"}
        )
        client = httpx.Client()
        try:
            main_mod.handle_message(
                state,
                client,
                settings,
                message_id="already-img",
                sender="1",
                text="",
                image={"path": "/p/x.jpg", "sha256": "h", "filename": "f.jpg"},
            )
            assert fake_rag_with_photo == []
        finally:
            client.close()


def test_image_ack_localisation() -> None:
    """``main._image_ack`` matches the documented EN/中文 strings."""
    assert main_mod._image_ack("en") == "Got the photo."
    assert main_mod._image_ack("zh") == "收到图片了。"
    assert main_mod._image_ack("zh-CN") == "收到图片了。"
    assert main_mod._image_ack("ZH") == "收到图片了。"
