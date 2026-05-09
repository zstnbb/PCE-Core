# SPDX-License-Identifier: Apache-2.0
"""PCE Core – MCP middleware proxy normalizer (UCS L3f, posture B).

Handles JSON-RPC 2.0 request/response pairs captured by ``pce_mcp_proxy``
(``provider = 'mcp:<upstream>'``, ``host = 'stdio'``,
``path = '<jsonrpc-method>'``).

Scope (v1, P5.B.1):

- ``tools/call`` → assistant tool_calls + tool response message.
  This is the primary product value: visualising what tools an agent
  invoked, with what arguments, and what came back.
- ``resources/read`` → assistant ``read_resource`` call + tool result.
  Same shape as tools/call but the "tool" is a resource URI.
- ``prompts/get`` → assistant ``get_prompt`` call + tool result.

Out of scope (intentionally returns ``None``):

- ``initialize`` / ``initialized`` — handshake metadata, no
  conversational value.
- ``tools/list`` / ``resources/list`` / ``prompts/list`` — tool
  catalog enumeration; useful as Tier 0 only.
- ``ping`` / ``$/cancelRequest`` — keep-alive / control frames.
- Server-initiated ``sampling/createMessage`` (host responds) —
  v2 territory; the wire-direction handling is asymmetric and pairing
  by id is ambiguous in posture B.

Output schema mapping:

- ``messages.role`` uses the OpenAI tool-calling convention so the
  existing dashboard renderers and OpenInference attribute path work
  without a third archetype tube. Specifically:
    - The agent's ``tools/call`` request maps to a single
      ``role=assistant`` message with a ``tool_calls`` array.
    - The upstream's response maps to a single ``role=tool`` message
      with ``tool_call_id`` (mirroring OpenAI's chat-completions
      schema for tool-call results).
- ``content_json.tool_calls[].function.name`` is namespaced as
  ``<upstream>.<tool_name>`` so the same tool name from two different
  upstreams (e.g. ``filesystem.read_file`` and ``git.read_file``)
  doesn't collide in the dashboard.
- ``session_key = "mcp-proxy:<upstream>:<YYYY-MM-DD>"`` groups all
  tool calls against one upstream within one local-time day into a
  single session. This is a stop-gap until P5.B.2 introduces a
  formal MCP session boundary signal (``initialize`` ↔ proxy exit).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from .base import BaseNormalizer, NormalizedMessage, NormalizedResult

logger = logging.getLogger("pce.normalizer.mcp_jsonrpc")


# Methods this normalizer produces messages for. Anything else (handshake,
# enumeration, control) returns ``None`` and stays Tier 0 only.
_NORMALIZABLE_METHODS = frozenset({
    "tools/call",
    "resources/read",
    "prompts/get",
})


class MCPJsonRpcNormalizer(BaseNormalizer):
    """Normalize ``pce_mcp_proxy`` JSON-RPC pairs into Tier 1 records."""

    def can_handle(self, provider: str, host: str, path: str) -> bool:
        if not provider or not provider.startswith("mcp:"):
            return False
        if host != "stdio":
            return False
        return path in _NORMALIZABLE_METHODS

    def normalize(
        self,
        request_body: str,
        response_body: str,
        *,
        provider: str,
        host: str,
        path: str,
        model_name: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> Optional[NormalizedResult]:
        req = _safe_json(request_body)
        resp = _safe_json(response_body)
        if not isinstance(req, dict) or not isinstance(resp, dict):
            return None

        upstream = provider.split(":", 1)[1] if ":" in provider else "unknown"

        if path == "tools/call":
            return self._normalize_invoke(
                req, resp, upstream, created_at,
                method="tools/call",
                request_name_field="name",
                request_args_field="arguments",
                tool_id_prefix="tool",
            )
        if path == "resources/read":
            return self._normalize_invoke(
                req, resp, upstream, created_at,
                method="resources/read",
                request_name_field="uri",
                request_args_field=None,
                tool_id_prefix="resource",
            )
        if path == "prompts/get":
            return self._normalize_invoke(
                req, resp, upstream, created_at,
                method="prompts/get",
                request_name_field="name",
                request_args_field="arguments",
                tool_id_prefix="prompt",
            )

        return None

    # ------------------------------------------------------------------
    # Generic invocation normaliser used by all three handled methods
    # ------------------------------------------------------------------

    def _normalize_invoke(
        self,
        req: dict[str, Any],
        resp: dict[str, Any],
        upstream: str,
        created_at: Optional[float],
        *,
        method: str,
        request_name_field: str,
        request_args_field: Optional[str],
        tool_id_prefix: str,
    ) -> Optional[NormalizedResult]:
        params = req.get("params")
        if not isinstance(params, dict):
            return None

        invocation_name = params.get(request_name_field)
        if not invocation_name or not isinstance(invocation_name, str):
            return None

        invocation_args: dict[str, Any] = {}
        if request_args_field:
            raw_args = params.get(request_args_field)
            if isinstance(raw_args, dict):
                invocation_args = raw_args

        rpc_id = req.get("id")
        # Tool-call ids must be unique per session, stable across re-
        # normalisation, and visually distinct from upstream JSON-RPC ids
        # (which can collide across days). Compose with upstream + method
        # + id.
        tool_call_id = f"mcp_{upstream}_{tool_id_prefix}_{rpc_id}"
        # Namespaced tool name keeps cross-upstream uniqueness in the
        # dashboard / token estimates.
        namespaced_name = f"{upstream}.{invocation_name}"

        # ── Assistant message: the agent's invocation ──────────────────
        # content_text embeds the tool_call_id so two invocations of the
        # same tool with identical arguments still produce distinct
        # messages — required so the message-processor's content-based
        # deduplication doesn't collapse a multi-step agent loop into a
        # single row.
        args_summary = json.dumps(invocation_args, ensure_ascii=False, sort_keys=True)
        tool_call_envelope = {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": namespaced_name,
                "arguments": args_summary,
            },
        }
        assistant_content_json = json.dumps(
            {"tool_calls": [tool_call_envelope]},
            ensure_ascii=False,
        )
        assistant_message = NormalizedMessage(
            role="assistant",
            content_text=f"[{method}] {namespaced_name} {args_summary} #{tool_call_id}",
            content_json=assistant_content_json,
            ts=created_at,
        )

        # ── Tool message: the upstream's response ──────────────────────
        # ts on the tool message is offset by 1µs after the assistant so
        # downstream queries that ``ORDER BY ts`` get the conversational
        # order (assistant invokes → tool replies) without needing an
        # auxiliary sort key. The microsecond bump is invisible at the
        # dashboard's display granularity.
        result_text, is_error, error_meta = _extract_result_text(resp)
        tool_ts = (created_at + 1e-6) if created_at is not None else None
        tool_content_json = json.dumps(
            {
                "tool_call_id": tool_call_id,
                "name": namespaced_name,
                "result": resp.get("result"),
                "error": resp.get("error") if "error" in resp else None,
                "is_error": is_error,
            },
            ensure_ascii=False,
        )
        # Suffix the tool message's content_text with the tool_call_id
        # for the same dedup-resistance reason as the assistant message.
        result_text_unique = (
            f"{result_text} #{tool_call_id}" if result_text else f"#{tool_call_id}"
        )
        tool_message = NormalizedMessage(
            role="tool",
            content_text=result_text_unique,
            content_json=tool_content_json,
            ts=tool_ts,
        )

        layer_meta = {
            "mcp": {
                "upstream": upstream,
                "method": method,
                "invocation_name": invocation_name,
                "tool_call_id": tool_call_id,
                "is_error": is_error,
            }
        }
        if error_meta:
            layer_meta["mcp"].update(error_meta)

        return NormalizedResult(
            provider=f"mcp:{upstream}",
            tool_family="mcp-proxy",
            session_key=_session_key(upstream, created_at),
            title_hint=f"MCP / {upstream}",
            messages=[assistant_message, tool_message],
            confidence=0.9,
            normalizer_name="MCPJsonRpcNormalizer",
            layer_meta=layer_meta,
        )


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------

def _safe_json(text: str) -> Any:
    """Return the parsed JSON or ``None`` on any failure."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_result_text(
    resp: dict[str, Any],
) -> tuple[str, bool, Optional[dict[str, Any]]]:
    """Return ``(text, is_error, error_meta)`` from a JSON-RPC response.

    Handles the three observed response shapes:

    - JSON-RPC level error: ``resp["error"] = {"code", "message", "data"}``
    - Tool-level error: ``resp["result"] = {"isError": true, "content": [...]}``
    - Success: ``resp["result"] = {"content": [{"type": "text", "text": ...}, ...]}``
    """
    if "error" in resp and isinstance(resp["error"], dict):
        err = resp["error"]
        code = err.get("code")
        msg = err.get("message", "")
        text = f"[JSON-RPC error {code}] {msg}".strip()
        return text, True, {"jsonrpc_error_code": code, "jsonrpc_error_message": msg}

    result = resp.get("result")
    if not isinstance(result, dict):
        # Some methods return primitives; stringify defensively.
        return json.dumps(result, ensure_ascii=False) if result is not None else "", False, None

    is_error = bool(result.get("isError", False))
    content = result.get("content")

    if isinstance(content, list):
        text_pieces: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                piece = item.get("text", "")
                if piece:
                    text_pieces.append(piece)
        if text_pieces:
            joined = "\n".join(text_pieces)
            return (f"[tool error] {joined}" if is_error else joined), is_error, None

    # Fallback: the whole result object as JSON.
    return json.dumps(result, ensure_ascii=False), is_error, None


def _session_key(upstream: str, created_at: Optional[float]) -> str:
    """Group all calls against one upstream on one local-time day."""
    if created_at:
        day = time.strftime("%Y-%m-%d", time.localtime(created_at))
    else:
        day = time.strftime("%Y-%m-%d")
    return f"mcp-proxy:{upstream}:{day}"
