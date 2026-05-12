# SPDX-License-Identifier: Apache-2.0
"""pce_test_conductor.llm_repair — opt-in LLM-refined selector repair.

P5.C.4.3 deliverable per HANDOFF-META-PIPELINE-KICKOFF §4.P5.C.4 step 3.
Closes the second half of ADR-011 G9 (LLM auto-fix proposal): the
conductor's heuristic `propose_patch` (P5.C.2) covers the 3 most-common
failure shapes mechanically; for ``UI_SELECTOR_MISS`` and
``URL_PATTERN_DRIFT`` cases that fall outside those templates, this
module asks an LLM (Anthropic or OpenAI) to propose a YAML patch.

Hard constraints (per ADR-019 §3.1 contract D):

1. **Opt-in only**: the LLM is never called unless the caller passes
   ``dry_run=False`` AND provides an API key (via env var or kwarg).
   Default behaviour returns a deterministic stub from ``_call_mock``
   so the conductor + tests run offline without paid API access.
2. **No patch application**: this module returns a ``LLMRepairResult``
   containing a YAML unified diff + rationale + confidence. The agent
   (Cascade / Claude Code) applies the diff via its own edit tool —
   the conductor never writes to disk.
3. **No API key persistence**: keys come from ``ANTHROPIC_API_KEY`` /
   ``OPENAI_API_KEY`` env vars or kwargs; never logged, never written.
4. **stdlib HTTP only**: uses ``urllib.request`` not ``requests`` /
   ``httpx`` — keeps PCE Core's dep footprint minimal and avoids
   pinning a third-party client version.

References:
  - ADR-011 G9 (LLM auto-fix proposal)
  - ADR-017 §3.5 (propose_patch contract — patches as data)
  - ADR-019 §3.1.D (patches-as-data, never auto-applied)
  - HANDOFF-META-PIPELINE-KICKOFF §4.P5.C.4 step 3
"""

from __future__ import annotations

import enum
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("pce.test_conductor.llm_repair")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Anthropic Messages API endpoint.
ANTHROPIC_ENDPOINT: str = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION: str = "2023-06-01"
ANTHROPIC_DEFAULT_MODEL: str = "claude-sonnet-4-5"
ANTHROPIC_DEFAULT_MAX_TOKENS: int = 4096

#: OpenAI Chat Completions API endpoint.
OPENAI_ENDPOINT: str = "https://api.openai.com/v1/chat/completions"
OPENAI_DEFAULT_MODEL: str = "gpt-5-mini"
OPENAI_DEFAULT_MAX_TOKENS: int = 4096

#: HTTP timeout in seconds. Larger than nominal because LLM responses
#: for selector repair can run 5-15s end-to-end on busy quotas.
HTTP_TIMEOUT_S: float = 30.0


class LLMProvider(str, enum.Enum):
    """Provider key.

    ``AUTO`` picks ANTHROPIC vs OPENAI based on the target_id naming
    convention (``*claude*`` / ``*anthropic*`` → ANTHROPIC; ``*chatgpt*`` /
    ``*openai*`` → OPENAI; everything else → MOCK).
    ``MOCK`` is the deterministic offline path used by CI + unit tests.
    """

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    MOCK = "mock"
    AUTO = "auto"


@dataclass
class LLMRepairResult:
    """Structured output of ``repair_selector``.

    The ``proposed_yaml_diff`` is a unified diff string (compatible
    with ``git apply``) targeting the YAML manifest under
    ``pce_core/adapters/<site>.yaml`` — not the Python adapter file.

    ``confidence`` is in [0.0, 1.0]. Heuristic mock outputs 0.5 by
    default; real LLM outputs parse the model's self-rating from the
    response text (falls back to 0.6).
    """

    proposed_yaml_diff: str
    rationale: str
    confidence: float
    provider: LLMProvider
    model: str
    target_id: str
    case_id: Optional[str] = None
    dry_run: bool = True
    error: Optional[str] = None
    raw_response: Optional[str] = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed_yaml_diff": self.proposed_yaml_diff,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "provider": self.provider.value,
            "model": self.model,
            "target_id": self.target_id,
            "case_id": self.case_id,
            "dry_run": self.dry_run,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def select_provider_for_target(target_id: str) -> LLMProvider:
    """Heuristic dispatcher based on target_id naming.

    Examples:
        - ``browser_chatgpt`` → OPENAI
        - ``browser_claude`` / ``desktop_claude_chat`` → ANTHROPIC
        - ``mcp_filesystem`` / ``cli_claude_code`` → MOCK (no clear LLM owner)

    Returns MOCK for ambiguous IDs so the caller doesn't accidentally
    hit a billable API with arbitrary input.
    """
    t = target_id.lower()
    if "chatgpt" in t or "openai" in t or "gpt" in t.split("_"):
        return LLMProvider.OPENAI
    if "claude_chat" in t or t.endswith("_claude") or "anthropic" in t:
        return LLMProvider.ANTHROPIC
    if "gemini" in t or "google" in t:
        # No Google LLM provider wired yet; fall back to mock.
        return LLMProvider.MOCK
    return LLMProvider.MOCK


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

REPAIR_SYSTEM_INSTRUCTION: str = """\
You are a selector-drift repair agent for the PCE (Personal Capture Engine)
Test Conductor. The user will give you (1) a YAML adapter manifest that
describes how to drive a web AI chat site (ChatGPT / Claude / Gemini /
etc.) and (2) evidence of a failed test run where the current YAML
selectors no longer match the live DOM. Propose a minimal YAML edit that
restores the missing selector(s).

Constraints (the user's MCP conductor enforces these — do not violate):
- Output ONLY a YAML unified diff (``--- before / +++ after`` shape) plus
  a short rationale paragraph and a confidence rating in [0.0, 1.0].
- Do not include any selector you cannot justify from the evidence.
- Prefer adding new alternatives to existing lists over replacing
  working selectors — fallback ordering is part of the contract.
- Comments in YAML must stay in English; use UTF-8 for any non-ASCII
  selector text. Do not invent localised labels.

Output format (strict):

```
RATIONALE: <one paragraph, ≤ 4 sentences>
CONFIDENCE: <float, 0.0 to 1.0>
DIFF:
```diff
<unified diff content here>
```
```
"""


def build_repair_prompt(
    *,
    target_id: str,
    case_id: Optional[str],
    failure_kind: str,
    failure_hint: str,
    stderr_excerpt: str,
    adapter_yaml_text: str,
) -> str:
    """Compose the user-side prompt for the LLM call.

    Trims long evidence to keep the payload under ~8K tokens. The
    full stderr is available in the run record for the human reviewer
    after the proposal lands.
    """
    excerpt = (stderr_excerpt or "")[-1500:]
    yaml_clip = (adapter_yaml_text or "")[:6000]
    return (
        f"Target: `{target_id}`"
        + (f"  ·  Case: `{case_id}`" if case_id else "")
        + "\n"
        f"FailureKind: `{failure_kind}`\n"
        f"FailureHint: {failure_hint}\n"
        "\n"
        "Evidence (tail of stderr):\n"
        "```\n"
        f"{excerpt}\n"
        "```\n"
        "\n"
        "Current YAML manifest (truncated to first 6 KB):\n"
        "```yaml\n"
        f"{yaml_clip}\n"
        "```\n"
        "\n"
        "Propose a minimal YAML diff that restores the missing selector(s). "
        "Follow the output format exactly."
    )


# ---------------------------------------------------------------------------
# Provider callers
# ---------------------------------------------------------------------------

def _post_json(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float = HTTP_TIMEOUT_S,
) -> tuple[int, str]:
    """Minimal stdlib POST that returns ``(status_code, body_text)``.

    Wrapped so tests can monkeypatch this single function instead of
    the whole urllib machinery.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        return int(exc.code), body
    except urllib.error.URLError as exc:
        return 0, f"URLError: {exc.reason!r}"


def _call_anthropic(
    *,
    prompt: str,
    api_key: str,
    model: str = ANTHROPIC_DEFAULT_MODEL,
    max_tokens: int = ANTHROPIC_DEFAULT_MAX_TOKENS,
) -> tuple[int, str]:
    """Call Anthropic Messages API. Returns ``(status, response_text)``."""
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": REPAIR_SYSTEM_INSTRUCTION,
        "messages": [{"role": "user", "content": prompt}],
    }
    return _post_json(url=ANTHROPIC_ENDPOINT, headers=headers, payload=payload)


def _call_openai(
    *,
    prompt: str,
    api_key: str,
    model: str = OPENAI_DEFAULT_MODEL,
    max_tokens: int = OPENAI_DEFAULT_MAX_TOKENS,
) -> tuple[int, str]:
    """Call OpenAI Chat Completions API. Returns ``(status, response_text)``."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": REPAIR_SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
    }
    return _post_json(url=OPENAI_ENDPOINT, headers=headers, payload=payload)


def _call_mock(*, prompt: str, target_id: str) -> tuple[int, str]:
    """Deterministic offline provider used when no API key is available.

    Returns a stub diff that targets a placeholder selector group with
    a generic note that the agent should investigate via DOM. The
    confidence is intentionally low so the human reviewer treats this
    as a starting hint, not a finished patch.
    """
    diff = (
        f"--- a/pce_core/adapters/{target_id.removeprefix('browser_')}.yaml\n"
        f"+++ b/pce_core/adapters/{target_id.removeprefix('browser_')}.yaml\n"
        f"@@ selectors block @@\n"
        f"   input:\n"
        f"     - '#prompt-textarea'         # current\n"
        f"+    - '[data-testid=\"composer-input\"]'   # mock proposal: P5.C.4.3 placeholder\n"
        f"+    - '[contenteditable=\"true\"][data-pce-mock=\"true\"]'\n"
    )
    body = (
        "RATIONALE: Mock provider — no LLM API key available. "
        "The diff above is a placeholder; the agent should open the failing tab in DevTools, "
        "locate the new composer input element, and replace the placeholder lines with real selectors. "
        "Run `verify_patch` after editing to confirm.\n"
        "CONFIDENCE: 0.25\n"
        "DIFF:\n"
        "```diff\n"
        + diff +
        "```\n"
    )
    return 200, body


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_RATIONALE_RE = re.compile(r"^\s*RATIONALE\s*:\s*(.+?)(?=^\s*(?:CONFIDENCE|DIFF)\s*:)",
                           re.IGNORECASE | re.MULTILINE | re.DOTALL)
_CONFIDENCE_RE = re.compile(r"^\s*CONFIDENCE\s*:\s*([0-9.]+)\s*$",
                            re.IGNORECASE | re.MULTILINE)
_DIFF_BLOCK_RE = re.compile(r"```diff\s*\n(.*?)\n```", re.DOTALL)


def _extract_text_from_provider_response(provider: LLMProvider, body: str) -> str:
    """Pull the assistant text out of the provider's JSON wrapper."""
    if provider == LLMProvider.MOCK:
        return body
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body
    if provider == LLMProvider.ANTHROPIC:
        # {"content": [{"type": "text", "text": "..."}], ...}
        for block in data.get("content") or []:
            if block.get("type") == "text":
                return str(block.get("text") or "")
        return body
    if provider == LLMProvider.OPENAI:
        # {"choices": [{"message": {"content": "..."}}], ...}
        for choice in data.get("choices") or []:
            msg = (choice or {}).get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return content
            # OpenAI may return content as list of dicts in newer schema
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                return "\n".join(parts)
        return body
    return body


def parse_repair_response(
    text: str,
    *,
    fallback_confidence: float = 0.6,
) -> tuple[str, float, str]:
    """Parse the LLM response into ``(diff, confidence, rationale)``.

    Tolerates minor format drift: missing CONFIDENCE falls back to
    ``fallback_confidence``; missing RATIONALE returns empty string;
    missing DIFF returns empty string (caller treats as failure).
    """
    rationale_match = _RATIONALE_RE.search(text)
    rationale = rationale_match.group(1).strip() if rationale_match else ""

    conf_match = _CONFIDENCE_RE.search(text)
    confidence = fallback_confidence
    if conf_match:
        try:
            confidence = float(conf_match.group(1))
            confidence = max(0.0, min(1.0, confidence))
        except ValueError:
            confidence = fallback_confidence

    diff_match = _DIFF_BLOCK_RE.search(text)
    diff = diff_match.group(1).strip() if diff_match else ""

    return diff, confidence, rationale


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------

def repair_selector(
    *,
    target_id: str,
    failure_kind: str,
    failure_hint: str,
    stderr_excerpt: str,
    adapter_yaml_text: str,
    case_id: Optional[str] = None,
    provider: LLMProvider = LLMProvider.AUTO,
    api_key: Optional[str] = None,
    dry_run: bool = True,
    model: Optional[str] = None,
) -> LLMRepairResult:
    """Top-level repair entry. **Does not write to disk.**

    Args:
      target_id: e.g. ``browser_chatgpt``
      failure_kind: one of the ``FailureKind`` enum names
        (``UI_SELECTOR_MISS`` is the primary use case; other kinds
        fall through to the mock provider since their patches are
        already heuristic).
      failure_hint: short human string from the classifier.
      stderr_excerpt: tail of the run's stderr (truncated to last 1500 chars).
      adapter_yaml_text: current YAML manifest body (full file text).
      case_id: optional case identifier for context.
      provider: explicit override of the dispatcher; default is AUTO.
      api_key: optional override. If None, env vars are consulted.
      dry_run: when True (default), never makes an HTTP call —
        returns ``_call_mock`` output instead.
      model: override the provider's default model name.

    Returns:
      ``LLMRepairResult`` with the proposed YAML diff + metadata.
      ``result.error`` is set when an HTTP error occurred.
    """
    effective = provider if provider != LLMProvider.AUTO else select_provider_for_target(target_id)

    prompt = build_repair_prompt(
        target_id=target_id, case_id=case_id,
        failure_kind=failure_kind, failure_hint=failure_hint,
        stderr_excerpt=stderr_excerpt, adapter_yaml_text=adapter_yaml_text,
    )

    # Force MOCK on dry_run regardless of effective provider.
    if dry_run:
        status, body = _call_mock(prompt=prompt, target_id=target_id)
        raw_text = body  # mock returns pre-formatted text directly
        diff, confidence, rationale = parse_repair_response(raw_text)
        return LLMRepairResult(
            proposed_yaml_diff=diff, rationale=rationale, confidence=confidence,
            provider=LLMProvider.MOCK, model="mock-deterministic",
            target_id=target_id, case_id=case_id, dry_run=True,
            raw_response=raw_text,
        )

    # Non-dry-run: dispatch to real provider, fallback to MOCK if no key.
    key = api_key
    if effective == LLMProvider.ANTHROPIC:
        key = key or os.environ.get("ANTHROPIC_API_KEY")
    elif effective == LLMProvider.OPENAI:
        key = key or os.environ.get("OPENAI_API_KEY")
    elif effective == LLMProvider.MOCK:
        key = None

    if effective != LLMProvider.MOCK and not key:
        logger.warning(
            "repair_selector: no API key for %s — falling back to MOCK",
            effective.value,
        )
        effective = LLMProvider.MOCK

    if effective == LLMProvider.ANTHROPIC:
        chosen_model = model or ANTHROPIC_DEFAULT_MODEL
        status, body = _call_anthropic(
            prompt=prompt, api_key=key or "", model=chosen_model,
        )
    elif effective == LLMProvider.OPENAI:
        chosen_model = model or OPENAI_DEFAULT_MODEL
        status, body = _call_openai(
            prompt=prompt, api_key=key or "", model=chosen_model,
        )
    else:  # MOCK
        chosen_model = "mock-deterministic"
        status, body = _call_mock(prompt=prompt, target_id=target_id)

    error_msg: Optional[str] = None
    if status >= 400 or status == 0:
        error_msg = f"provider HTTP {status}: {body[:300]}"
        logger.warning("repair_selector: %s — falling back to MOCK", error_msg)
        # Salvage: produce a mock proposal so the caller still has something
        # to look at, but flag the error.
        _, mock_body = _call_mock(prompt=prompt, target_id=target_id)
        diff, confidence, rationale = parse_repair_response(mock_body)
        return LLMRepairResult(
            proposed_yaml_diff=diff, rationale=rationale, confidence=confidence,
            provider=LLMProvider.MOCK, model="mock-fallback",
            target_id=target_id, case_id=case_id, dry_run=False,
            error=error_msg, raw_response=body,
        )

    raw_text = _extract_text_from_provider_response(effective, body)
    diff, confidence, rationale = parse_repair_response(raw_text)
    return LLMRepairResult(
        proposed_yaml_diff=diff, rationale=rationale, confidence=confidence,
        provider=effective, model=chosen_model,
        target_id=target_id, case_id=case_id, dry_run=False,
        raw_response=raw_text,
    )


__all__ = [
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_ENDPOINT",
    "LLMProvider",
    "LLMRepairResult",
    "OPENAI_DEFAULT_MODEL",
    "OPENAI_ENDPOINT",
    "REPAIR_SYSTEM_INSTRUCTION",
    "build_repair_prompt",
    "parse_repair_response",
    "repair_selector",
    "select_provider_for_target",
]
