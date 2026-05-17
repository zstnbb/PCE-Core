# SPDX-License-Identifier: Apache-2.0
"""Tests for pce_core.standards.loader.

Covers the schema invariants documented in ADR-006 Wave 4r §6:
    - frontmatter required
    - schema_version=1 only
    - >=3 redundancy legs
    - >=1 verification_script
    - parsing of L1 (label): desc / L1: desc
    - list extraction handles bullets and numbered items
    - section heading lookup is case-insensitive

Also runs list_standards() against the real shipped standards/ directory and
asserts the 5 P0 target_ids load cleanly. This is the live regression guard
for the markdown files.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pce_core.standards.loader import (
    Leg,
    Standard,
    StandardsError,
    list_standards,
    load_standard,
)


# ---------------------------------------------------------------------------
# Live tests against shipped standards/ (the 5 P0 sites)
# ---------------------------------------------------------------------------

EXPECTED_P0_TARGETS = {
    "f1_chatgpt_web",
    "f1_claude_web",
    "f1_gemini_web",
    "f1_gas_web",
    "f1_grok_web",
}


def test_list_standards_finds_all_5_p0():
    stds = list_standards()
    seen = {s.target_id for s in stds}
    assert EXPECTED_P0_TARGETS.issubset(seen), (
        f"missing P0 targets: {EXPECTED_P0_TARGETS - seen}"
    )


@pytest.mark.parametrize("target_id", sorted(EXPECTED_P0_TARGETS))
def test_each_p0_standard_loads_cleanly(target_id):
    s = load_standard(target_id)
    assert s.target_id == target_id
    assert s.schema_version == 1
    assert s.tier == "S0"
    assert s.capability.strip(), "capability section must not be empty"
    assert s.verification_scripts, "must list verification scripts"
    assert len(s.redundancy_legs) >= 3, "ADR-006 §3.3 requires >=3 legs"


def test_load_standard_unknown_target_raises():
    with pytest.raises(StandardsError):
        load_standard("nonexistent_target_id_12345")


def test_standard_is_frozen():
    s = load_standard("f1_chatgpt_web")
    with pytest.raises(Exception):
        s.target_id = "tampered"


# ---------------------------------------------------------------------------
# Synthetic markdown tests (in tmp_path)
# ---------------------------------------------------------------------------

_VALID_MD = """\
---
target_id: f1_test_site
domain: test.example.com
tier: S0
last_validated: 2026-05-17
validator: unit-test
schema_version: 1
---

# Capability

Send prompt, receive response.

# Expected user-visible behaviour

1. User types into the input.
2. Response streams back.
3. Conversation persists.

# Critical DOM landmarks

- "input" -- where the user types
- "response" -- where the answer renders
- "history" -- prior turns

# Verification scripts

- t00_smoke
- t01_basic_chat

# Redundancy plan

- L1 (extension capture): background.js intercepts /api/chat
- L2 (proxy capture): mitmproxy captures /api/chat
- L3 (DOM capture): universal-extractor reads response from DOM

# Known instability surfaces

- selectors rotate

# Not-our-concern

- voice mode
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / f"{name}.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_parses_valid_markdown(tmp_path):
    _write(tmp_path, "f1_test_site", _VALID_MD)
    s = load_standard("f1_test_site", standards_dir=tmp_path)
    assert s.target_id == "f1_test_site"
    assert s.domain == "test.example.com"
    assert s.tier == "S0"
    assert "Send prompt" in s.capability
    assert s.expected_behaviour == [
        "User types into the input.",
        "Response streams back.",
        "Conversation persists.",
    ]
    assert s.verification_scripts == ["t00_smoke", "t01_basic_chat"]
    assert len(s.redundancy_legs) == 3
    assert s.redundancy_legs[0].name == "L1"
    assert s.redundancy_legs[0].label == "extension capture"
    assert "intercepts /api/chat" in s.redundancy_legs[0].description
    assert s.instability_surfaces == ["selectors rotate"]
    assert s.out_of_scope == ["voice mode"]


def test_missing_frontmatter_raises(tmp_path):
    _write(tmp_path, "bad", "# Capability\n\nNo frontmatter.\n")
    with pytest.raises(StandardsError, match="frontmatter"):
        load_standard("bad", standards_dir=tmp_path)


def test_unsupported_schema_version_rejected(tmp_path):
    body = _VALID_MD.replace("schema_version: 1", "schema_version: 2")
    _write(tmp_path, "v2", body)
    with pytest.raises(StandardsError):
        load_standard("v2", standards_dir=tmp_path)


def test_fewer_than_3_legs_rejected(tmp_path):
    # Drop L3 from the valid template
    body = _VALID_MD.replace(
        "- L3 (DOM capture): universal-extractor reads response from DOM\n", ""
    )
    _write(tmp_path, "twolegs", body)
    with pytest.raises(StandardsError, match=r"≥3|>=3|3 entries|legs"):
        load_standard("twolegs", standards_dir=tmp_path)


def test_empty_verification_scripts_rejected(tmp_path):
    body = _VALID_MD.replace(
        "# Verification scripts\n\n- t00_smoke\n- t01_basic_chat\n",
        "# Verification scripts\n\n",
    )
    _write(tmp_path, "noverify", body)
    with pytest.raises(StandardsError):
        load_standard("noverify", standards_dir=tmp_path)


def test_leg_parse_with_label():
    leg = Leg.parse("- L2 (proxy capture): mitmproxy CA captures the same call")
    assert leg.name == "L2"
    assert leg.label == "proxy capture"
    assert leg.description == "mitmproxy CA captures the same call"


def test_leg_parse_without_label():
    leg = Leg.parse("L9: bare description with no parens")
    assert leg.name == "L9"
    assert leg.label == ""
    assert leg.description == "bare description with no parens"


def test_leg_parse_bad_raises():
    with pytest.raises(StandardsError):
        Leg.parse("not a leg line at all")


def test_section_alias_lookup_case_insensitive(tmp_path):
    body = _VALID_MD.replace(
        "# Verification scripts",
        "# Verification Scripts (sources of truth for V-GREEN)",
    )
    _write(tmp_path, "alias", body)
    s = load_standard("alias", standards_dir=tmp_path)
    assert s.verification_scripts == ["t00_smoke", "t01_basic_chat"]


def test_underscore_prefixed_files_skipped(tmp_path):
    _write(tmp_path, "f1_test_site", _VALID_MD)
    _write(tmp_path, "_template", _VALID_MD)  # should be ignored
    stds = list_standards(standards_dir=tmp_path)
    target_ids = [s.target_id for s in stds]
    assert "f1_test_site" in target_ids
    # _template would parse to target_id=f1_test_site too (same content); but
    # since we skip _-prefixed names, only one entry should exist.
    assert len(stds) == 1
