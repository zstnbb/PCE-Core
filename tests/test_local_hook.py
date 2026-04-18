# SPDX-License-Identifier: Apache-2.0
"""Tests for pce_core.local_hook – local model capture proxy."""

import sys
import os
import json
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["PCE_DATA_DIR"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pce_core.config import ALLOWED_HOSTS, LOCAL_MODEL_PORTS
from pce_core.db import init_db, query_captures, query_by_pair
from pce_core.local_hook.hook import (
    create_hook_app,
    _extract_model,
    _detect_provider,
    _capture_async,
)


def test_extended_allowlist():
    """Verify the allowlist has been extended with new domains."""
    # Original domains
    assert "api.openai.com" in ALLOWED_HOSTS
    assert "api.anthropic.com" in ALLOWED_HOSTS
    assert "api.deepseek.com" in ALLOWED_HOSTS

    # Newly added
    assert "api.replicate.com" in ALLOWED_HOSTS
    assert "api-inference.huggingface.co" in ALLOWED_HOSTS
    assert "api.ai21.com" in ALLOWED_HOSTS
    assert "api.stability.ai" in ALLOWED_HOSTS
    assert "codewhisperer.amazonaws.com" in ALLOWED_HOSTS
    assert "api.tabnine.com" in ALLOWED_HOSTS
    assert "api.sourcegraph.com" in ALLOWED_HOSTS

    # Local model servers
    assert "localhost" in ALLOWED_HOSTS
    assert "127.0.0.1" in ALLOWED_HOSTS

    # Chinese providers
    assert "api.moonshot.cn" in ALLOWED_HOSTS
    assert "api.baichuan-ai.com" in ALLOWED_HOSTS
    assert "api.zhipuai.cn" in ALLOWED_HOSTS

    print(f"[PASS] Extended allowlist: {len(ALLOWED_HOSTS)} domains")


def test_local_model_ports():
    """Verify known local model ports are defined."""
    assert 11434 in LOCAL_MODEL_PORTS  # Ollama
    assert 1234 in LOCAL_MODEL_PORTS   # LM Studio
    assert 8000 in LOCAL_MODEL_PORTS   # vLLM
    assert 8080 in LOCAL_MODEL_PORTS   # LocalAI
    assert 5000 in LOCAL_MODEL_PORTS   # Text Gen WebUI
    assert 3000 in LOCAL_MODEL_PORTS   # Jan
    print(f"[PASS] Local model ports: {len(LOCAL_MODEL_PORTS)} known ports")


def test_detect_provider():
    """Test port-to-provider mapping."""
    assert _detect_provider(11434) == "ollama"
    assert _detect_provider(1234) == "lm-studio"
    assert _detect_provider(8000) == "vllm"
    assert _detect_provider(8080) == "localai"
    assert _detect_provider(5000) == "text-gen-webui"
    assert _detect_provider(3000) == "jan"
    assert _detect_provider(9999) == "local-model"
    print("[PASS] Provider detection from port")


def test_extract_model():
    """Test model extraction from request body."""
    assert _extract_model('{"model":"llama3","messages":[]}') == "llama3"
    assert _extract_model('{"model":"mistral-7b","prompt":"hi"}') == "mistral-7b"
    assert _extract_model("not json") is None
    assert _extract_model("") is None
    assert _extract_model('{"messages":[]}') is None
    print("[PASS] Model extraction from body")


def test_capture_roundtrip():
    """Test that _capture_async actually writes to the DB."""
    # Use default DB path (from PCE_DATA_DIR env var set at top of file)
    init_db()

    import uuid
    pair_id = f"test-hook-pair-{uuid.uuid4().hex[:8]}"

    _capture_async(
        direction="request",
        pair_id=pair_id,
        host="127.0.0.1:11434",
        path="/api/chat",
        method="POST",
        provider="ollama",
        model_name="llama3",
        body_text='{"model":"llama3","messages":[{"role":"user","content":"hello"}]}',
    )

    _capture_async(
        direction="response",
        pair_id=pair_id,
        host="127.0.0.1:11434",
        path="/api/chat",
        method="POST",
        provider="ollama",
        model_name="llama3",
        status_code=200,
        latency_ms=150.5,
        body_text='{"message":{"role":"assistant","content":"Hi there!"},"model":"llama3"}',
    )

    # Verify captures are in DB (using default db_path)
    rows = query_by_pair(pair_id)
    assert len(rows) == 2
    directions = {r["direction"] for r in rows}
    assert directions == {"request", "response"}

    req_row = [r for r in rows if r["direction"] == "request"][0]
    assert req_row["provider"] == "ollama"
    assert req_row["model_name"] == "llama3"
    assert req_row["host"] == "127.0.0.1:11434"

    resp_row = [r for r in rows if r["direction"] == "response"][0]
    assert resp_row["status_code"] == 200
    assert resp_row["latency_ms"] == 150.5

    # Verify meta_json has capture_source
    meta = json.loads(req_row["meta_json"]) if req_row.get("meta_json") else {}
    assert meta.get("capture_source") == "local_hook"

    print("[PASS] Capture roundtrip: request + response stored with correct metadata")


def test_create_hook_app():
    """Test that create_hook_app returns a valid FastAPI app."""
    app = create_hook_app(target_host="127.0.0.1", target_port=11434)
    assert app is not None
    assert app.title == "PCE Local Hook → http://127.0.0.1:11434"

    # Verify route is registered
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/{path:path}" in routes
    print("[PASS] Hook app created with correct target URL and routes")


def test_all():
    test_extended_allowlist()
    test_local_model_ports()
    test_detect_provider()
    test_extract_model()
    test_capture_roundtrip()
    test_create_hook_app()
    print("\n=== All local hook tests passed ===")


if __name__ == "__main__":
    test_all()
