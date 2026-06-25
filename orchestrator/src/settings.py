"""Typed runtime settings for the orchestrator.

Hand-rolled pydantic BaseSettings-style config (no ``pydantic-settings`` dep).
Reads ``.env`` via ``python-dotenv`` once at import time, validates required
paths/URLs eagerly, and exposes typed fields on :class:`Settings`.

All env access in the orchestrator goes through ``Settings.from_env()`` — no
``os.environ['X']`` scattered around. Tests construct ``Settings(...)`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import HttpUrl, TypeAdapter

_URL_ADAPTER = TypeAdapter(HttpUrl)


def _coerce_int(name: str, raw: str | None, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _coerce_url(raw: Any) -> HttpUrl:
    if isinstance(raw, HttpUrl):
        return raw
    return _URL_ADAPTER.validate_python(str(raw))


@dataclass(frozen=True)
class Settings:
    inbox_path: Path
    orchestrator_db: Path
    orchestrator_log: Path | None
    orchestrator_port: int
    wa_bridge_url: HttpUrl
    wa_bridge_token: str
    log_level: str
    booking_horizon_days: int = 90
    admin_http_token: str = ""
    admin_telegram_ids: tuple[int, ...] = ()
    booking_rules_path: Path | None = None

    @classmethod
    def from_env(cls, env_file: str | os.PathLike[str] | None = None) -> Settings:
        env: dict[str, str] = {}
        for k, v in dotenv_values(env_file or ".env").items():
            if v is not None:
                env[k] = v
        for k, v in os.environ.items():
            env.setdefault(k, v)
        return cls(
            inbox_path=Path(env["INBOX_PATH"]),
            orchestrator_db=Path(env["ORCHESTRATOR_DB"]),
            orchestrator_log=(
                Path(env["ORCHESTRATOR_LOG"]) if env.get("ORCHESTRATOR_LOG") else None
            ),
            orchestrator_port=_coerce_int("ORCHESTRATOR_PORT", env.get("ORCHESTRATOR_PORT"), 7789),
            wa_bridge_url=_coerce_url(env["WA_BRIDGE_URL"]),
            wa_bridge_token=env.get("WA_BRIDGE_TOKEN", ""),
            log_level=env.get("LOG_LEVEL", "INFO"),
            booking_horizon_days=_coerce_int(
                "BOOKING_HORIZON_DAYS", env.get("BOOKING_HORIZON_DAYS"), 90
            ),
            admin_http_token=env.get("ADMIN_HTTP_TOKEN", ""),
            admin_telegram_ids=_parse_admin_telegram_ids(env.get("ADMIN_TELEGRAM_IDS")),
            booking_rules_path=(
                Path(env["BOOKING_RULES_PATH"]) if env.get("BOOKING_RULES_PATH") else None
            ),
        )

    @staticmethod
    def from_mapping(mapping: dict[str, Any]) -> Settings:
        """Build Settings from a plain dict (used by tests + fixtures)."""
        defaults: dict[str, Any] = dict(
            inbox_path=Path("/tmp/inbox.ndjson"),
            orchestrator_db=Path("/tmp/state.sqlite"),
            orchestrator_log=None,
            orchestrator_port=7789,
            wa_bridge_url="http://127.0.0.1:7788",
            wa_bridge_token="test-token",
            log_level="INFO",
            booking_horizon_days=90,
            admin_http_token="",
            admin_telegram_ids=(),
            booking_rules_path=None,
        )
        defaults.update({k: v for k, v in mapping.items() if v is not None})
        admin_ids = defaults["admin_telegram_ids"]
        if isinstance(admin_ids, (str, bytes)):
            admin_ids = _parse_admin_telegram_ids(
                admin_ids.decode() if isinstance(admin_ids, bytes) else admin_ids
            )
        elif not isinstance(admin_ids, tuple):
            admin_ids = tuple(int(x) for x in admin_ids)
        return Settings(
            inbox_path=Path(defaults["inbox_path"]),
            orchestrator_db=Path(defaults["orchestrator_db"]),
            orchestrator_log=(
                Path(defaults["orchestrator_log"]) if defaults.get("orchestrator_log") else None
            ),
            orchestrator_port=int(defaults["orchestrator_port"]),
            wa_bridge_url=_coerce_url(defaults["wa_bridge_url"]),
            wa_bridge_token=str(defaults["wa_bridge_token"]),
            log_level=str(defaults["log_level"]),
            booking_horizon_days=int(defaults["booking_horizon_days"]),
            admin_http_token=str(defaults["admin_http_token"]),
            admin_telegram_ids=admin_ids,
            booking_rules_path=(
                Path(defaults["booking_rules_path"]) if defaults.get("booking_rules_path") else None
            ),
        )


def _parse_admin_telegram_ids(raw: str | None) -> tuple[int, ...]:
    """Parse ``ADMIN_TELEGRAM_IDS`` (comma-separated ints) into a tuple.

    Empty / unset → empty tuple. Bad entries are skipped with a soft
    warning (rather than raising at boot) so a single typo in the env
    doesn't take the orchestrator down.
    """
    if not raw:
        return ()
    out: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            import logging

            logging.getLogger(__name__).warning(
                "ADMIN_TELEGRAM_IDS: skipping non-integer token %r", token
            )
    return tuple(out)


__all__ = ["Settings"]
