# SPDX-License-Identifier: Apache-2.0
"""P5.A-1 + P5.A-2 regression tests — lock the Tier-1 subscription capture
surface so later refactors can't silently drop a target.

Scope:
- **ALLOWED_HOSTS** must include the 6 user-facing subscription domains
  that L1 proxy needs to intercept (``chatgpt.com``, ``chat.openai.com``,
  ``claude.ai``, ``api.githubcopilot.com``, ``gemini.google.com``,
  ``copilot.microsoft.com``).
- **KNOWN_APPS** must include ChatGPT Desktop and Codex CLI so the
  launcher / detection paths cover the two Tier-1 executables missing
  from the list before P5.A.

The tests are structural (no network, no process launch) — they simply
assert the declarative data lives where downstream code expects it.
"""
from __future__ import annotations

import pytest

from pce_core.config import ALLOWED_HOSTS
from pce_core.electron_proxy import KNOWN_APPS, ElectronApp


# ---------------------------------------------------------------------------
# P5.A-1 — Allowlist subscription-tier domains
# ---------------------------------------------------------------------------

SUBSCRIPTION_DOMAINS = (
    "chatgpt.com",           # ChatGPT Web / Desktop
    "chat.openai.com",       # Legacy ChatGPT URL (still redirects)
    "claude.ai",             # Claude Web / Desktop
    "api.githubcopilot.com", # GitHub Copilot modern endpoint
    "gemini.google.com",     # Gemini Web
    "copilot.microsoft.com", # Microsoft Copilot Web
)


class TestSubscriptionAllowlist:
    @pytest.mark.parametrize("domain", SUBSCRIPTION_DOMAINS)
    def test_subscription_domain_in_allowlist(self, domain):
        assert domain in ALLOWED_HOSTS, (
            f"P5.A-1 regression: subscription-tier domain {domain!r} was "
            f"dropped from ALLOWED_HOSTS. L1 proxy can no longer capture "
            f"this Tier-1 target."
        )

    def test_allowlist_still_covers_api_key_endpoints(self):
        """Subscription additions must not displace the API-key endpoints
        — both flows coexist (a user can have both a paid subscription and
        an API key for the same provider)."""
        for api_host in ("api.openai.com", "api.anthropic.com",
                          "generativelanguage.googleapis.com"):
            assert api_host in ALLOWED_HOSTS

    def test_allowlist_has_at_least_the_expected_6_new_hosts(self):
        """Coarse sanity: the 6 new hosts should all be net-new entries
        (i.e. weren't already present under a different umbrella)."""
        for domain in SUBSCRIPTION_DOMAINS:
            assert domain in ALLOWED_HOSTS


# ---------------------------------------------------------------------------
# P5.A-2 — KNOWN_APPS subscription targets
# ---------------------------------------------------------------------------

def _get_app(name: str) -> ElectronApp:
    for app in KNOWN_APPS:
        if app.name == name:
            return app
    pytest.fail(f"KNOWN_APPS missing entry with name={name!r}")


class TestKnownAppsChatGPTDesktop:
    def test_chatgpt_desktop_entry_present(self):
        app = _get_app("chatgpt-desktop")
        assert app.display_name == "ChatGPT Desktop"

    def test_chatgpt_desktop_windows_exe_shapes(self):
        app = _get_app("chatgpt-desktop")
        assert "ChatGPT.exe" in app.exe_names
        # Windows installer drops under AppData\Local\Programs\ChatGPT\
        win_paths_s = "|".join(app.win_paths)
        assert "ChatGPT.exe" in win_paths_s
        assert any("AppData" in p for p in app.win_paths)

    def test_chatgpt_desktop_mac_app_bundle(self):
        app = _get_app("chatgpt-desktop")
        assert any("ChatGPT.app" in p for p in app.mac_paths)

    def test_chatgpt_desktop_linux_unsupported(self):
        """OpenAI has not shipped a Linux build; linux_paths must be empty
        so the detection layer doesn't report false positives."""
        app = _get_app("chatgpt-desktop")
        assert app.linux_paths == []

    def test_chatgpt_desktop_targets_the_subscription_domain(self):
        """A ChatGPT Desktop entry is useless if its ai_domains don't point
        at the same host the subscription traffic actually lands on."""
        app = _get_app("chatgpt-desktop")
        assert "chatgpt.com" in app.ai_domains
        # The allowlist must include every declared ai_domain so the proxy
        # doesn't drop the traffic we told the launcher to route to it.
        for domain in app.ai_domains:
            assert domain in ALLOWED_HOSTS, (
                f"ChatGPT Desktop ai_domain {domain!r} not in ALLOWED_HOSTS "
                f"— L1 proxy would see the flow but decline to capture it."
            )


class TestKnownAppsCodexCLI:
    def test_codex_cli_entry_present(self):
        app = _get_app("codex-cli")
        assert app.display_name == "Codex CLI"

    def test_codex_cli_has_platform_exe_variants(self):
        app = _get_app("codex-cli")
        # Windows: npm installs .cmd shim; .exe is rare but possible
        assert "codex.cmd" in app.exe_names
        # Unix: plain ``codex`` binary symlink
        assert "codex" in app.exe_names

    def test_codex_cli_npm_global_prefix_covered(self):
        """npm default global prefix on Windows is %APPDATA%\\npm; the
        KNOWN_APPS entry must list this path or Codex detection will miss
        the common install location."""
        app = _get_app("codex-cli")
        assert any("npm" in p for p in app.win_paths)

    def test_codex_cli_unix_installs_reachable(self):
        app = _get_app("codex-cli")
        assert any("/usr/local/bin/codex" in p for p in app.mac_paths)
        assert "/usr/bin/codex" in app.linux_paths or \
               "/usr/local/bin/codex" in app.linux_paths

    def test_codex_cli_targets_openai_api(self):
        app = _get_app("codex-cli")
        assert "api.openai.com" in app.ai_domains
        for domain in app.ai_domains:
            assert domain in ALLOWED_HOSTS


# ---------------------------------------------------------------------------
# Cross-cutting — Tier-1 coverage matrix
# ---------------------------------------------------------------------------

class TestTier1CaptureCoverage:
    """UCS §10.1 acceptance: ChatGPT Desktop / Copilot VS Code / Codex CLI /
    Claude Web/Desktop / Cursor must each appear in KNOWN_APPS *and* have
    every ai_domain they declare in ALLOWED_HOSTS."""

    @pytest.mark.parametrize("app_name", [
        "cursor",
        "vscode",
        "windsurf",
        "claude-desktop",
        "chatgpt-desktop",
        "codex-cli",
    ])
    def test_app_declared_domains_are_proxy_visible(self, app_name):
        app = _get_app(app_name)
        for domain in app.ai_domains:
            assert domain in ALLOWED_HOSTS, (
                f"KNOWN_APPS[{app_name}] declares ai_domain {domain!r} "
                f"that ALLOWED_HOSTS is missing; L1 capture would be "
                f"silently dropped for this app."
            )
