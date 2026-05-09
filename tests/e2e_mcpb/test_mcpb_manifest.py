# SPDX-License-Identifier: Apache-2.0
"""Validate pce_mcp/mcpb/manifest.json against the .mcpb spec 0.2.

These tests are pure-Python (no Node dependency) so they always run in
CI. They guard the structural invariants the Anthropic directory
reviewer — and Claude Desktop's install-time validator — will enforce.

References:
- https://github.com/anthropics/mcpb (official spec)
- ADR-016 §3.3 for the PCE-specific field requirements.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MCPB_DIR = REPO_ROOT / "pce_mcp" / "mcpb"
MANIFEST_PATH = MCPB_DIR / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST_PATH.is_file(), f"manifest.json missing at {MANIFEST_PATH}"
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Spec-required top-level fields
# ---------------------------------------------------------------------------

def test_manifest_version_is_supported(manifest):
    """manifest_version must be one of the spec versions we tested."""
    assert manifest.get("manifest_version") in {"0.2", "0.3"}


def test_required_top_level_fields(manifest):
    """.mcpb spec 0.2 REQUIRED fields."""
    required = {"manifest_version", "name", "version", "description", "server"}
    missing = required - set(manifest.keys())
    assert not missing, f"manifest.json missing required fields: {missing}"


def test_name_is_slug_safe(manifest):
    """Name must be URL/filesystem-safe for the packed artifact filename."""
    name = manifest["name"]
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*[a-z0-9]", name), \
        f"name {name!r} is not a valid slug"


def test_version_is_semver(manifest):
    """Version must be semver so the Directory sort / auto-update works."""
    version = manifest["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?", version), \
        f"version {version!r} is not semver"


# ---------------------------------------------------------------------------
# Server / runtime
# ---------------------------------------------------------------------------

def test_server_block_is_node(manifest):
    """ADR-016 §4.3 decided on Node for runtime ubiquity in Claude Desktop."""
    server = manifest["server"]
    assert server.get("type") == "node", \
        "ADR-016 §4.3 mandates server.type == 'node'"
    assert server.get("entry_point") == "server/index.js"
    mcp = server.get("mcp_config", {})
    assert mcp.get("command") == "node"
    args = mcp.get("args") or []
    assert any("index.js" in a for a in args), \
        "mcp_config.args must point at server/index.js"


def test_server_entry_point_exists(manifest):
    entry = manifest["server"]["entry_point"]
    assert (MCPB_DIR / entry).is_file(), \
        f"server.entry_point {entry} does not exist on disk"


def test_mcp_config_uses_user_config_substitution(manifest):
    """ADR-016 §3.3 — PCE_CORE_URL must be user-configurable via ${user_config.*}."""
    env = manifest["server"]["mcp_config"].get("env", {})
    assert env.get("PCE_CORE_URL", "").startswith("${user_config."), \
        "PCE_CORE_URL must be sourced from user_config.pce_core_url"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "pce_capture",
    "pce_query",
    "pce_stats",
    "pce_sessions",
    "pce_session_messages",
    "pce_capture_pair",
}


def test_tools_match_python_implementation(manifest):
    """manifest.tools must mirror pce_mcp/server.py's @mcp.tool() decorations."""
    names = {t["name"] for t in manifest.get("tools", [])}
    assert names == EXPECTED_TOOLS, \
        f"tools mismatch vs pce_mcp/server.py: missing={EXPECTED_TOOLS-names} extra={names-EXPECTED_TOOLS}"


def test_tools_each_have_description(manifest):
    for t in manifest["tools"]:
        assert t.get("description"), f"tool {t.get('name')} is missing description"


# ---------------------------------------------------------------------------
# User config
# ---------------------------------------------------------------------------

def test_user_config_has_pce_core_url(manifest):
    uc = manifest.get("user_config", {})
    assert "pce_core_url" in uc, "user_config.pce_core_url must exist"
    entry = uc["pce_core_url"]
    assert entry.get("type") == "string"
    default_url = entry.get("default", "")
    assert default_url.startswith("http://127.0.0.1:"), \
        "default must be a loopback URL to match pce_core INGEST_PORT default"


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------

def test_compatibility_declares_all_platforms(manifest):
    platforms = set(manifest.get("compatibility", {}).get("platforms", []))
    assert {"darwin", "win32", "linux"}.issubset(platforms), \
        "Must declare support for all three desktop platforms"


def test_compatibility_node_version(manifest):
    runtimes = manifest.get("compatibility", {}).get("runtimes", {})
    assert "node" in runtimes, "compatibility.runtimes.node required"


# ---------------------------------------------------------------------------
# Privacy — Anthropic directory submission requires an explicit array.
# ---------------------------------------------------------------------------

def test_privacy_policies_present_and_nonempty(manifest):
    policies = manifest.get("privacy_policies") or []
    assert isinstance(policies, list) and len(policies) >= 3, \
        "ADR-016 §3.3 requires ≥3 privacy policy statements"
    joined = " ".join(policies).lower()
    # Must call out what gets captured, where it lives, and no external upload.
    assert "local" in joined
    assert "sqlite" in joined or "database" in joined
    assert "no " in joined or "not" in joined, "must explicitly negate external upload"


# ---------------------------------------------------------------------------
# Bundle assets
# ---------------------------------------------------------------------------

def test_icon_exists(manifest):
    icon_rel = manifest.get("icon")
    assert icon_rel, "manifest.icon is required for Claude Desktop UI"
    icon_path = MCPB_DIR / icon_rel
    assert icon_path.is_file(), f"icon {icon_path} does not exist on disk"
    # Claude Desktop expects at least 128×128; we accept PNG or similar
    assert icon_path.suffix.lower() == ".png"


def test_package_json_version_matches_manifest(manifest):
    pkg_path = MCPB_DIR / "package.json"
    assert pkg_path.is_file()
    with pkg_path.open("r", encoding="utf-8") as fh:
        pkg = json.load(fh)
    assert pkg["name"] == manifest["name"], "package.json name must match manifest.json"
    assert pkg["version"] == manifest["version"], "package.json version must match manifest.json"


def test_formatters_and_index_js_exist():
    for f in ("server/index.js", "server/formatters.js"):
        assert (MCPB_DIR / f).is_file(), f"missing bundled JS file: {f}"
