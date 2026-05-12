# SPDX-License-Identifier: Apache-2.0
"""Tests for ``pce_core.adapter_loader`` (P5.C.4.1).

Coverage:

| Aspect | Tests |
|---|---|
| Defaults load | 3 (every shipped manifest parses, count = 3, all v1) |
| AdapterConfig shape | 3 (selectors_for / timeout_ms / to_dict round-trip) |
| Parity with existing site adapters | 3 (chatgpt / claude / gemini class attrs match YAML) |
| Validation errors | 6 (missing keys, bad selectors, bad timeouts, bad regex, too-new schema, bad selector type) |
| apply_to_class | 3 (selectors mirror / timeouts mirror / unknown group warns) |

18 tests total — well above the P5.C.4.1 acceptance gate. Backward
compat: every existing site adapter's class attribute is asserted to
equal the YAML-loaded value, so P5.C.4.2 can refactor without
changing behaviour.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pce_core.adapter_loader import (  # noqa: E402
    DEFAULT_ADAPTERS_DIR,
    LATEST_SCHEMA_VERSION,
    SELECTOR_GROUP_TO_ATTR,
    AdapterConfig,
    AdapterValidationError,
    apply_to_class,
    list_adapter_names,
    load_adapter,
    load_all_adapters,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
    return path


def _minimal_manifest(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "name": "minimal",
        "provider": "test",
        "url": "https://example.test/",
        "selectors": {"input": ["#input"]},
        "timeouts_ms": {"page_load": 10000},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Defaults: every shipped manifest parses
# ---------------------------------------------------------------------------

def test_default_manifests_load_cleanly() -> None:
    """All shipped YAMLs must load without raising."""
    configs = load_all_adapters()
    names = {c.name for c in configs}
    assert {"chatgpt", "claude", "gemini"}.issubset(names)


def test_list_adapter_names_returns_sorted_stems() -> None:
    names = list_adapter_names()
    assert "chatgpt" in names
    assert names == sorted(names)


def test_default_manifests_use_current_schema_version() -> None:
    for cfg in load_all_adapters():
        assert cfg.schema_version == LATEST_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# AdapterConfig shape
# ---------------------------------------------------------------------------

def test_selectors_for_returns_empty_list_for_unknown_group() -> None:
    cfg = load_adapter("chatgpt")
    assert cfg.selectors_for("definitely_not_a_group") == []
    assert cfg.selectors_for("input")  # non-empty for chatgpt


def test_timeout_ms_falls_back_to_default() -> None:
    cfg = load_adapter("chatgpt")
    assert cfg.timeout_ms("page_load") == 25000  # YAML mirrors live chatgpt.py override
    assert cfg.timeout_ms("nonexistent", default=12345) == 12345


def test_to_dict_round_trips_session_url_pattern() -> None:
    cfg = load_adapter("chatgpt")
    d = cfg.to_dict()
    assert d["session_url_pattern"] == "/c/([a-z0-9-]+)"
    # Sanity: every required field present in the dump.
    for key in ("name", "provider", "url", "selectors", "timeouts_ms",
                "settings_url", "tier", "plane", "canary_endpoint"):
        assert key in d


# ---------------------------------------------------------------------------
# Parity with existing tests/e2e_probe/sites/*.py
# ---------------------------------------------------------------------------

def test_chatgpt_yaml_matches_existing_site_adapter() -> None:
    """The YAML must reproduce the live class attributes 1:1.

    This is the **backward-compat gate** for P5.C.4.2: if this test
    fails, the YAML drifted from the Python source and the upcoming
    refactor would change behaviour. Fix the YAML before refactoring.
    """
    from tests.e2e_probe.sites.chatgpt import ChatGPTAdapter

    cfg = load_adapter("chatgpt")
    assert tuple(cfg.selectors_for("input")) == ChatGPTAdapter.input_selectors
    assert tuple(cfg.selectors_for("send_button")) == ChatGPTAdapter.send_button_selectors
    assert tuple(cfg.selectors_for("response_container")) == ChatGPTAdapter.response_container_selectors
    assert tuple(cfg.selectors_for("login_wall")) == ChatGPTAdapter.login_wall_selectors
    assert cfg.timeout_ms("page_load") == ChatGPTAdapter.page_load_timeout_ms
    assert cfg.timeout_ms("response") == ChatGPTAdapter.response_timeout_ms
    assert cfg.settings_url == ChatGPTAdapter.settings_url
    assert cfg.url == ChatGPTAdapter.url
    assert cfg.provider == ChatGPTAdapter.provider


def test_claude_yaml_matches_existing_site_adapter() -> None:
    from tests.e2e_probe.sites.claude import ClaudeAdapter

    cfg = load_adapter("claude")
    assert tuple(cfg.selectors_for("input")) == ClaudeAdapter.input_selectors
    assert tuple(cfg.selectors_for("send_button")) == ClaudeAdapter.send_button_selectors
    assert tuple(cfg.selectors_for("response_container")) == ClaudeAdapter.response_container_selectors
    assert cfg.timeout_ms("page_load") == ClaudeAdapter.page_load_timeout_ms
    assert cfg.timeout_ms("response") == ClaudeAdapter.response_timeout_ms
    assert cfg.settings_url == ClaudeAdapter.settings_url
    assert cfg.url == ClaudeAdapter.url


def test_gemini_yaml_matches_existing_site_adapter() -> None:
    from tests.e2e_probe.sites.gemini import GeminiAdapter

    cfg = load_adapter("gemini")
    assert tuple(cfg.selectors_for("input")) == GeminiAdapter.input_selectors
    assert tuple(cfg.selectors_for("send_button")) == GeminiAdapter.send_button_selectors
    assert tuple(cfg.selectors_for("response_container")) == GeminiAdapter.response_container_selectors
    assert cfg.timeout_ms("submit_verify") == GeminiAdapter.submit_verify_timeout_ms
    assert cfg.timeout_ms("response") == GeminiAdapter.response_timeout_ms


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

def test_missing_required_key_raises(tmp_path: Path) -> None:
    bad = {"name": "no_schema", "provider": "x", "url": "https://x.test"}
    p = _write_yaml(tmp_path / "bad.yaml", bad)
    with pytest.raises(AdapterValidationError, match="schema_version"):
        load_adapter("bad", adapters_dir=tmp_path)


def test_too_new_schema_version_raises(tmp_path: Path) -> None:
    bad = _minimal_manifest(schema_version=99)
    _write_yaml(tmp_path / "bad.yaml", bad)
    with pytest.raises(AdapterValidationError, match="schema_version=99"):
        load_adapter("bad", adapters_dir=tmp_path)


def test_non_list_selectors_raises(tmp_path: Path) -> None:
    bad = _minimal_manifest(selectors={"input": "not_a_list"})
    _write_yaml(tmp_path / "bad.yaml", bad)
    with pytest.raises(AdapterValidationError, match="must be a list"):
        load_adapter("bad", adapters_dir=tmp_path)


def test_empty_selector_string_raises(tmp_path: Path) -> None:
    bad = _minimal_manifest(selectors={"input": ["", "  "]})
    _write_yaml(tmp_path / "bad.yaml", bad)
    with pytest.raises(AdapterValidationError, match="non-empty string"):
        load_adapter("bad", adapters_dir=tmp_path)


def test_negative_timeout_raises(tmp_path: Path) -> None:
    bad = _minimal_manifest(timeouts_ms={"page_load": -100})
    _write_yaml(tmp_path / "bad.yaml", bad)
    with pytest.raises(AdapterValidationError, match="positive int"):
        load_adapter("bad", adapters_dir=tmp_path)


def test_invalid_regex_raises(tmp_path: Path) -> None:
    bad = _minimal_manifest(session_url_pattern="(unclosed")
    _write_yaml(tmp_path / "bad.yaml", bad)
    with pytest.raises(AdapterValidationError, match="invalid regex"):
        load_adapter("bad", adapters_dir=tmp_path)


def test_unknown_regex_flag_raises(tmp_path: Path) -> None:
    bad = _minimal_manifest(
        session_url_pattern="/c/(.+)",
        session_url_pattern_flags=["BOGUS_FLAG"],
    )
    _write_yaml(tmp_path / "bad.yaml", bad)
    with pytest.raises(AdapterValidationError, match="unknown regex flag"):
        load_adapter("bad", adapters_dir=tmp_path)


def test_load_adapter_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_adapter("ghost", adapters_dir=tmp_path)


def test_load_all_adapters_skips_malformed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Malformed manifests are logged + skipped, not raised."""
    _write_yaml(tmp_path / "good.yaml", _minimal_manifest(name="good"))
    _write_yaml(tmp_path / "bad.yaml", _minimal_manifest(schema_version=99))
    out = load_all_adapters(adapters_dir=tmp_path)
    names = {c.name for c in out}
    assert names == {"good"}


# ---------------------------------------------------------------------------
# apply_to_class
# ---------------------------------------------------------------------------

def test_apply_to_class_mirrors_selectors() -> None:
    """Each YAML selector group lands on the canonical class attribute."""
    class _Stub:
        input_selectors = ()
        send_button_selectors = ()

    cfg = load_adapter("chatgpt")
    apply_to_class(_Stub, cfg)
    assert _Stub.input_selectors == tuple(cfg.selectors_for("input"))
    assert _Stub.send_button_selectors == tuple(cfg.selectors_for("send_button"))
    assert _Stub.name == "chatgpt"
    assert _Stub.provider == "openai"


def test_apply_to_class_mirrors_timeouts_and_session_pattern() -> None:
    class _Stub:
        page_load_timeout_ms = 0
        response_timeout_ms = 0
        session_url_pattern = None

    cfg = load_adapter("chatgpt")
    apply_to_class(_Stub, cfg)
    assert _Stub.page_load_timeout_ms == 25000
    assert _Stub.response_timeout_ms == 120000
    assert isinstance(_Stub.session_url_pattern, re.Pattern)
    assert _Stub.session_url_pattern.search("/c/abc-123") is not None


def test_apply_to_class_handles_unknown_selector_group_gracefully(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """An unmapped selector group becomes ``<group>_selectors`` + warns."""
    data = _minimal_manifest(
        name="future_site",
        selectors={"input": ["#x"], "future_thing": ["#y"]},
    )
    _write_yaml(tmp_path / "future_site.yaml", data)
    cfg = load_adapter("future_site", adapters_dir=tmp_path)

    class _Stub:
        input_selectors = ()

    with caplog.at_level("WARNING", logger="pce.adapter_loader"):
        apply_to_class(_Stub, cfg)
    assert _Stub.future_thing_selectors == ("#y",)  # type: ignore[attr-defined]
    assert any("future_thing" in m for m in caplog.messages)


def test_supported_selector_groups_cover_all_default_yaml_keys() -> None:
    """Every selector group mentioned in the shipped YAMLs has a Python attr mapping.

    Without this, P5.C.4.2 would silently ship YAML keys that don't
    project onto the existing class attributes — the refactor would
    appear to succeed but selectors wouldn't actually flow through.
    """
    seen: set[str] = set()
    for cfg in load_all_adapters():
        seen.update(cfg.selectors.keys())
    unmapped = seen - set(SELECTOR_GROUP_TO_ATTR.keys())
    assert not unmapped, (
        f"YAMLs use selector groups not in SELECTOR_GROUP_TO_ATTR: {unmapped} "
        f"— add them to pce_core/adapter_loader.py before P5.C.4.2"
    )
