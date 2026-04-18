# SPDX-License-Identifier: Apache-2.0
"""Concrete embedding backends.

Each backend is a self-contained class that knows how to turn a batch of
texts into vectors. They all share the same :class:`EmbeddingBackend`
protocol so :mod:`pce_core.embeddings` can swap them out behind a single
factory.

A backend's job is narrow:

- Declare its ``name``, ``model_name``, and embedding ``dim``.
- Expose ``available`` as a cheap health check (does the package import?
  is the endpoint reachable?). ``False`` opts us into the FTS-only path
  without a runtime error.
- Provide ``embed_batch(texts)`` that returns one vector per input, all
  of length ``dim``. Raising is allowed — the factory catches and logs.

No backend performs I/O on import — heavy work is deferred to the first
``embed_batch`` call so that importing :mod:`pce_core.embeddings` stays
cheap even when the user hasn't opted in.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger("pce.embeddings.backends")


DEFAULT_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class EmbeddingBackend(Protocol):
    """All embedding backends implement this narrow surface."""

    name: str
    model_name: str
    dim: int
    available: bool
    reason: Optional[str]  # why unavailable, for diagnostics

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------

def parse_spec(spec: str) -> tuple[str, str, str]:
    """Parse a backend spec into ``(name, model, endpoint)``.

    Grammar::

        <name>[:<model>][@<endpoint>]

    Either segment is optional; missing parts return ``""``.
    """
    spec = (spec or "").strip()
    if not spec:
        return ("", "", "")

    # Split off ``@endpoint`` first so that endpoints with colons (e.g.
    # ``http://host:11434``) don't get confused with the model separator.
    endpoint = ""
    if "@" in spec:
        spec, endpoint = spec.split("@", 1)

    if ":" in spec:
        name, model = spec.split(":", 1)
    else:
        name, model = spec, ""

    return (name.strip().lower(), model.strip(), endpoint.strip())


# ---------------------------------------------------------------------------
# Noop (default) backend
# ---------------------------------------------------------------------------

@dataclass
class NoopBackend:
    """No-op backend used whenever embeddings are disabled.

    Semantic search short-circuits to FTS when :attr:`available` is
    ``False`` — see ``pce_core.semantic_search``.
    """

    name: str = "disabled"
    model_name: str = ""
    dim: int = 0
    available: bool = False
    reason: Optional[str] = "embeddings_disabled"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


# ---------------------------------------------------------------------------
# sentence-transformers (local, fully offline)
# ---------------------------------------------------------------------------

class SentenceTransformersBackend:
    """Local SBERT encoder via ``sentence-transformers``.

    Requires ``pip install sentence-transformers``; the first call
    downloads the model (~80 MB for all-MiniLM-L6-v2) to Hugging Face's
    default cache dir.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.name = "sentence_transformers"
        self.model_name = model_name
        self._model = None
        self._dim: Optional[int] = None
        self.reason: Optional[str] = None

        # Defer the actual import until load(). A failed import should
        # degrade gracefully, not crash every downstream user.
        try:
            import sentence_transformers  # noqa: F401
            self.available = True
        except Exception as exc:
            self.available = False
            self.reason = f"sentence_transformers_import_failed: {type(exc).__name__}"

    @property
    def dim(self) -> int:
        if self._dim is not None:
            return self._dim
        if not self.available:
            return 0
        self._load()
        return self._dim or 0

    def _load(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        logger.info("loading sentence-transformers model %r", self.model_name)
        self._model = SentenceTransformer(self.model_name)
        # ``get_sentence_embedding_dimension`` is the canonical way.
        try:
            self._dim = int(self._model.get_sentence_embedding_dimension())
        except Exception:
            # Fallback: encode a tiny string and measure.
            sample = self._model.encode(["hi"], show_progress_bar=False)
            self._dim = len(sample[0])

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        assert self._model is not None
        vectors = self._model.encode(
            list(texts),
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        # ``encode`` returns numpy arrays — coerce to plain Python lists
        # so callers never have to import numpy.
        return [[float(x) for x in v] for v in vectors]


# ---------------------------------------------------------------------------
# Ollama (local HTTP)
# ---------------------------------------------------------------------------

class OllamaBackend:
    """Ollama ``/api/embeddings`` backend.

    Works with any model Ollama has pulled (e.g. ``nomic-embed-text``,
    ``mxbai-embed-large``, ``all-minilm``). No Python dep required — we
    use ``urllib`` to keep the footprint zero.
    """

    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        endpoint: str = "http://127.0.0.1:11434",
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.name = "ollama"
        self.model_name = model_name
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._dim: Optional[int] = None
        self.reason: Optional[str] = None
        # We don't pre-flight Ollama at init to avoid blocking on a
        # machine where it's intentionally off. The first embed call will
        # surface the real status (and log a warning via the factory).
        self.available = True

    @property
    def dim(self) -> int:
        if self._dim is not None:
            return self._dim
        # Probe the endpoint with a tiny embedding to learn the shape.
        try:
            vec = self._embed_single("dim probe")
            self._dim = len(vec)
        except Exception:
            # Leave dim as 0 so callers know something is wrong without
            # us crashing here. ``available`` will be flipped on the next
            # real call.
            self._dim = 0
        return self._dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Ollama's embeddings endpoint takes one prompt per call. Batch
        # by looping — the local HTTP cost is negligible.
        return [self._embed_single(t) for t in texts]

    def _embed_single(self, text: str) -> list[float]:
        body = json.dumps({
            "model": self.model_name,
            "prompt": text,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            self.available = False
            self.reason = f"ollama_unreachable: {exc}"
            raise ConnectionError(self.reason) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"ollama_bad_json: {exc}") from exc
        emb = payload.get("embedding")
        if not isinstance(emb, list):
            raise ValueError("ollama_missing_embedding_field")
        return [float(x) for x in emb]


# ---------------------------------------------------------------------------
# OpenAI (hosted)
# ---------------------------------------------------------------------------

class OpenAIBackend:
    """OpenAI ``/v1/embeddings`` backend. Requires ``OPENAI_API_KEY``.

    Supports the ``text-embedding-3-*`` family (defaults to ``small``).
    Any compatible endpoint (e.g. Azure OpenAI, OpenRouter) works by
    pointing ``endpoint`` at it.
    """

    _DEFAULT_DIMS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.name = "openai"
        self.model_name = model_name
        self.endpoint = (endpoint or os.environ.get("OPENAI_API_BASE")
                         or "https://api.openai.com/v1").rstrip("/")
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        self.reason: Optional[str] = None
        if not self._api_key:
            self.available = False
            self.reason = "openai_api_key_missing"
        else:
            self.available = True
        self._dim: Optional[int] = self._DEFAULT_DIMS.get(model_name)

    @property
    def dim(self) -> int:
        if self._dim is not None:
            return self._dim
        # Unknown model — probe once.
        try:
            vec = self.embed_batch(["dim probe"])[0]
            self._dim = len(vec)
        except Exception:
            self._dim = 0
        return self._dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.available:
            raise RuntimeError(self.reason or "openai_backend_unavailable")
        body = json.dumps({
            "model": self.model_name,
            "input": list(texts),
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/embeddings",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError) as exc:
            self.available = False
            self.reason = f"openai_unreachable: {exc}"
            raise ConnectionError(self.reason) from exc
        payload = json.loads(raw.decode("utf-8"))
        data = payload.get("data") or []
        if len(data) != len(texts):
            raise ValueError(
                f"openai_embedding_count_mismatch: want={len(texts)} got={len(data)}"
            )
        return [[float(x) for x in d["embedding"]] for d in data]


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "EmbeddingBackend",
    "NoopBackend",
    "OllamaBackend",
    "OpenAIBackend",
    "SentenceTransformersBackend",
    "parse_spec",
]
