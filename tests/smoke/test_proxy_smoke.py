# SPDX-License-Identifier: Apache-2.0
"""Smoke: mitmproxy addon path.

Exercises the PCE proxy addon against a synthetic HTTPFlow and verifies:
1. A capture is persisted when the host is on the allowlist.
2. Sensitive headers are redacted before landing in SQLite.
3. Non-allowlisted hosts are ignored (no row inserted).
4. The addon emits structured events (``capture.request_recorded`` etc.).

This test does NOT start a real ``mitmdump`` server — it imports the
addon class and calls its ``request`` / ``response`` handlers directly.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytest.importorskip("mitmproxy")  # addon imports mitmproxy types


def _make_flow(host: str, *, request_body: bytes, response_body: bytes, status: int = 200):
    """Build a synthetic mitmproxy flow with the fields PCEAddon needs."""
    from mitmproxy.test import tflow, tutils

    req = tutils.treq(
        method=b"POST",
        scheme=b"https",
        authority=host.encode(),
        host=host,
        port=443,
        path=b"/v1/chat/completions",
        content=request_body,
        headers=(
            (b"authorization", b"Bearer sk-secret-token"),
            (b"cookie", b"session=abc123"),
            (b"content-type", b"application/json"),
        ),
    )
    resp = tutils.tresp(
        status_code=status,
        content=response_body,
        headers=(
            (b"set-cookie", b"tracker=xyz"),
            (b"content-type", b"application/json"),
        ),
    )
    flow = tflow.tflow(req=req, resp=resp)
    return flow


def _addon(db_path: Path, monkeypatch):
    """Build a PCEAddon configured to write into ``db_path``."""
    from pce_proxy import addon as proxy_addon
    from pce_core import db as pce_db

    # The addon module called ``init_db()`` at import time into the session
    # temp dir. Re-initialise against the per-test DB and route all capture
    # writes there by stubbing DB_PATH where needed.
    pce_db.init_db(db_path)

    def _insert(*args, **kwargs):
        kwargs.setdefault("db_path", db_path)
        return pce_db.insert_capture(*args, **kwargs)

    def _record(stage, message, **kwargs):
        kwargs.setdefault("db_path", db_path)
        return pce_db.record_pipeline_error(stage, message, **kwargs)

    monkeypatch.setattr(proxy_addon, "insert_capture", _insert)
    monkeypatch.setattr(proxy_addon, "record_pipeline_error", _record)
    # Don't attempt to auto-normalize during smoke (it has its own DB path)
    monkeypatch.setattr(proxy_addon, "try_normalize_pair", lambda *a, **k: None)
    return proxy_addon.PCEAddon()


def test_allowlisted_request_and_response_persisted(tmp_path: Path, monkeypatch, caplog):
    db = tmp_path / "proxy.db"
    addon = _addon(db, monkeypatch)

    caplog.set_level(logging.INFO, logger="pce.addon")

    req_body = json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": "hello"}]}).encode()
    resp_body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "hi!"}}]}).encode()

    flow = _make_flow("api.openai.com", request_body=req_body, response_body=resp_body)
    addon.request(flow)
    addon.response(flow)

    # Verify two rows landed in raw_captures
    from pce_core.db import query_recent
    rows = query_recent(10, db_path=db)
    assert len(rows) == 2, f"Expected 2 captures, got {len(rows)}: {rows}"
    directions = {r["direction"] for r in rows}
    assert directions == {"request", "response"}
    assert all(r["host"] == "api.openai.com" for r in rows)
    assert all(r["provider"] == "openai" for r in rows)

    # Redaction: no secrets landed
    for r in rows:
        blob = (r.get("headers_redacted_json") or "") + (r.get("body_text_or_json") or "")
        assert "sk-secret-token" not in blob, "API key leaked into capture"
        assert "session=abc123" not in blob, "Cookie leaked into capture"
    req_headers = next(json.loads(r["headers_redacted_json"]) for r in rows if r["direction"] == "request")
    # Header names preserved (lowercase per mitmproxy), values replaced
    lowered = {k.lower(): v for k, v in req_headers.items()}
    assert lowered.get("authorization") == "REDACTED"
    assert lowered.get("cookie") == "REDACTED"

    # Structured events were emitted
    events = {getattr(r, "event", None) for r in caplog.records}
    assert "capture.request_recorded" in events
    assert "capture.response_recorded" in events


def test_non_allowlisted_host_is_ignored(tmp_path: Path, monkeypatch):
    db = tmp_path / "proxy_ignore.db"
    addon = _addon(db, monkeypatch)

    flow = _make_flow(
        "example.com",
        request_body=b'{"anything":"here"}',
        response_body=b'{"ok":true}',
    )
    addon.request(flow)
    addon.response(flow)

    from pce_core.db import query_recent
    rows = query_recent(10, db_path=db)
    assert rows == [], f"Non-AI host should not be captured, got: {rows}"


def test_response_latency_is_recorded(tmp_path: Path, monkeypatch):
    db = tmp_path / "proxy_latency.db"
    addon = _addon(db, monkeypatch)

    flow = _make_flow(
        "api.anthropic.com",
        request_body=b'{"model":"claude-3","messages":[]}',
        response_body=b'{"content":[{"type":"text","text":"ok"}]}',
    )
    addon.request(flow)
    addon.response(flow)

    from pce_core.db import query_recent
    rows = query_recent(10, db_path=db)
    resp_row = next(r for r in rows if r["direction"] == "response")
    assert resp_row["status_code"] == 200
    assert resp_row["latency_ms"] is not None
    assert resp_row["latency_ms"] >= 0
