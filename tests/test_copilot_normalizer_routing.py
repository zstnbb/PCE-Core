# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the W6 GitHub Copilot Chat (L1) routing.

The L3g (local-storage) flavour of Copilot Chat already had its own
``CopilotChatNormalizer`` covering captures from
``%APPDATA%/Code/User/globalStorage/emptyWindowChatSessions/``. W6 adds
the L1 path: when mitmproxy or A2 sees a request to
``api.githubcopilot.com/chat/completions``, the existing OpenAI-compatible
chat-completions schema should kick in (GitHub Copilot Chat speaks the
exact same wire format as api.openai.com, just with GitHub auth tokens).
The fix is two-line additions in two places:

  1. ``pce_core.normalizer.openai._COMPATIBLE_HOSTS`` — add the Copilot
     hostnames so the normalizer's ``can_handle()`` returns True.
  2. ``pce_proxy.addon._provider_from_host`` and
     ``pce_sslkeylog.parser._provider_from_host`` — return
     ``"github-copilot"`` (distinct from ``"openai"``) for billing /
     rate-limit attribution.

These tests pin both behaviours so we don't regress.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from pce_core.normalizer.openai import OpenAIChatNormalizer


# ---------------------------------------------------------------------------
# can_handle routing
# ---------------------------------------------------------------------------


def test_openai_normalizer_handles_githubcopilot_chat_path():
    n = OpenAIChatNormalizer()
    assert n.can_handle("", "api.githubcopilot.com", "/chat/completions")
    assert n.can_handle("", "api.githubcopilot.com", "/v1/chat/completions")


def test_openai_normalizer_handles_copilot_proxy_legacy_endpoint():
    """The pre-Chat Copilot completions endpoint (used by older VS Code
    versions for inline suggestions). Same OpenAI-compatible wire."""
    n = OpenAIChatNormalizer()
    assert n.can_handle(
        "", "copilot-proxy.githubusercontent.com",
        "/v1/engines/copilot-codex/completions",
    )


def test_openai_normalizer_handles_github_copilot_provider_label():
    """When the row's ``provider`` is already ``github-copilot`` (set by
    the proxy / sslkeylog parser), the normalizer should still route to
    OpenAIChatNormalizer based on path or host even if provider doesn't
    match the OpenAI compatibility set."""
    n = OpenAIChatNormalizer()
    assert n.can_handle(
        "github-copilot", "api.githubcopilot.com", "/chat/completions",
    )


# ---------------------------------------------------------------------------
# end-to-end normalize() smoke
# ---------------------------------------------------------------------------


def test_openai_normalizer_processes_copilot_chat_request_response():
    """A Copilot Chat request/response pair flows through normalize()
    and produces the expected user + assistant messages, with model name
    preserved."""
    n = OpenAIChatNormalizer()
    req = json.dumps({
        "model": "gpt-4o-copilot",
        "messages": [
            {"role": "system", "content": "You are GitHub Copilot."},
            {"role": "user", "content": "Why is my regex matching too much?"},
        ],
        "stream": False,
    })
    resp = json.dumps({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Greedy quantifiers — use `*?` for lazy matching.",
            },
            "finish_reason": "stop",
        }],
        "model": "gpt-4o-copilot",
        "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
    })
    result = n.normalize(
        request_body=req, response_body=resp,
        provider="github-copilot",
        host="api.githubcopilot.com",
        path="/chat/completions",
    )
    assert result is not None
    assert result.model_name == "gpt-4o-copilot"
    user_msgs = [m for m in result.messages if m.role == "user"]
    asst_msgs = [m for m in result.messages if m.role == "assistant"]
    assert any("regex" in m.content_text.lower() for m in user_msgs)
    assert any("lazy" in m.content_text.lower() for m in asst_msgs)


# ---------------------------------------------------------------------------
# provider_from_host returns "github-copilot" not "openai"
# ---------------------------------------------------------------------------


def test_provider_from_host_github_copilot_proxy():
    from pce_proxy.addon import _provider_from_host as proxy_pfh
    assert proxy_pfh("api.githubcopilot.com") == "github-copilot"
    assert proxy_pfh("copilot-proxy.githubusercontent.com") == "github-copilot"
    # Sanity: real OpenAI still maps to "openai"
    assert proxy_pfh("api.openai.com") == "openai"


def test_provider_from_host_github_copilot_sslkeylog():
    from pce_sslkeylog.parser import _provider_from_host as sslk_pfh
    assert sslk_pfh("api.githubcopilot.com") == "github-copilot"
    assert sslk_pfh("copilot-proxy.githubusercontent.com") == "github-copilot"
    # Existing mappings still work
    assert sslk_pfh("api.openai.com") == "openai"
    assert sslk_pfh("chatgpt.com") == "openai"
    # Cursor + Codeium also got proper provider labels
    assert sslk_pfh("api2.cursor.sh") == "cursor"
    assert sslk_pfh("server.codeium.com") == "codeium"
    assert sslk_pfh("server.self-serve.windsurf.com") == "codeium"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
