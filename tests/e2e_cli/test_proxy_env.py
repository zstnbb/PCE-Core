# SPDX-License-Identifier: Apache-2.0
"""Tests for pce_cli_wrapper._proxy_env — child env augmentation.

The augment_child_env() function is the L1 enablement seam for the
three wrapped CLIs: it injects HTTPS_PROXY family + NODE_OPTIONS
(--tls-keylog + --require=undici-shim) into the child env when the
operator opts in via PCE_CLI_WRAPPER_PROXY / SSLKEYLOGFILE.

These tests exercise the policy without spawning real subprocesses.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pce_cli_wrapper._proxy_env import (
    NODE_TARGET_IDS,
    PROXY_ENV_TRIGGER,
    PROXY_TARGET_ENV_KEYS,
    augment_child_env,
    proxy_inject_script_path,
)


# ----------------------------------------------------------------------
# Static catalogue invariants
# ----------------------------------------------------------------------


def test_node_target_ids_match_discovery():
    """The Node augmentation set must include all three known CLIs.

    discovery._TARGETS is the source of truth for which command names
    pce_cli_wrapper ships shims for. If a fourth Node CLI is added
    there, NODE_TARGET_IDS must extend; otherwise that CLI silently
    skips the keylog / undici-proxy injection.
    """
    from pce_cli_wrapper.discovery import known_targets

    target_ids = {t.target_id for t in known_targets()}
    assert NODE_TARGET_IDS == target_ids, (
        f"_proxy_env.NODE_TARGET_IDS={NODE_TARGET_IDS} drifted from "
        f"discovery target catalogue {target_ids}. Update one of them."
    )


def test_proxy_inject_script_exists():
    """The undici proxy-inject require shim must ship with the wheel."""
    p = proxy_inject_script_path()
    assert p.is_file(), f"missing shim {p}"
    body = p.read_text(encoding="utf-8")
    # Mandatory shape — guards against accidental no-op shim.
    assert "ProxyAgent" in body
    assert "setGlobalDispatcher" in body
    # Safety contract — shim must wrap everything in try/catch so a
    # broken require never breaks the user's CLI invocation.
    assert "try {" in body and "catch" in body


# ----------------------------------------------------------------------
# augment_child_env policy
# ----------------------------------------------------------------------


def test_no_trigger_means_no_changes():
    """Without PCE_CLI_WRAPPER_PROXY or SSLKEYLOGFILE, env is untouched."""
    base = {"FOO": "bar", "PATH": "/usr/bin"}
    out = augment_child_env(target_id="claude-code", base_env=base)
    assert out == base
    # And SHALL NOT contain proxy keys we did not have to begin with.
    for k in PROXY_TARGET_ENV_KEYS:
        assert k not in out


def test_proxy_trigger_exports_all_four_proxy_keys():
    """PCE_CLI_WRAPPER_PROXY → HTTPS_PROXY + HTTP_PROXY + lowercase."""
    base = {PROXY_ENV_TRIGGER: "http://127.0.0.1:8080"}
    out = augment_child_env(target_id="claude-code", base_env=base)
    for k in PROXY_TARGET_ENV_KEYS:
        assert out[k] == "http://127.0.0.1:8080", (
            f"key {k!r} missing or wrong"
        )


def test_existing_https_proxy_not_overwritten():
    """If the operator already set HTTPS_PROXY directly, we don't fight."""
    base = {
        PROXY_ENV_TRIGGER: "http://127.0.0.1:8080",
        "HTTPS_PROXY": "http://corp-proxy:3128",
    }
    out = augment_child_env(target_id="claude-code", base_env=base)
    assert out["HTTPS_PROXY"] == "http://corp-proxy:3128"
    # Lower-case variant still gets set because it wasn't there.
    assert out["https_proxy"] == "http://127.0.0.1:8080"


def test_sslkeylog_only_node_targets_get_tls_keylog():
    """NODE_OPTIONS=--tls-keylog appears only for Node targets."""
    base = {"SSLKEYLOGFILE": "/tmp/keys.log"}

    # Node target: gets the flag.
    out_node = augment_child_env(target_id="claude-code", base_env=base)
    assert "--tls-keylog=/tmp/keys.log" in out_node["NODE_OPTIONS"]

    # Non-Node target id: untouched.
    out_other = augment_child_env(target_id="some-future-rust-cli", base_env=base)
    assert "NODE_OPTIONS" not in out_other


def test_sslkeylog_path_with_spaces_is_quoted():
    """NODE_OPTIONS is shell-style; paths with spaces must be quoted."""
    base = {"SSLKEYLOGFILE": r"C:\Program Files\PCE\keys.log"}
    out = augment_child_env(target_id="gemini-cli", base_env=base)
    # Quoted form preserved verbatim.
    assert '--tls-keylog="C:\\Program Files\\PCE\\keys.log"' in out["NODE_OPTIONS"]


def test_existing_node_options_preserved_and_appended():
    """We append to NODE_OPTIONS, never replace operator-set flags."""
    base = {
        "SSLKEYLOGFILE": "/tmp/k.log",
        "NODE_OPTIONS": "--max-old-space-size=4096",
    }
    out = augment_child_env(target_id="claude-code", base_env=base)
    assert "--max-old-space-size=4096" in out["NODE_OPTIONS"]
    assert "--tls-keylog=/tmp/k.log" in out["NODE_OPTIONS"]


def test_tls_keylog_not_duplicated_on_re_augment():
    """augment_child_env is idempotent — running twice produces same env."""
    base = {"SSLKEYLOGFILE": "/tmp/k.log"}
    once = augment_child_env(target_id="codex-cli", base_env=base)
    twice = augment_child_env(target_id="codex-cli", base_env=once)
    # Count occurrences — must be exactly one regardless of pass count.
    assert twice["NODE_OPTIONS"].count("--tls-keylog") == 1


def test_undici_shim_required_only_when_proxy_set():
    """The --require=shim flag appears only with PCE_CLI_WRAPPER_PROXY set."""
    # Keylog alone → no --require flag.
    base_kl = {"SSLKEYLOGFILE": "/tmp/k.log"}
    out_kl = augment_child_env(target_id="gemini-cli", base_env=base_kl)
    assert "--require=" not in out_kl.get("NODE_OPTIONS", "")

    # Proxy set → --require=<shim> appears (path resolved at call time).
    base_pr = {PROXY_ENV_TRIGGER: "http://127.0.0.1:8080"}
    out_pr = augment_child_env(target_id="gemini-cli", base_env=base_pr)
    expected_substr = "_undici_proxy_inject.js"
    assert expected_substr in out_pr.get("NODE_OPTIONS", ""), (
        f"NODE_OPTIONS={out_pr.get('NODE_OPTIONS')!r} missing undici shim"
    )


def test_undici_shim_not_duplicated_on_re_augment():
    """Same idempotence guarantee for --require= flag."""
    base = {PROXY_ENV_TRIGGER: "http://127.0.0.1:8080"}
    once = augment_child_env(target_id="claude-code", base_env=base)
    twice = augment_child_env(target_id="claude-code", base_env=once)
    assert twice["NODE_OPTIONS"].count("--require=") == 1


def test_target_id_none_treated_as_non_node():
    """If discovery has no target_id, skip Node-specific augmentation.

    This is the safety path: a future caller may pass None when the
    shim is invoked under some non-catalogued situation. We must not
    crash, and we must not silently inject NODE_OPTIONS into a process
    that may not be Node.
    """
    base = {
        PROXY_ENV_TRIGGER: "http://127.0.0.1:8080",
        "SSLKEYLOGFILE": "/tmp/k.log",
    }
    out = augment_child_env(target_id=None, base_env=base)
    # Proxy family still set (target-agnostic).
    assert out["HTTPS_PROXY"] == "http://127.0.0.1:8080"
    # NODE_OPTIONS NOT set (target-specific).
    assert "NODE_OPTIONS" not in out


def test_does_not_mutate_input_base_env():
    """augment_child_env is pure — input dict is left intact."""
    base = {
        PROXY_ENV_TRIGGER: "http://127.0.0.1:8080",
        "SSLKEYLOGFILE": "/tmp/k.log",
    }
    snapshot = dict(base)
    _ = augment_child_env(target_id="claude-code", base_env=base)
    assert base == snapshot, "base_env was mutated"


def test_falls_back_to_os_environ_when_base_env_none(monkeypatch):
    """If caller omits base_env, function reads os.environ."""
    monkeypatch.setenv(PROXY_ENV_TRIGGER, "http://localhost:8080")
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    # Clear any pre-existing HTTPS_PROXY (e.g. operator's Clash setup
    # at 127.0.0.1:7890) so we exercise the trigger → export path
    # rather than test_existing_https_proxy_not_overwritten's "don't
    # fight operator" path.
    for k in PROXY_TARGET_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    out = augment_child_env(target_id="claude-code")
    assert out["HTTPS_PROXY"] == "http://localhost:8080"
