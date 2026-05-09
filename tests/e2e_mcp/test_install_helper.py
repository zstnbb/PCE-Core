# SPDX-License-Identifier: Apache-2.0
"""Tests for ``pce_mcp_proxy.install`` — the one-shot helper that wraps
every MCP server in a supported host's config with ``pce_mcp_proxy``.

Covers (roughly in order of importance):

- wrap_server_entry: idempotency, self-skip (pce_mcp), no-command skip,
  env/cwd preservation, arg re-layout correctness.
- unwrap_server_entry: round-trip with wrap_server_entry.
- plan_install: action tally, diff summary, dry-run inertness.
- apply_install: backup creation, file write, no-op when nothing to change.
- Backup / restore round-trip (list_backups, restore_latest_backup).
- Host catalog: every host resolves at least one candidate; keys unique.
- CLI subcommands via subprocess: detect / install --dry-run / status.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pce_mcp_proxy import install as inst


# ---------------------------------------------------------------------------
# wrap_server_entry
# ---------------------------------------------------------------------------


def test_wrap_fresh_entry_produces_proxy_wrapper():
    entry = {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    }
    new_entry, action = inst.wrap_server_entry(
        "fs", entry, python_exe="/usr/bin/python3",
    )
    assert action == "wrapped"
    assert new_entry["command"] == "/usr/bin/python3"
    assert new_entry["args"] == [
        "-m", "pce_mcp_proxy",
        "--upstream-name", "fs",
        "--",
        "npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp",
    ]


def test_wrap_preserves_env_and_cwd():
    entry = {
        "command": "python",
        "args": ["-m", "mcp_git"],
        "env": {"GIT_PAGER": "cat"},
        "cwd": "/home/user/repo",
    }
    new_entry, action = inst.wrap_server_entry(
        "git", entry, python_exe="python3",
    )
    assert action == "wrapped"
    assert new_entry["env"] == {"GIT_PAGER": "cat"}
    assert new_entry["cwd"] == "/home/user/repo"


def test_wrap_is_idempotent_via_already_detection():
    entry = {
        "command": "npx",
        "args": ["-y", "@scope/server"],
    }
    first, action1 = inst.wrap_server_entry("s", entry, python_exe="py")
    assert action1 == "wrapped"
    second, action2 = inst.wrap_server_entry("s", first, python_exe="py")
    assert action2 == "already"
    assert second == first


def test_wrap_skips_pce_mcp_self():
    entry = {"command": "python", "args": ["-m", "pce_mcp"]}
    new_entry, action = inst.wrap_server_entry("pce", entry)
    assert action == "skip_self"
    assert new_entry == entry


def test_wrap_skips_entries_without_command():
    # Cursor / Windsurf allow remote SSE/HTTP MCP servers without command.
    entry = {"url": "https://example.com/mcp", "type": "sse"}
    new_entry, action = inst.wrap_server_entry("remote", entry)
    assert action == "skip_shape"
    assert new_entry == entry


def test_wrap_skips_non_dict_shapes():
    new_entry, action = inst.wrap_server_entry("weird", "not-a-dict")  # type: ignore[arg-type]
    assert action == "skip_shape"


# ---------------------------------------------------------------------------
# unwrap_server_entry
# ---------------------------------------------------------------------------


def test_unwrap_round_trips_wrap():
    original = {
        "command": "npx",
        "args": ["-y", "@scope/server", "arg1"],
        "env": {"FOO": "1"},
    }
    wrapped, _ = inst.wrap_server_entry("s", original, python_exe="/usr/bin/python3")
    unwrapped, action = inst.unwrap_server_entry(wrapped)
    assert action == "unwrapped"
    assert unwrapped["command"] == original["command"]
    assert unwrapped["args"] == original["args"]
    assert unwrapped.get("env") == original["env"]


def test_unwrap_noop_on_unwrapped_entry():
    entry = {"command": "npx", "args": ["-y", "@scope/server"]}
    new_entry, action = inst.unwrap_server_entry(entry)
    assert action == "noop"
    assert new_entry == entry


# ---------------------------------------------------------------------------
# plan_install
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_host(tmp_path):
    """Build a McpHost whose candidate points at tmp_path (unique per test)."""
    cfg_path = tmp_path / "fake_config.json"
    host = inst.McpHost(
        key="fake_host",
        display_name="Fake Host",
        tier="test",
        path_candidates=(lambda p=cfg_path: p,),  # closure captures cfg_path
    )
    return host, cfg_path


def test_plan_install_on_missing_config_reports_not_exists(fake_host):
    host, cfg_path = fake_host
    plan = inst.plan_install(host)
    assert plan.config_exists is False
    assert plan.wrapped_count == 0


def test_plan_install_wraps_only_non_pce_non_wrapped(fake_host):
    host, cfg_path = fake_host
    cfg_path.write_text(json.dumps({
        "mcpServers": {
            "pce": {"command": "python", "args": ["-m", "pce_mcp"]},
            "git": {"command": "python", "args": ["-m", "mcp_git"]},
            "already": {
                "command": "/usr/bin/python3",
                "args": ["-m", "pce_mcp_proxy", "--", "npx", "-y", "@scope/srv"],
            },
            "remote": {"url": "https://example.com/mcp"},
        }
    }, indent=2), encoding="utf-8")

    plan = inst.plan_install(host, python_exe="python3")
    assert plan.config_exists
    assert plan.actions == {
        "pce": "skip_self",
        "git": "wrapped",
        "already": "already",
        "remote": "skip_shape",
    }
    assert plan.wrapped_count == 1
    assert plan.already_count == 1
    assert plan.skipped_count == 2
    assert plan.has_changes


def test_plan_install_with_no_changes_has_changes_false(fake_host):
    host, cfg_path = fake_host
    cfg_path.write_text(json.dumps({
        "mcpServers": {
            "pce": {"command": "python", "args": ["-m", "pce_mcp"]},
        }
    }), encoding="utf-8")
    plan = inst.plan_install(host)
    assert not plan.has_changes


# ---------------------------------------------------------------------------
# apply_install + backup
# ---------------------------------------------------------------------------


def test_apply_install_writes_backup_and_config(fake_host):
    host, cfg_path = fake_host
    cfg_path.write_text(json.dumps({
        "mcpServers": {
            "git": {"command": "python", "args": ["-m", "mcp_git"]},
        }
    }), encoding="utf-8")
    plan = inst.plan_install(host, python_exe="py")
    backup = inst.apply_install(plan)
    assert backup is not None
    assert backup.is_file()
    assert cfg_path.is_file()

    # Backup has the ORIGINAL content
    backup_cfg = json.loads(backup.read_text(encoding="utf-8"))
    assert backup_cfg["mcpServers"]["git"]["args"] == ["-m", "mcp_git"]

    # Config has the WRAPPED content
    new_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    git = new_cfg["mcpServers"]["git"]
    assert git["args"][0:2] == ["-m", "pce_mcp_proxy"]
    assert git["args"][-3:] == ["--", "python", "-m"] or git["args"][-2:] == ["python", "-m"] or "python" in git["args"]


def test_apply_install_dry_run_does_not_touch_disk(fake_host):
    host, cfg_path = fake_host
    original_text = json.dumps({
        "mcpServers": {
            "git": {"command": "python", "args": ["-m", "mcp_git"]},
        }
    }, indent=2)
    cfg_path.write_text(original_text, encoding="utf-8")
    plan = inst.plan_install(host)
    result = inst.apply_install(plan, dry_run=True)
    assert result is None
    # File unchanged
    assert cfg_path.read_text(encoding="utf-8") == original_text


def test_apply_install_is_idempotent(fake_host):
    host, cfg_path = fake_host
    cfg_path.write_text(json.dumps({
        "mcpServers": {
            "git": {"command": "python", "args": ["-m", "mcp_git"]},
        }
    }), encoding="utf-8")
    # First install — actually wraps
    plan1 = inst.plan_install(host, python_exe="py")
    assert plan1.has_changes
    inst.apply_install(plan1)

    # Second install — everything already wrapped, no changes
    plan2 = inst.plan_install(host, python_exe="py")
    assert plan2.wrapped_count == 0
    assert plan2.already_count == 1
    assert not plan2.has_changes
    result = inst.apply_install(plan2)
    assert result is None  # no-op


def test_apply_install_missing_config_raises(fake_host):
    host, cfg_path = fake_host
    assert not cfg_path.is_file()
    plan = inst.plan_install(host)
    # has_changes is False but we still expect a raise if config is missing
    # when the user explicitly tries to apply — test that by constructing a
    # plan that *would* have changes if the file existed:
    plan_missing = inst.PlannedChange(
        host=host,
        config_path=cfg_path,
        config_exists=False,
        original_cfg={},
        new_cfg={"mcpServers": {"git": {"command": "x"}}},
        actions={"git": "wrapped"},
    )
    with pytest.raises(FileNotFoundError):
        inst.apply_install(plan_missing)


# ---------------------------------------------------------------------------
# Backup listing / restore
# ---------------------------------------------------------------------------


def test_list_backups_ordered_oldest_first(fake_host):
    host, cfg_path = fake_host
    cfg_path.write_text('{"mcpServers": {}}', encoding="utf-8")
    b1 = inst.write_backup(cfg_path)
    time.sleep(1.1)  # ensure distinct timestamp
    b2 = inst.write_backup(cfg_path)
    assert b1 is not None and b2 is not None
    backups = inst.list_backups(cfg_path)
    assert len(backups) == 2
    assert backups[0] == b1
    assert backups[-1] == b2


def test_restore_latest_backup_reverts_content(fake_host):
    host, cfg_path = fake_host
    cfg_path.write_text('{"original": true}', encoding="utf-8")
    backup = inst.write_backup(cfg_path)
    assert backup is not None
    # Mutate the config
    cfg_path.write_text('{"mutated": true}', encoding="utf-8")
    # Restore
    restored_from = inst.restore_latest_backup(cfg_path)
    assert restored_from == backup
    restored_text = cfg_path.read_text(encoding="utf-8")
    assert json.loads(restored_text) == {"original": True}


def test_backup_unique_when_called_rapidly(fake_host):
    host, cfg_path = fake_host
    cfg_path.write_text("{}", encoding="utf-8")
    # Call twice in the same second — code must use a counter suffix
    b1 = inst.write_backup(cfg_path)
    b2 = inst.write_backup(cfg_path)
    assert b1 is not None and b2 is not None
    assert b1 != b2


# ---------------------------------------------------------------------------
# Host catalog
# ---------------------------------------------------------------------------


def test_host_keys_are_unique():
    keys = [h.key for h in inst.HOSTS]
    assert len(keys) == len(set(keys))


def test_every_host_has_at_least_one_candidate():
    for host in inst.HOSTS:
        cands = host.candidates()
        assert cands, f"host {host.key} resolved zero candidates on this OS"


def test_detect_hosts_returns_one_row_per_host():
    rows = inst.detect_hosts()
    assert len(rows) == len(inst.HOSTS)
    # exists flag is deterministic per this machine; just assert shape
    for r in rows:
        assert r.host in inst.HOSTS
        assert isinstance(r.exists, bool)


# ---------------------------------------------------------------------------
# CLI via subprocess
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_cli(repo_root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "pce_mcp_proxy.install", *args]
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=full_env,
        timeout=30,
    )


def test_cli_detect_runs_and_lists_all_hosts(repo_root: Path):
    result = _run_cli(repo_root, "detect")
    assert result.returncode == 0, result.stderr
    assert "Detected MCP hosts:" in result.stdout
    # Every host's display_name appears
    for host in inst.HOSTS:
        assert host.display_name in result.stdout


def test_cli_status_runs_without_error(repo_root: Path):
    result = _run_cli(repo_root, "status")
    assert result.returncode == 0, result.stderr
    assert "PCE MCP proxy install status:" in result.stdout


def test_cli_install_requires_host_or_all(repo_root: Path):
    result = _run_cli(repo_root, "install")
    assert result.returncode == 2
    assert "--host" in result.stderr or "--all" in result.stderr


def test_cli_install_unknown_host_errors(repo_root: Path):
    result = _run_cli(repo_root, "install", "--host=definitely_not_a_real_host")
    assert result.returncode == 2
    assert "unknown host" in result.stderr.lower()


def test_cli_install_dry_run_does_not_mutate(tmp_path, repo_root: Path, monkeypatch):
    """End-to-end: point a host key at a tmp config, run install --dry-run,
    verify no mutation + no backup file created."""
    # Fake claude_desktop to a tmp path via env override — we patch the
    # host's resolver by monkeypatching _appdata_roaming etc. in a child
    # process using a dedicated env var. Since we can't easily monkeypatch
    # the subprocess, do this test as an in-process call to main() instead.
    cfg = tmp_path / "fake_claude.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "git": {"command": "python", "args": ["-m", "mcp_git"]},
        }
    }, indent=2), encoding="utf-8")

    # Build a fake host via monkeypatch of HOSTS_BY_KEY
    fake_host = inst.McpHost(
        key="_fake_for_dry_run",
        display_name="Fake",
        tier="test",
        path_candidates=(lambda p=cfg: p,),
    )
    monkeypatch.setitem(inst.HOSTS_BY_KEY, "_fake_for_dry_run", fake_host)
    monkeypatch.setattr(inst, "HOSTS", inst.HOSTS + (fake_host,))

    rc = inst.main(["install", "--host=_fake_for_dry_run", "--dry-run"])
    assert rc == 0
    # Config unchanged
    loaded = json.loads(cfg.read_text(encoding="utf-8"))
    assert loaded["mcpServers"]["git"]["args"] == ["-m", "mcp_git"]
    # No backup
    assert not inst.list_backups(cfg)
