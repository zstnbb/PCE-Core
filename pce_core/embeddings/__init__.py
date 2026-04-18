# SPDX-License-Identifier: Apache-2.0
"""Pluggable embedding backends (P4.1, TASK-006 §6.1).

Design goals:

- **No required dependency.** The default backend is :class:`NoopBackend`
  which just reports ``available=False``. Everything downstream tolerates
  ``None`` embeddings so the feature ships without forcing a model
  install.
- **Multiple optional backends.** Users can opt into
  ``sentence-transformers`` for a fully-local path (≈ 80 MB model),
  Ollama for a self-hosted HTTP path, or OpenAI for a hosted path.
- **Configurable without a restart.** Selection happens via the
  ``PCE_EMBEDDINGS_BACKEND`` env var or
  ``preferences.embeddings_backend`` in ``app_state.json``, evaluated at
  call time.
- **Fail-open.** If the configured backend fails to load (missing
  package, bad network, …) we log a warning and degrade to the noop
  backend — never raise into the ingest / search hot path.

Backend selection string format::

    <name>[:<model>]

Examples::

    disabled
    sentence_transformers
    sentence_transformers:all-MiniLM-L6-v2
    ollama:nomic-embed-text
    ollama:mxbai-embed-large@http://127.0.0.1:11434
    openai:text-embedding-3-small

See :mod:`pce_core.embeddings.backends` for the concrete implementations.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from .backends import (
    EmbeddingBackend,
    NoopBackend,
    OllamaBackend,
    OpenAIBackend,
    SentenceTransformersBackend,
    parse_spec,
)

logger = logging.getLogger("pce.embeddings")


# Cache the resolved backend so we don't re-import sentence-transformers
# on every search call.
_BACKEND_LOCK = threading.Lock()
_CACHED_BACKEND: Optional[EmbeddingBackend] = None
_CACHED_SPEC: Optional[str] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_backend(spec: Optional[str] = None) -> EmbeddingBackend:
    """Return the configured embedding backend.

    Resolution order for ``spec``:

    1. Explicit ``spec`` argument (used by tests).
    2. ``PCE_EMBEDDINGS_BACKEND`` env var.
    3. ``preferences.embeddings_backend`` in ``app_state.json``.
    4. Default ``"disabled"``.

    The resolved backend is cached until :func:`reset` is called or the
    spec string changes.
    """
    global _CACHED_BACKEND, _CACHED_SPEC

    resolved = (spec if spec is not None else _resolve_spec()) or "disabled"
    with _BACKEND_LOCK:
        if _CACHED_BACKEND is not None and _CACHED_SPEC == resolved:
            return _CACHED_BACKEND
        backend = _build_backend(resolved)
        _CACHED_BACKEND = backend
        _CACHED_SPEC = resolved
        return backend


def reset() -> None:
    """Drop the cached backend so the next call re-resolves the spec.

    Primarily useful for tests and for the (future) dashboard preference
    page that lets users switch backends at runtime.
    """
    global _CACHED_BACKEND, _CACHED_SPEC
    with _BACKEND_LOCK:
        _CACHED_BACKEND = None
        _CACHED_SPEC = None


def embed_texts(texts: list[str]) -> Optional[list[list[float]]]:
    """Convenience: embed a batch of texts with the configured backend.

    Returns ``None`` if the backend is unavailable — callers must handle
    that (semantic search falls back to FTS, backfill skips the batch).
    """
    backend = get_backend()
    if not backend.available:
        return None
    try:
        return backend.embed_batch(texts)
    except Exception:
        logger.exception(
            "embedding backend %s failed on batch of %d texts",
            backend.name, len(texts),
        )
        return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _resolve_spec() -> Optional[str]:
    env = os.environ.get("PCE_EMBEDDINGS_BACKEND", "").strip()
    if env:
        return env
    try:
        from .. import app_state
        prefs = app_state.load_state().get("preferences") or {}
        pref = prefs.get("embeddings_backend")
        if isinstance(pref, str) and pref.strip():
            return pref.strip()
    except Exception:
        logger.debug("app_state probe failed", exc_info=True)
    return None


def _build_backend(spec: str) -> EmbeddingBackend:
    """Map a spec string to a concrete backend, never raising."""
    name, model, endpoint = parse_spec(spec)
    try:
        if name in ("", "disabled", "none", "off"):
            return NoopBackend()
        if name in ("sentence_transformers", "st", "sbert"):
            return SentenceTransformersBackend(
                model_name=model or "all-MiniLM-L6-v2",
            )
        if name == "ollama":
            return OllamaBackend(
                model_name=model or "nomic-embed-text",
                endpoint=endpoint or "http://127.0.0.1:11434",
            )
        if name == "openai":
            return OpenAIBackend(
                model_name=model or "text-embedding-3-small",
                endpoint=endpoint,
            )
    except Exception as exc:
        logger.warning(
            "embedding backend %r failed to load (%s: %s) — "
            "falling back to disabled",
            spec, type(exc).__name__, exc,
        )
        return NoopBackend(reason=f"backend_load_failed: {type(exc).__name__}")

    logger.warning("unknown embedding backend %r — disabling", spec)
    return NoopBackend(reason=f"unknown_backend:{name}")


__all__ = [
    "EmbeddingBackend",
    "NoopBackend",
    "OllamaBackend",
    "OpenAIBackend",
    "SentenceTransformersBackend",
    "embed_texts",
    "get_backend",
    "reset",
]
