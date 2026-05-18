# SPDX-License-Identifier: Apache-2.0
"""pce_cli_wrapper._proxy_env – inject capture env vars into child CLI.

The three wrapped CLIs (claude / codex / gemini) are all Node-based on
npm install:

    claude.cmd  → bin/claude.exe   (Anthropic single-exec Node bundle)
    codex.cmd   → node …/codex.js  (OpenAI Codex CLI)
    gemini.cmd  → node …/gemini.js (Google Gemini CLI)

We use this fact to enable two L1 capture lanes through the L3h shim:

1. **HTTPS_PROXY family** — set so the child routes its requests
   through mitmproxy (or any HTTP proxy the operator points us at).
   Both upper-case and lower-case variants are exported because Node
   libraries pick different ones depending on age.

2. **NODE_OPTIONS=--tls-keylog=$SSLKEYLOGFILE** — Node 12+ writes raw
   TLS session keys to the keylog path on every handshake. Together
   with the A2 ``pce_sslkeylog`` daemon, this gives us an offline-
   decrypt path that does not require the child to even know mitmproxy
   exists. Useful for clients (e.g. Node 22+ undici) that no longer
   auto-honour ``HTTPS_PROXY``.

3. **NODE_OPTIONS=--require=…/_undici_proxy_inject.js** — for Node 22+
   undici (Gemini CLI), the global dispatcher must be replaced with
   ``ProxyAgent`` for ``HTTPS_PROXY`` to take effect. The injected
   require shim does this if undici is available.

Both vectors are *opt-in*: nothing changes unless the operator (or
the install step) sets one of the trigger env vars. The shim never
silently routes traffic.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, MutableMapping, Optional

__all__ = [
    "augment_child_env",
    "proxy_inject_script_path",
    "PROXY_ENV_TRIGGER",
    "PROXY_TARGET_ENV_KEYS",
    "NODE_TARGET_IDS",
]


# When this env var is set in the parent process, the relay injects
# proxy + keylog vars into the child env. We do NOT use HTTPS_PROXY
# directly as the trigger because some users have it set globally for
# other reasons (e.g. corp proxy) and would not expect the CLI shim
# to suddenly behave differently.
PROXY_ENV_TRIGGER = "PCE_CLI_WRAPPER_PROXY"

# Env keys we export to the child when PCE_CLI_WRAPPER_PROXY is set.
PROXY_TARGET_ENV_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "https_proxy",
    "http_proxy",
)

# target_id values that get Node-specific augmentation (NODE_OPTIONS).
# Kept in sync with discovery._TARGETS.
NODE_TARGET_IDS = frozenset(
    {"claude-code", "codex-cli", "gemini-cli"}
)


def proxy_inject_script_path() -> Path:
    """Return absolute path to the undici proxy-inject require shim.

    The shim lives next to this module so it is shipped with the wheel
    and is reachable from any working directory.
    """
    return Path(__file__).resolve().parent / "_undici_proxy_inject.js"


def augment_child_env(
    *,
    target_id: Optional[str],
    base_env: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Return a copy of ``base_env`` with proxy / keylog vars injected.

    Behaviour:

    - If ``base_env`` is None, the current process environment is used
      as the starting point.
    - If ``PCE_CLI_WRAPPER_PROXY`` is set, export it to all of
      ``PROXY_TARGET_ENV_KEYS`` *unless that key is already set in
      base_env*. Existing operator overrides win.
    - If ``SSLKEYLOGFILE`` is set, append ``--tls-keylog=$SSLKEYLOGFILE``
      to ``NODE_OPTIONS`` for Node targets (idempotent — never
      duplicated). The path is wrapped in double quotes only if it
      contains whitespace, because Node parses NODE_OPTIONS like a
      shell line.
    - For Node targets, when ``PCE_CLI_WRAPPER_PROXY`` is set, also
      append ``--require=<undici-shim>`` to ``NODE_OPTIONS`` so that
      undici-based clients (gemini-cli on Node 22+) actually honour
      the proxy.

    The function is pure — it does not mutate ``base_env`` or
    ``os.environ``. The caller passes the returned dict to
    ``subprocess.Popen(env=...)``.
    """
    env_in: MutableMapping[str, str] = dict(base_env if base_env is not None else os.environ)
    env: dict[str, str] = dict(env_in)

    proxy = env.get(PROXY_ENV_TRIGGER, "").strip()
    keylog = env.get("SSLKEYLOGFILE", "").strip()
    is_node = (target_id or "").lower() in NODE_TARGET_IDS

    # 1. HTTPS_PROXY family
    if proxy:
        for key in PROXY_TARGET_ENV_KEYS:
            env.setdefault(key, proxy)

    # 2. + 3. NODE_OPTIONS augmentation (only for Node targets)
    if is_node:
        node_opts = env.get("NODE_OPTIONS", "").strip()
        additions: list[str] = []

        if keylog:
            keylog_arg = _quote_if_needed(keylog)
            tls_flag = f"--tls-keylog={keylog_arg}"
            if "--tls-keylog" not in node_opts:
                additions.append(tls_flag)

        if proxy:
            shim_path = proxy_inject_script_path()
            shim_arg = _quote_if_needed(str(shim_path))
            require_flag = f"--require={shim_arg}"
            # de-dup if same shim already required
            if str(shim_path) not in node_opts:
                additions.append(require_flag)

        if additions:
            new_opts = (node_opts + " " + " ".join(additions)).strip()
            env["NODE_OPTIONS"] = new_opts

    return env


def _quote_if_needed(value: str) -> str:
    """Wrap ``value`` in double quotes if it contains whitespace.

    NODE_OPTIONS is parsed shell-style by Node, so paths with spaces
    (common on Windows under ``C:\\Program Files\\``) need quoting.
    """
    if not value:
        return value
    if any(ch.isspace() for ch in value):
        # Escape any embedded double-quotes by backslash. Node's
        # NODE_OPTIONS parser is shell-style and accepts this form.
        escaped = value.replace('"', r"\"")
        return f'"{escaped}"'
    return value
