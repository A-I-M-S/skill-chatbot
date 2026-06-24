"""Inbound image handling for the orchestrator (issue #10)."""

from __future__ import annotations

import logging
import re
from typing import Any

from .state import State

# ruff: noqa: RUF001  (Chinese string literals use fullwidth punctuation on purpose)

logger = logging.getLogger(__name__)

IMAGE_ACK_EN = "Got the photo."
IMAGE_ACK_ZH = "收到图片了。"

QUESTION_CHARS = ("?", "？")
QUESTION_KEYWORDS = (
    "what",
    "which",
    "when",
    "where",
    "who",
    "why",
    "how",
    "can",
    "could",
    "would",
    "should",
    "is",
    "are",
    "do",
    "does",
    "tell",
    "show",
    "describe",
    "explain",
    "recognize",
    "identify",
)
ZH_QUESTION_KEYWORDS = (
    "什么",
    "哪",
    "怎么",
    "为什么",
    "吗",
    "呢",
    "是",
    "有没有",
    "可以",
    "能否",
    "识别",
    "看看",
    "介绍",
    "解释",
    "告诉",
)

_PHOTO_CONTEXT_TEMPLATE_EN = "I have a photo at {path} from this chat."
_PHOTO_CONTEXT_TEMPLATE_ZH = "我这边有一张图片，路径 {path}。"


def is_question_caption(text: str) -> bool:
    """Cheap heuristic: does the caption look like a question?

    Returns ``True`` if the text ends with a question mark (EN or CJK) OR
    starts with one of the common WH/auxiliary question words (EN + ZH). The
    router will get the photo context prepended; it can choose to ignore it
    if the caption is a non-question (e.g. ``"see this"``).
    """
    s = (text or "").strip()
    if not s:
        return False
    if s.endswith(QUESTION_CHARS):
        return True
    lower = s.lower()
    for kw in QUESTION_KEYWORDS:
        if lower.startswith(kw + " ") or lower == kw:
            return True
    for kw in ZH_QUESTION_KEYWORDS:
        if s.startswith(kw):
            return True
    return bool(re.search(r"\?|？", s))


def detect_language_hint(text: str) -> str:
    """Best-effort language hint for the ack message.

    Returns ``"zh"`` if the caption (or fallback empty) contains CJK
    characters, ``"en"`` otherwise. The full multilingual router (#9) will
    take over for body replies; this is only the ack.
    """
    if not text:
        return "en"
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return "zh"
    return "en"


def build_photo_context_line(image_path: str, language: str = "en") -> str:
    """Build the ``I have a photo at <path> from this chat.`` prepended line."""
    if language == "zh":
        return _PHOTO_CONTEXT_TEMPLATE_ZH.format(path=image_path)
    return _PHOTO_CONTEXT_TEMPLATE_EN.format(path=image_path)


def ack_for(language: str) -> str:
    return IMAGE_ACK_ZH if language == "zh" else IMAGE_ACK_EN


def save_last_image(state: State, sender: str, image: dict[str, str], message_id: str) -> None:
    """Persist the inbound image metadata for ``sender``."""
    state.set_last_image(
        sender=sender,
        message_id=message_id,
        path=str(image["path"]),
        sha256=str(image["sha256"]),
        filename=str(image["filename"]),
    )
    logger.info(
        "saved last_image sender=%s message_id=%s path=%s",
        sender,
        message_id,
        image["path"],
    )


def build_image_user_message(image: dict[str, str], caption: str, language: str = "en") -> str:
    """Compose the user-facing text for the router when the caption is a question.

    Per the brief: ``prepend I have a photo at <path> from this chat. to the
    router's user-message``. The router (issue #4) may then call ``faq`` to
    search the rag-photos corpus. If the caption is empty we still return the
    prepended line so the LLM knows there is a photo but no question to answer.
    """
    photo_line = build_photo_context_line(image["path"], language=language)
    caption = (caption or "").strip()
    if not caption:
        return photo_line
    return f"{photo_line}\n{caption}"


def process_inbound_image(
    state: State,
    sender: str,
    message_id: str,
    image: dict[str, str],
    caption: str = "",
    language: str | None = None,
) -> dict[str, Any]:
    """Run the image-inbound step: save metadata + return the ack.

    Returns a dict with:

    - ``ack``: the short ack message to send to the customer.
    - ``user_message``: the (possibly photo-context-prepended) text to feed
      to the router — empty when the caption is blank (the router is not
      called for image-only messages; the ack alone is the reply).
    - ``language``: ``"en"`` or ``"zh"`` (used by the caller for i18n).
    - ``routed``: ``True`` when the router should be invoked (caption is a
      question), ``False`` otherwise.
    """
    if language is None:
        language = detect_language_hint(caption)
    save_last_image(state, sender, image, message_id)
    if not caption.strip():
        logger.info(
            "image-only inbound sender=%s message_id=%s -> ack only",
            sender,
            message_id,
        )
        return {
            "ack": ack_for(language),
            "user_message": "",
            "language": language,
            "routed": False,
        }
    if is_question_caption(caption):
        logger.info(
            "image+caption routed as question sender=%s message_id=%s",
            sender,
            message_id,
        )
        return {
            "ack": ack_for(language),
            "user_message": build_image_user_message(image, caption, language=language),
            "language": language,
            "routed": True,
        }
    logger.info(
        "image+non-question caption sender=%s message_id=%s -> ack only",
        sender,
        message_id,
    )
    return {
        "ack": ack_for(language),
        "user_message": "",
        "language": language,
        "routed": False,
    }


__all__ = [
    "IMAGE_ACK_EN",
    "IMAGE_ACK_ZH",
    "ack_for",
    "build_image_user_message",
    "build_photo_context_line",
    "detect_language_hint",
    "is_question_caption",
    "process_inbound_image",
    "save_last_image",
]
