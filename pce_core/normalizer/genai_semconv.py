# SPDX-License-Identifier: Apache-2.0
"""OpenTelemetry GenAI / OpenLLMetry attribute aliasing (P4.3).

PCE already emits OpenInference-flavoured attribute dicts on every OTLP
span (see ``pce_core.normalizer.openinference_mapper``). Most of the
hosted LLM-observability backends (Phoenix, Langfuse, Arize, Weights &
Biases Traces …) consume OpenInference natively.

But a growing cohort (OpenTelemetry-native backends, OpenLLMetry's
Traceloop, Grafana's LLM panel, Datadog APM's GenAI view) instead
consume the OTel GenAI semantic conventions (``gen_ai.*``) and/or the
OpenLLMetry flavour that extends them. See:

- OTel GenAI semconv:
  https://opentelemetry.io/docs/specs/semconv/gen-ai/
- OpenLLMetry conventions:
  https://github.com/traceloop/openllmetry/blob/main/docs/content/en/docs/semantic-conventions/generation.md

This module converts an OI attribute dict into a set of ``gen_ai.*``
aliases so one PCE span can satisfy both ecosystems. It is intentionally
**additive** — the OI keys are never removed, and the alias set is
stable even if the underlying OI mapper evolves.

Selection is controlled by ``PCE_OTEL_GENAI_SEMCONV``:

- ``both`` (default) — emit OI **and** gen_ai aliases
- ``only``           — emit only gen_ai (strip the OI keys)
- ``off``            — do nothing (legacy OI-only behaviour)

Attribute mapping summary
-------------------------

=================================================   ================================================
OpenInference key                                   OTel GenAI / OpenLLMetry alias
=================================================   ================================================
``openinference.span.kind``                         ``gen_ai.operation.name`` ("chat" for LLM/CHAT)
``llm.system`` / ``llm.provider``                   ``gen_ai.system``
``llm.model_name``                                  ``gen_ai.request.model`` **and** ``gen_ai.response.model``
``llm.token_count.prompt``                          ``gen_ai.usage.input_tokens`` (OpenLLMetry: ``llm.usage.prompt_tokens``)
``llm.token_count.completion``                      ``gen_ai.usage.output_tokens``
``llm.token_count.total``                           ``gen_ai.usage.total_tokens``
``session.id``                                      ``gen_ai.conversation.id``
``user.id``                                         ``gen_ai.user.id``
``input.value``                                     ``gen_ai.prompt``  (stringified JSON)
``output.value``                                    ``gen_ai.completion``
``llm.input_messages.{i}.message.role``             ``gen_ai.prompt.{i}.role``
``llm.input_messages.{i}.message.content``          ``gen_ai.prompt.{i}.content``
``llm.output_messages.{i}.message.role``            ``gen_ai.completion.{i}.role``
``llm.output_messages.{i}.message.content``         ``gen_ai.completion.{i}.content``
=================================================   ================================================

Keys not listed above (``llm.invocation_parameters``, rich message
contents, metadata blob, etc.) are intentionally left untouched — they
are either identical across conventions or have no canonical gen_ai
equivalent yet.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Env-driven selection
# ---------------------------------------------------------------------------

GENAI_MODE_OFF = "off"
GENAI_MODE_BOTH = "both"   # default
GENAI_MODE_ONLY = "only"

_ALLOWED_MODES = {GENAI_MODE_OFF, GENAI_MODE_BOTH, GENAI_MODE_ONLY}


def get_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve the current ``PCE_OTEL_GENAI_SEMCONV`` mode.

    Defaults to ``both`` so any operator who's already wired OTLP up
    gets OpenLLMetry / GenAI compatibility for free. ``off`` preserves
    the legacy OI-only behaviour for anyone who explicitly asked for it.
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    mode = (source.get("PCE_OTEL_GENAI_SEMCONV") or "").strip().lower()
    if mode not in _ALLOWED_MODES:
        return GENAI_MODE_BOTH
    return mode


# ---------------------------------------------------------------------------
# Attribute key constants (for callers that want to avoid stringifying)
# ---------------------------------------------------------------------------

GA_OPERATION_NAME = "gen_ai.operation.name"
GA_SYSTEM = "gen_ai.system"
GA_REQUEST_MODEL = "gen_ai.request.model"
GA_RESPONSE_MODEL = "gen_ai.response.model"
GA_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GA_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GA_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GA_CONVERSATION_ID = "gen_ai.conversation.id"
GA_USER_ID = "gen_ai.user.id"
GA_PROMPT = "gen_ai.prompt"
GA_COMPLETION = "gen_ai.completion"

# OpenLLMetry keeps a parallel flat namespace used by some dashboards.
OLM_USAGE_PROMPT_TOKENS = "llm.usage.prompt_tokens"
OLM_USAGE_COMPLETION_TOKENS = "llm.usage.completion_tokens"
OLM_USAGE_TOTAL_TOKENS = "llm.usage.total_tokens"

# Operation-name mapping for the OI span.kind discriminator.
_OP_NAME_BY_SPAN_KIND = {
    "LLM": "chat",
    "CHAT": "chat",
    "AGENT": "invoke_agent",
    "TOOL": "execute_tool",
}

# Pattern for OI indexed-message keys.
_RE_INPUT_MSG = re.compile(
    r"^llm\.input_messages\.(?P<i>\d+)\.message\.(?P<suffix>role|content|name|tool_call_id)$",
)
_RE_OUTPUT_MSG = re.compile(
    r"^llm\.output_messages\.(?P<i>\d+)\.message\.(?P<suffix>role|content|name|tool_call_id)$",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def to_genai_aliases(oi_attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Return a **new** dict holding only the ``gen_ai.*`` / OpenLLMetry
    aliases derived from ``oi_attributes``.

    Never raises. Missing source keys just mean the corresponding alias
    is omitted — backends tolerate optional attributes.
    """
    out: dict[str, Any] = {}
    if not oi_attributes:
        return out

    # Operation name: OI's span kind tells us whether this is chat vs tool.
    span_kind = oi_attributes.get("openinference.span.kind")
    if span_kind:
        op = _OP_NAME_BY_SPAN_KIND.get(str(span_kind).upper())
        if op:
            out[GA_OPERATION_NAME] = op

    system = (
        oi_attributes.get("llm.system")
        or oi_attributes.get("llm.provider")
    )
    if system:
        out[GA_SYSTEM] = str(system)

    model = oi_attributes.get("llm.model_name")
    if model:
        out[GA_REQUEST_MODEL] = str(model)
        # PCE captures the *actual* served model, which matches both
        # request and response in the common case. Downstream backends
        # treat these as hints, not equalities.
        out[GA_RESPONSE_MODEL] = str(model)

    # Tokens — emit both OTel GenAI (input/output) and OpenLLMetry
    # (prompt/completion) spellings so dashboards of either flavour work.
    prompt_tokens = oi_attributes.get("llm.token_count.prompt")
    if prompt_tokens is not None:
        out[GA_USAGE_INPUT_TOKENS] = int(prompt_tokens)
        out[OLM_USAGE_PROMPT_TOKENS] = int(prompt_tokens)
    completion_tokens = oi_attributes.get("llm.token_count.completion")
    if completion_tokens is not None:
        out[GA_USAGE_OUTPUT_TOKENS] = int(completion_tokens)
        out[OLM_USAGE_COMPLETION_TOKENS] = int(completion_tokens)
    total_tokens = oi_attributes.get("llm.token_count.total")
    if total_tokens is not None:
        out[GA_USAGE_TOTAL_TOKENS] = int(total_tokens)
        out[OLM_USAGE_TOTAL_TOKENS] = int(total_tokens)
    elif prompt_tokens is not None or completion_tokens is not None:
        out[GA_USAGE_TOTAL_TOKENS] = int(prompt_tokens or 0) + int(completion_tokens or 0)
        out[OLM_USAGE_TOTAL_TOKENS] = out[GA_USAGE_TOTAL_TOKENS]

    if "session.id" in oi_attributes and oi_attributes["session.id"]:
        out[GA_CONVERSATION_ID] = str(oi_attributes["session.id"])
    if "user.id" in oi_attributes and oi_attributes["user.id"]:
        out[GA_USER_ID] = str(oi_attributes["user.id"])

    if "input.value" in oi_attributes and oi_attributes["input.value"] is not None:
        out[GA_PROMPT] = str(oi_attributes["input.value"])
    if "output.value" in oi_attributes and oi_attributes["output.value"] is not None:
        out[GA_COMPLETION] = str(oi_attributes["output.value"])

    # Indexed messages — OpenLLMetry expects a parallel ``gen_ai.prompt.{i}.*``
    # / ``gen_ai.completion.{i}.*`` namespace.
    for key, value in oi_attributes.items():
        m = _RE_INPUT_MSG.match(key)
        if m:
            out[f"gen_ai.prompt.{m['i']}.{m['suffix']}"] = value
            continue
        m = _RE_OUTPUT_MSG.match(key)
        if m:
            out[f"gen_ai.completion.{m['i']}.{m['suffix']}"] = value

    return out


def apply_mode(
    oi_attributes: Mapping[str, Any],
    *,
    mode: Optional[str] = None,
) -> dict[str, Any]:
    """Return the attribute dict after applying the configured mode.

    - ``off``  → original OI dict unchanged
    - ``both`` → OI ∪ gen_ai aliases
    - ``only`` → gen_ai aliases + any non-``llm.*`` / non-``input.*`` /
                 non-``output.*`` OI extras (metadata, span.kind, user.id,
                 session.id) preserved for context.

    The ``only`` mode intentionally keeps ``openinference.span.kind``
    because backends use it as a routing hint even when consuming
    gen_ai primary keys.
    """
    m = (mode or get_mode()).lower()
    if m == GENAI_MODE_OFF:
        return dict(oi_attributes)

    aliases = to_genai_aliases(oi_attributes)

    if m == GENAI_MODE_ONLY:
        stripped = {
            k: v for k, v in oi_attributes.items()
            if not _is_llm_primary_key(k)
        }
        stripped.update(aliases)
        return stripped

    # ``both``
    merged = dict(oi_attributes)
    merged.update(aliases)
    return merged


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _is_llm_primary_key(key: str) -> bool:
    """Is this an OpenInference key we replace in ``only`` mode?"""
    return (
        key.startswith("llm.")
        or key.startswith("input.")
        or key.startswith("output.")
        or key.startswith("message.")
    )


__all__ = [
    "GENAI_MODE_BOTH",
    "GENAI_MODE_OFF",
    "GENAI_MODE_ONLY",
    "apply_mode",
    "get_mode",
    "to_genai_aliases",
]
