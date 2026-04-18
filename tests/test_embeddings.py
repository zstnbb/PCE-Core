# SPDX-License-Identifier: Apache-2.0
"""Tests for the pluggable embeddings backend layer (P4.1).

Covers the surface of :mod:`pce_core.embeddings`:

- spec parsing
- noop / st / ollama / openai backend construction
- spec resolution order (arg → env → app_state → default)
- cache / reset behaviour
- graceful degradation when a backend fails to load
- ``embed_texts`` returning ``None`` when the backend is unavailable

No test in this module requires the ``sentence-transformers`` package
or a running Ollama / OpenAI endpoint — we mock the external surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    import pce_core.config as _cfg
    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "pce.db", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_embeddings_cache():
    from pce_core import embeddings
    embeddings.reset()
    yield
    embeddings.reset()


# ---------------------------------------------------------------------------
# parse_spec
# ---------------------------------------------------------------------------

class TestParseSpec:
    def test_empty(self):
        from pce_core.embeddings.backends import parse_spec
        assert parse_spec("") == ("", "", "")
        assert parse_spec("   ") == ("", "", "")

    def test_name_only(self):
        from pce_core.embeddings.backends import parse_spec
        assert parse_spec("disabled") == ("disabled", "", "")

    def test_name_and_model(self):
        from pce_core.embeddings.backends import parse_spec
        assert parse_spec("sentence_transformers:all-MiniLM-L6-v2") == (
            "sentence_transformers", "all-MiniLM-L6-v2", "",
        )

    def test_name_and_endpoint(self):
        from pce_core.embeddings.backends import parse_spec
        assert parse_spec("ollama@http://127.0.0.1:11434") == (
            "ollama", "", "http://127.0.0.1:11434",
        )

    def test_name_model_and_endpoint(self):
        from pce_core.embeddings.backends import parse_spec
        assert parse_spec("ollama:mxbai-embed-large@http://127.0.0.1:11434") == (
            "ollama", "mxbai-embed-large", "http://127.0.0.1:11434",
        )


# ---------------------------------------------------------------------------
# NoopBackend (default)
# ---------------------------------------------------------------------------

class TestNoopBackend:
    def test_shape(self):
        from pce_core.embeddings.backends import NoopBackend
        b = NoopBackend()
        assert b.available is False
        assert b.dim == 0
        assert b.name == "disabled"
        assert b.embed_batch(["hi", "world"]) == [[], []]


# ---------------------------------------------------------------------------
# Factory resolution
# ---------------------------------------------------------------------------

class TestGetBackend:

    def test_explicit_disabled_spec(self, monkeypatch):
        from pce_core import embeddings
        monkeypatch.delenv("PCE_EMBEDDINGS_BACKEND", raising=False)
        b = embeddings.get_backend("disabled")
        assert b.available is False

    def test_env_var_wins_over_default(self, monkeypatch):
        from pce_core import embeddings
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        b = embeddings.get_backend()
        assert b.available is False

    def test_prefs_used_when_env_missing(self, monkeypatch, isolated_data_dir):
        from pce_core import app_state, embeddings
        monkeypatch.delenv("PCE_EMBEDDINGS_BACKEND", raising=False)

        state = app_state.load_state()
        state.setdefault("preferences", {})["embeddings_backend"] = "disabled"
        app_state.save_state(state)

        b = embeddings.get_backend()
        assert b.name == "disabled"

    def test_unknown_backend_falls_back_to_noop(self, monkeypatch):
        from pce_core import embeddings
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "nonsense_backend")
        b = embeddings.get_backend()
        assert b.available is False
        assert b.reason and "unknown_backend" in b.reason

    def test_cache_is_reused_for_same_spec(self, monkeypatch):
        from pce_core import embeddings
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        b1 = embeddings.get_backend()
        b2 = embeddings.get_backend()
        assert b1 is b2

    def test_reset_drops_cache(self, monkeypatch):
        from pce_core import embeddings
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        b1 = embeddings.get_backend()
        embeddings.reset()
        b2 = embeddings.get_backend()
        assert b1 is not b2


# ---------------------------------------------------------------------------
# SentenceTransformersBackend: only test availability detection without
# actually loading the (large) model.
# ---------------------------------------------------------------------------

class TestSentenceTransformersBackend:

    def test_unavailable_when_package_missing(self, monkeypatch):
        from pce_core.embeddings.backends import SentenceTransformersBackend
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sentence_transformers" or name.startswith("sentence_transformers."):
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        b = SentenceTransformersBackend(model_name="irrelevant")
        assert b.available is False
        assert b.reason and "sentence_transformers_import_failed" in b.reason


# ---------------------------------------------------------------------------
# OllamaBackend: exercise the HTTP surface with a mocked urlopen.
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
    def read(self):  # noqa: D401
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


class TestOllamaBackend:

    def test_embed_single_roundtrip(self, monkeypatch):
        from pce_core.embeddings.backends import OllamaBackend

        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(json.dumps({
                "embedding": [0.1, 0.2, 0.3, 0.4],
            }).encode("utf-8"))

        monkeypatch.setattr(
            "pce_core.embeddings.backends.urllib.request.urlopen",
            fake_urlopen,
        )

        b = OllamaBackend(model_name="nomic-embed-text")
        assert b.available is True
        vecs = b.embed_batch(["hello"])
        assert vecs == [[0.1, 0.2, 0.3, 0.4]]
        assert captured["url"].endswith("/api/embeddings")
        assert captured["body"]["model"] == "nomic-embed-text"
        assert captured["body"]["prompt"] == "hello"

    def test_connection_error_flips_available(self, monkeypatch):
        from pce_core.embeddings.backends import OllamaBackend
        import urllib.error

        def fake_urlopen(*_a, **_kw):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(
            "pce_core.embeddings.backends.urllib.request.urlopen",
            fake_urlopen,
        )
        b = OllamaBackend(model_name="nomic-embed-text")
        with pytest.raises(ConnectionError):
            b.embed_batch(["x"])
        assert b.available is False
        assert b.reason and "ollama_unreachable" in b.reason


# ---------------------------------------------------------------------------
# OpenAIBackend
# ---------------------------------------------------------------------------

class TestOpenAIBackend:

    def test_unavailable_without_api_key(self, monkeypatch):
        from pce_core.embeddings.backends import OpenAIBackend
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        b = OpenAIBackend()
        assert b.available is False
        assert b.reason == "openai_api_key_missing"

    def test_embed_batch_shape(self, monkeypatch):
        from pce_core.embeddings.backends import OpenAIBackend
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        def fake_urlopen(req, timeout=None):
            return _FakeResp(json.dumps({
                "data": [
                    {"embedding": [0.1, 0.2, 0.3]},
                    {"embedding": [0.4, 0.5, 0.6]},
                ]
            }).encode("utf-8"))

        monkeypatch.setattr(
            "pce_core.embeddings.backends.urllib.request.urlopen",
            fake_urlopen,
        )
        b = OpenAIBackend(model_name="text-embedding-3-small")
        assert b.available is True
        out = b.embed_batch(["a", "b"])
        assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    def test_count_mismatch_raises(self, monkeypatch):
        from pce_core.embeddings.backends import OpenAIBackend
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        def fake_urlopen(req, timeout=None):
            return _FakeResp(json.dumps({
                "data": [{"embedding": [0.1]}],
            }).encode("utf-8"))

        monkeypatch.setattr(
            "pce_core.embeddings.backends.urllib.request.urlopen",
            fake_urlopen,
        )
        b = OpenAIBackend()
        with pytest.raises(ValueError, match="count_mismatch"):
            b.embed_batch(["a", "b"])


# ---------------------------------------------------------------------------
# embed_texts helper
# ---------------------------------------------------------------------------

class TestEmbedTextsHelper:

    def test_returns_none_when_backend_unavailable(self, monkeypatch):
        from pce_core import embeddings
        monkeypatch.setenv("PCE_EMBEDDINGS_BACKEND", "disabled")
        assert embeddings.embed_texts(["hi"]) is None

    def test_returns_vectors_when_backend_available(self, monkeypatch):
        from pce_core import embeddings
        from pce_core.embeddings.backends import NoopBackend

        # Swap in a stub backend that claims to be available. Patching
        # ``get_backend`` directly is cleaner than reaching into the
        # cache since ``embed_texts`` re-resolves the spec on each call.
        stub = mock.MagicMock(spec=NoopBackend())
        stub.available = True
        stub.name = "stub"
        stub.embed_batch.return_value = [[1.0, 0.0]]
        monkeypatch.setattr(embeddings, "get_backend", lambda spec=None: stub)

        assert embeddings.embed_texts(["hi"]) == [[1.0, 0.0]]

    def test_returns_none_when_backend_raises(self, monkeypatch):
        from pce_core import embeddings
        from pce_core.embeddings.backends import NoopBackend

        stub = mock.MagicMock(spec=NoopBackend())
        stub.available = True
        stub.name = "stub"
        stub.embed_batch.side_effect = RuntimeError("kaboom")
        monkeypatch.setattr(embeddings, "get_backend", lambda spec=None: stub)

        assert embeddings.embed_texts(["hi"]) is None
