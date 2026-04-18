# SPDX-License-Identifier: Apache-2.0
"""PCE Core – Privacy middleware: header & body redaction (UCS §6.1, P5.A-10).

Ensures sensitive authentication material never reaches the database in
clear text. Two layers of defence:

1. **Header redaction** — values of ``Authorization`` / ``Cookie`` /
   ``Set-Cookie`` / ``X-Api-Key`` / ``Api-Key`` / ``Proxy-Authorization``
   (see :data:`pce_core.config.REDACT_HEADERS`) are replaced with the
   literal string ``"REDACTED"``.
2. **Body-level secret scrubbing** — regex-based replace of known secret
   shapes (Bearer tokens, JWT ``eyJ…`` payloads, OpenAI ``sk-…`` and
   Anthropic ``sk-ant-…`` API keys, Google ``AIza…`` API keys, GitHub
   ``gh[pousr]_…`` personal / OAuth tokens, Stripe ``(sk|pk)_live|test``
   keys). Bodies frequently contain such material inline — e.g. an LLM
   that paste-echoes the user's request — so header-only redaction
   cannot be the only safeguard.

User-extensible patterns: every comma-separated regex in the environment
variable ``PCE_REDACT_PATTERNS`` is appended to the default list at
module import. This lets operators scrub corporate SSO tokens / internal
bearer formats without a code change.

The canonical entry point is :class:`PrivacyGuard` / :func:`get_privacy_guard`
(FastAPI dependency). ``redact_headers`` / ``redact_headers_json`` /
``safe_body_text`` remain exported for backward compat — they power
``pce_proxy/addon.py`` and older call sites.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Pattern

from .config import REDACT_HEADERS

logger = logging.getLogger("pce.redact")


# ---------------------------------------------------------------------------
# Header redaction (legacy surface, unchanged behaviour)
# ---------------------------------------------------------------------------

def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with sensitive values replaced."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in REDACT_HEADERS:
            out[k] = "REDACTED"
        else:
            out[k] = v
    return out


def redact_headers_json(headers: dict[str, str]) -> str:
    """Redact and return a JSON string ready for storage."""
    return json.dumps(redact_headers(headers), ensure_ascii=False)


def redact_headers_json_str(headers_json: Optional[str]) -> str:
    """Parse a JSON string of headers, redact, and re-serialise.

    Accepts ``None`` / ``""`` / malformed JSON without raising — returns
    the input unchanged so downstream code (which often re-stores the
    string) doesn't have to special-case these shapes.
    """
    if not headers_json:
        return headers_json or "{}"
    try:
        parsed = json.loads(headers_json)
    except (TypeError, ValueError):
        # Not JSON — can't safely redact header values, leave alone.
        return headers_json
    if not isinstance(parsed, dict):
        return headers_json
    return redact_headers_json({str(k): str(v) for k, v in parsed.items()})


def safe_body_text(raw: bytes, limit: int = 2 * 1024 * 1024) -> tuple[str, str]:
    """Decode body bytes, truncating if excessively large.

    Returns (text, format) where format is 'json' or 'text'.
    """
    if not raw:
        return "", "text"

    text = raw[:limit].decode("utf-8", errors="replace")

    # Quick check: does it look like JSON?
    stripped = text.lstrip()
    fmt = "json" if stripped.startswith(("{", "[")) else "text"
    return text, fmt


# ---------------------------------------------------------------------------
# Body-level secret scrubbing (P5.A-10)
# ---------------------------------------------------------------------------

# Each default pattern is (regex, replacement). ``(?i)`` is applied where
# header prefixes (``Bearer``) commonly differ in case; the token bodies
# themselves are already case-normalised by their issuers.
#
# Ordering matters only for mutually-overlapping patterns — e.g. the
# ``sk-ant-`` Anthropic rule must land before the generic ``sk-`` OpenAI
# rule or every Anthropic key would match the shorter prefix first.
_DEFAULT_SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    # Bearer / Basic auth in Authorization-style strings leaking into bodies
    (r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/=]{8,}", "Bearer [REDACTED]"),
    (r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}", "Basic [REDACTED]"),
    # JWT — three base64url segments separated by dots, starting with eyJ
    (r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}", "[REDACTED_JWT]"),
    # Anthropic API keys (must precede OpenAI sk- rule due to prefix overlap)
    (r"\bsk-ant-[A-Za-z0-9_\-]{20,}", "[REDACTED_ANTHROPIC_KEY]"),
    # OpenAI-style secret keys: sk-proj-*, sk-admin-*, plain sk-*
    (r"\bsk-(?:proj-|admin-|live-|test-)?[A-Za-z0-9_\-]{20,}", "[REDACTED_OPENAI_KEY]"),
    # Google API keys
    (r"\bAIza[A-Za-z0-9_\-]{30,}", "[REDACTED_GOOGLE_API_KEY]"),
    # GitHub tokens (classic ghp_, fine-grained github_pat_, oauth gho_,
    # user access ghu_, refresh ghr_, server ghs_)
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}", "[REDACTED_GITHUB_TOKEN]"),
    (r"\bgithub_pat_[A-Za-z0-9_]{30,}", "[REDACTED_GITHUB_TOKEN]"),
    # Stripe
    (r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}", "[REDACTED_STRIPE_KEY]"),
    # AWS access key IDs (prefixed AKIA, ASIA, AGPA, AIDA, AROA, AIPA …)
    (r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA)[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]"),
    # Slack tokens
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "[REDACTED_SLACK_TOKEN]"),
)


def _load_env_patterns() -> list[tuple[str, str]]:
    """Parse ``PCE_REDACT_PATTERNS`` (comma-separated regex strings).

    Invalid regex entries are logged and skipped so a typo in the env var
    can't brick ingest. Each custom pattern gets replaced with a generic
    ``[REDACTED_CUSTOM]`` token.
    """
    raw = os.environ.get("PCE_REDACT_PATTERNS", "")
    if not raw.strip():
        return []
    patterns: list[tuple[str, str]] = []
    for candidate in raw.split(","):
        rx = candidate.strip()
        if not rx:
            continue
        try:
            re.compile(rx)
        except re.error as exc:
            logger.warning(
                "PCE_REDACT_PATTERNS: dropping invalid regex %r (%s)", rx, exc,
            )
            continue
        patterns.append((rx, "[REDACTED_CUSTOM]"))
    return patterns


@dataclass
class PrivacyGuard:
    """Bundle of compiled redaction rules applied to headers + body text.

    Instances are cheap to construct (regex pre-compiled once) and
    immutable in practice. The global :func:`get_privacy_guard` is the
    FastAPI dependency; tests can pass their own instance via
    ``app.dependency_overrides``.
    """

    patterns: tuple[Pattern[str], ...]
    replacements: tuple[str, ...]
    redact_headers: bool = True

    @classmethod
    def from_rules(
        cls,
        rules: Iterable[tuple[str, str]],
        *,
        redact_headers: bool = True,
    ) -> "PrivacyGuard":
        compiled: list[Pattern[str]] = []
        replacements: list[str] = []
        for pattern, replacement in rules:
            compiled.append(re.compile(pattern))
            replacements.append(replacement)
        return cls(
            patterns=tuple(compiled),
            replacements=tuple(replacements),
            redact_headers=redact_headers,
        )

    @classmethod
    def default(cls) -> "PrivacyGuard":
        """Build the canonical guard (defaults + ``PCE_REDACT_PATTERNS``)."""
        rules: list[tuple[str, str]] = list(_DEFAULT_SECRET_PATTERNS)
        rules.extend(_load_env_patterns())
        return cls.from_rules(rules)

    def scrub_body(self, text: Optional[str]) -> str:
        """Apply every secret pattern to ``text``. Empty input → ``""``."""
        if not text:
            return text or ""
        scrubbed = text
        for pattern, replacement in zip(self.patterns, self.replacements):
            scrubbed = pattern.sub(replacement, scrubbed)
        return scrubbed

    def scrub_headers_json(self, headers_json: Optional[str]) -> str:
        """Redact sensitive header values inside a JSON string payload."""
        if not self.redact_headers:
            return headers_json or "{}"
        return redact_headers_json_str(headers_json)

    def apply(
        self,
        headers_json: Optional[str],
        body_text: Optional[str],
    ) -> tuple[str, str]:
        """One-shot helper: redact both at once.

        Returns ``(headers_json, body_text)`` as strings safe to persist.
        """
        return self.scrub_headers_json(headers_json), self.scrub_body(body_text)


# A single process-wide guard — rebuilt at module import so
# ``PCE_REDACT_PATTERNS`` changes take effect on process start.
_DEFAULT_GUARD: PrivacyGuard = PrivacyGuard.default()


def get_privacy_guard() -> PrivacyGuard:
    """FastAPI dependency yielding the active :class:`PrivacyGuard`.

    Keeping this a plain function (not a class constructor) lets callers
    swap the guard in tests via::

        from pce_core.redact import get_privacy_guard
        app.dependency_overrides[get_privacy_guard] = lambda: my_test_guard
    """
    return _DEFAULT_GUARD


def set_default_guard(guard: PrivacyGuard) -> None:
    """Replace the process-wide guard. Intended for tests / CLI overrides."""
    global _DEFAULT_GUARD
    _DEFAULT_GUARD = guard


def redact_body_secrets(text: Optional[str]) -> str:
    """Module-level shortcut for the current default guard's body scrub."""
    return _DEFAULT_GUARD.scrub_body(text)
