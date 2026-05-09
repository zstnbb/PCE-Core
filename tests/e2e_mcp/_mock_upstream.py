# SPDX-License-Identifier: Apache-2.0
"""Tiny scriptable MCP-style upstream for ``pce_mcp_proxy`` e2e tests.

This file is invoked by the proxy as the wrapped MCP server. It speaks
the same line-delimited JSON-RPC 2.0 stdio protocol so the proxy can
relay it without any test-specific awareness.

Configuration is via the ``PCE_MOCK_RESPONSES`` environment variable
which holds a JSON object. Top-level structure::

    {
      "initialize": {...},          # plain method → static result
      "tools/list": {...},
      "tools/call": {                # method-with-name → keyed by name
        "echo":    {"result": {...}},
        "boom":    {"error": {...}},
        "slowmo":  {"@@delay_ms": 200, "result": {...}}
      },
      "@@notify_after_initialize": [ # one-shot server-initiated frames
        {"method": "notifications/progress", "params": {"value": 0.5}}
      ],
      "@@exit_code": 0               # exit code on EOF (default 0)
    }

Special markers anywhere in a response template:

- ``@@delay_ms``: sleep N ms before emitting the response
- ``@@drop``: silently drop the request (no response written)
- ``@@close``: emit the response then close stdout (graceful shutdown)

The mock is intentionally minimal — it supports just enough surface to
exercise the proxy's classification, capture, and normalisation paths.
Anything more elaborate would mean re-implementing FastMCP, which we
already test elsewhere via ``pce_mcp/`` itself.
"""

from __future__ import annotations

import json
import os
import sys
import time


def _emit(msg: dict) -> None:
    """Write one JSON-RPC frame to stdout with a trailing newline."""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _emit_error(rpc_id, code: int, message: str) -> None:
    _emit({
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message},
    })


def _resolve_template(
    spec: dict,
    method: str,
    params: dict | None,
) -> dict | None:
    """Resolve the response template for one method call.

    Returns ``None`` to signal "no response should be written"
    (handled via the ``@@drop`` marker).
    """
    template = spec.get(method)
    if template is None:
        return {"__not_found__": True}

    # method-with-name dispatch (tools/call, prompts/get, resources/read)
    if method in ("tools/call", "prompts/get") and isinstance(template, dict):
        name_key = (params or {}).get("name", "")
        sub = template.get(name_key)
        if sub is None:
            sub = template.get("__default__")
        if sub is None:
            return {"__not_found__": True}
        template = sub
    elif method == "resources/read" and isinstance(template, dict):
        uri_key = (params or {}).get("uri", "")
        sub = template.get(uri_key)
        if sub is None:
            sub = template.get("__default__")
        if sub is None:
            return {"__not_found__": True}
        template = sub

    if not isinstance(template, dict):
        return {"__not_found__": True}

    if template.get("@@drop"):
        return None

    delay = template.get("@@delay_ms")
    if isinstance(delay, (int, float)) and delay > 0:
        time.sleep(delay / 1000.0)

    return template


def _serialise(rpc_id, template: dict) -> dict:
    """Materialise a wire-ready JSON-RPC response from a template."""
    out: dict = {"jsonrpc": "2.0", "id": rpc_id}
    if "error" in template:
        out["error"] = template["error"]
    else:
        out["result"] = template.get("result", {})
    return out


def main() -> int:
    raw = os.environ.get("PCE_MOCK_RESPONSES", "{}")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError:
        spec = {}
    if not isinstance(spec, dict):
        spec = {}

    exit_code = int(spec.get("@@exit_code", 0) or 0)
    notify_after_initialize = spec.get("@@notify_after_initialize") or []
    initialize_done = False

    while True:
        line = sys.stdin.readline()
        if not line:
            break  # EOF: host (or proxy) closed our stdin

        text = line.strip()
        if not text:
            continue

        try:
            req = json.loads(text)
        except json.JSONDecodeError:
            continue

        if not isinstance(req, dict):
            continue

        method = req.get("method", "")
        rpc_id = req.get("id")

        # Notifications never get a response
        if rpc_id is None:
            # Hook: emit any pre-loaded server-initiated frames once
            # we've seen the standard `notifications/initialized`.
            if (
                method == "notifications/initialized"
                and not initialize_done
                and notify_after_initialize
            ):
                initialize_done = True
                for frame in notify_after_initialize:
                    if isinstance(frame, dict):
                        _emit(frame)
            continue

        template = _resolve_template(spec, method, req.get("params"))
        if template is None:
            # @@drop marker — silently swallow
            continue
        if template.get("__not_found__"):
            _emit_error(rpc_id, -32601, f"Method not found: {method}")
            continue

        resp = _serialise(rpc_id, template)
        _emit(resp)

        if template.get("@@close"):
            break

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
