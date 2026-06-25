"""ACL round-trip tests for the admin sub-app.

We don't have rag_qdrant installed in the orchestrator test venv, so
the tests monkeypatch a fake ``rag_qdrant`` module into ``sys.modules``
before the handler imports it. The fake records calls and returns the
canned response shape so we can assert that:

- ``POST /admin/ingest`` calls ``rag_qdrant.ingest_file`` with the
  right ACL (default admin-only, int override, ``"public"`` override).
- ``POST /admin/grant`` and ``POST /admin/revoke`` both forward
  ``(source, telegram_id)`` to ``rag_qdrant``.
- ``GET /admin/show`` reads via ``rag_qdrant.show_access``.

We also exercise the dispatcher's handling of malformed bodies and the
``?source=...`` filter on ``/admin/show``.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.admin import AdminSettings

from .conftest import auth_headers, call

# ---------------------------------------------------------------------------
# Fake rag_qdrant module injected before the handler imports it
# ---------------------------------------------------------------------------


class _FakeRagModule:
    def __init__(self) -> None:
        self.ingest_calls: list[dict[str, object]] = []
        self.grant_calls: list[dict[str, object]] = []
        self.revoke_calls: list[dict[str, object]] = []
        self.show_calls: list[str] = []
        # Per-source fake ACL for show_access
        self.acl_state: dict[str, dict[str, object]] = {}

    def ingest_file(self, path: Path, *, source: str, allowed_telegram_ids=None):  # type: ignore[no-untyped-def]
        self.ingest_calls.append(
            {
                "path": path,
                "source": source,
                "allowed_telegram_ids": list(allowed_telegram_ids)
                if allowed_telegram_ids is not None
                else None,
            }
        )
        # Echo the ACL we stored so tests can assert round-trip
        self.acl_state[source] = {
            "allowed_telegram_ids": list(allowed_telegram_ids)
            if allowed_telegram_ids is not None
            else None,
            "chunk_count": 3,
        }
        return 3  # chunk count

    def grant_access(self, source: str, telegram_id):  # type: ignore[no-untyped-def]
        norm = str(int(telegram_id)) if not isinstance(telegram_id, str) else telegram_id
        self.grant_calls.append({"source": source, "telegram_id": norm})
        cur = self.acl_state.setdefault(source, {"allowed_telegram_ids": None, "chunk_count": 0})
        existing = list(cur.get("allowed_telegram_ids") or [])
        if norm not in existing:
            existing.append(norm)
        cur["allowed_telegram_ids"] = existing
        return {"source": source, "telegram_id": norm, "updated": 3}

    def revoke_access(self, source: str, telegram_id):  # type: ignore[no-untyped-def]
        norm = str(int(telegram_id)) if not isinstance(telegram_id, str) else telegram_id
        self.revoke_calls.append({"source": source, "telegram_id": norm})
        cur = self.acl_state.setdefault(source, {"allowed_telegram_ids": None, "chunk_count": 0})
        existing = list(cur.get("allowed_telegram_ids") or [])
        existing = [x for x in existing if x != norm]
        cur["allowed_telegram_ids"] = existing if existing else None
        return {"source": source, "telegram_id": norm, "updated": 3, "removed": 1}

    def show_access(self, source: str):  # type: ignore[no-untyped-def]
        self.show_calls.append(source)
        cur = self.acl_state.setdefault(source, {"allowed_telegram_ids": None, "chunk_count": 0})
        return {
            "source": source,
            "allowed_telegram_ids": list(cur.get("allowed_telegram_ids") or []),
            "chunk_count": cur.get("chunk_count", 0),
        }

    def get_qdrant_client(self):  # type: ignore[no-untyped-def]
        return _FakeClient(self)

    # config submodule shim — handler imports ``rag_qdrant.config.settings``
    @property
    def config(self) -> _FakeConfig:
        return _FakeConfig()


class _FakeConfig:
    @property
    def qdrant_collection(self) -> str:
        return "test_collection"


class _FakeClient:
    def __init__(self, rag: _FakeRagModule) -> None:
        self._rag = rag

    def scroll(self, *, collection_name, with_payload, with_vectors, limit, offset=None):  # type: ignore[no-untyped-def]
        # Pretend we have a few sources in the collection
        sources = sorted(self._rag.acl_state.keys())
        points = []
        for src in sources:
            points.append(
                types.SimpleNamespace(
                    payload={"source": src},
                )
            )
        return points, None


@pytest.fixture
def fake_rag(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeRagModule]:
    """Inject a fake ``rag_qdrant`` module into ``sys.modules`` for the test."""
    fake = _FakeRagModule()
    mod = types.ModuleType("rag_qdrant")
    mod.ingest_file = fake.ingest_file
    mod.grant_access = fake.grant_access
    mod.revoke_access = fake.revoke_access
    mod.show_access = fake.show_access
    mod.get_qdrant_client = fake.get_qdrant_client
    # nested ``config`` submodule
    config_mod = types.ModuleType("rag_qdrant.config")
    config_mod.settings = _FakeConfig()
    monkeypatch.setitem(sys.modules, "rag_qdrant.config", config_mod)
    monkeypatch.setitem(sys.modules, "rag_qdrant", mod)
    yield fake
    monkeypatch.delitem(sys.modules, "rag_qdrant", raising=False)
    monkeypatch.delitem(sys.modules, "rag_qdrant.config", raising=False)


# ---------------------------------------------------------------------------
# /admin/ingest
# ---------------------------------------------------------------------------


def test_ingest_default_acl_is_admins(
    tmp_path: Path, admin_cfg: AdminSettings, fake_rag: _FakeRagModule
) -> None:
    md = tmp_path / "rules.md"
    md.write_text("# rules\nstuff", encoding="utf-8")
    status, payload = call(
        "POST",
        "/admin/ingest",
        body={"source_type": "path", "target": str(md)},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 200, payload
    assert payload["chunks"] == 3
    assert payload["acl"] == {"public": False, "telegram_ids": [111, 222]}
    assert len(fake_rag.ingest_calls) == 1
    call_args = fake_rag.ingest_calls[0]
    assert call_args["source"] == "rules"
    # rag_qdrant normalizes ints to strings (per qdrant_store._normalize_allowed_telegram_ids).
    # Our handler forwards the ints as-is; the fake mirrors the upstream behaviour.
    assert [str(x) for x in call_args["allowed_telegram_ids"]] == ["111", "222"]


def test_ingest_with_int_acl(
    tmp_path: Path, admin_cfg: AdminSettings, fake_rag: _FakeRagModule
) -> None:
    md = tmp_path / "notes.md"
    md.write_text("notes", encoding="utf-8")
    status, payload = call(
        "POST",
        "/admin/ingest",
        body={"source_type": "path", "target": str(md), "telegram_id_acl": 555},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 200
    assert payload["acl"] == {"public": False, "telegram_ids": [555]}
    assert [str(x) for x in fake_rag.ingest_calls[0]["allowed_telegram_ids"]] == ["555"]


def test_ingest_with_public_acl(
    tmp_path: Path, admin_cfg: AdminSettings, fake_rag: _FakeRagModule
) -> None:
    md = tmp_path / "public.md"
    md.write_text("anything", encoding="utf-8")
    status, payload = call(
        "POST",
        "/admin/ingest",
        body={"source_type": "path", "target": str(md), "telegram_id_acl": "public"},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 200
    assert payload["acl"] == {"public": True, "telegram_ids": []}
    assert fake_rag.ingest_calls[0]["allowed_telegram_ids"] == []


def test_ingest_rejects_missing_file(
    tmp_path: Path, admin_cfg: AdminSettings, fake_rag: _FakeRagModule
) -> None:
    status, payload = call(
        "POST",
        "/admin/ingest",
        body={"source_type": "path", "target": str(tmp_path / "missing.md")},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert "file not found" in payload["message"]


def test_ingest_rejects_unknown_source_type(
    admin_cfg: AdminSettings, fake_rag: _FakeRagModule
) -> None:
    status, payload = call(
        "POST",
        "/admin/ingest",
        body={"source_type": "gopher", "target": "/tmp/x"},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert "source_type" in payload["message"]


def test_ingest_rejects_bad_acl_string(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    status, payload = call(
        "POST",
        "/admin/ingest",
        body={"source_type": "path", "target": "/tmp/x", "telegram_id_acl": "everyone"},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert "telegram_id_acl" in payload["message"]


def test_ingest_requires_target(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    status, payload = call(
        "POST",
        "/admin/ingest",
        body={"source_type": "path"},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert "target" in payload["message"]


def test_ingest_md_source_requires_md_extension(
    tmp_path: Path, admin_cfg: AdminSettings, fake_rag: _FakeRagModule
) -> None:
    pdf = tmp_path / "x.txt"
    pdf.write_text("text", encoding="utf-8")
    status, payload = call(
        "POST",
        "/admin/ingest",
        body={"source_type": "md", "target": str(pdf)},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert ".md" in payload["message"]


# ---------------------------------------------------------------------------
# /admin/grant + /admin/revoke
# ---------------------------------------------------------------------------


def test_grant_calls_rag_qdrant(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    fake_rag.acl_state["faq"] = {"allowed_telegram_ids": ["111"], "chunk_count": 3}
    status, payload = call(
        "POST",
        "/admin/grant",
        body={"source": "faq", "telegram_id": 555},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 200
    assert payload["action"] == "grant"
    assert payload["source"] == "faq"
    assert payload["telegram_id"] == "555"
    assert payload["updated"] == 3
    assert fake_rag.grant_calls == [{"source": "faq", "telegram_id": "555"}]


def test_revoke_calls_rag_qdrant(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    fake_rag.acl_state["faq"] = {"allowed_telegram_ids": ["111", "555"], "chunk_count": 3}
    status, payload = call(
        "POST",
        "/admin/revoke",
        body={"source": "faq", "telegram_id": 555},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 200
    assert payload["action"] == "revoke"
    assert payload["source"] == "faq"
    assert fake_rag.revoke_calls == [{"source": "faq", "telegram_id": "555"}]


def test_grant_requires_source(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    status, payload = call(
        "POST",
        "/admin/grant",
        body={"telegram_id": 555},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert "source" in payload["message"]


def test_grant_requires_telegram_id(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    status, payload = call(
        "POST",
        "/admin/grant",
        body={"source": "faq"},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert "telegram_id" in payload["message"]


def test_grant_rejects_username_for_now(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    status, payload = call(
        "POST",
        "/admin/grant",
        body={"source": "faq", "username": "alice"},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400
    assert "username" in payload["message"]


def test_grant_rejects_non_int_id(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    status, _payload = call(
        "POST",
        "/admin/grant",
        body={"source": "faq", "telegram_id": "555"},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400


def test_grant_rejects_bool_id(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    status, _payload = call(
        "POST",
        "/admin/grant",
        body={"source": "faq", "telegram_id": True},
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 400


# ---------------------------------------------------------------------------
# /admin/show
# ---------------------------------------------------------------------------


def test_show_lists_source(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    fake_rag.acl_state["faq"] = {"allowed_telegram_ids": ["111"], "chunk_count": 4}
    fake_rag.acl_state["rules"] = {"allowed_telegram_ids": None, "chunk_count": 7}
    status, payload = call(
        "GET",
        "/admin/show?source=faq",
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 200
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["source"] == "faq"
    assert payload["rows"][0]["allowed_telegram_ids"] == ["111"]


def test_show_lists_all_sources(admin_cfg: AdminSettings, fake_rag: _FakeRagModule) -> None:
    fake_rag.acl_state["faq"] = {"allowed_telegram_ids": ["111"], "chunk_count": 4}
    fake_rag.acl_state["rules"] = {"allowed_telegram_ids": None, "chunk_count": 7}
    status, payload = call(
        "GET",
        "/admin/show",
        headers=auth_headers(),
        admin=admin_cfg,
    )
    assert status == 200
    sources = {row["source"] for row in payload["rows"]}
    assert sources == {"faq", "rules"}
