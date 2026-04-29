# SPDX-License-Identifier: Apache-2.0
"""Tests for ``pce_probe.triage`` \u2014 the matrix summary triage CLI.

The triage tool reads a ``summary.json`` produced by the matrix runner
and emits a fix-ready view per FAIL/SKIP cell. These tests exercise:

  * ``triage_summary`` against synthetic JSON \u2014 happy path + edges
  * ``render_text`` / ``render_json`` formatting (smoke)
  * ``_latest_summary`` correctly picks the lex-newest run
  * UTF-8 BOM tolerance (Windows tools sometimes write one)
  * CLI exit code semantics (1 if any FAIL, else 0)

The fixtures use ``tmp_path`` so no state leaks into the workspace.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pce_probe.triage import (
    TriagedFailure,
    _latest_summary,
    main as triage_main,
    render_json,
    render_text,
    triage_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_summary(*, with_fail: bool = True) -> dict:
    """Build a synthetic summary.json body. ``with_fail=False`` returns
    an all-PASS summary used by the no-failures branch.
    """
    results = [
        {
            "case_id": "T00",
            "site_name": "chatgpt",
            "status": "pass",
            "summary": "ok",
            "duration_ms": 850,
            "details": {"pipeline": {"server_online": True}},
        },
    ]
    if with_fail:
        results.append(
            {
                "case_id": "T01",
                "site_name": "chatgpt",
                "status": "fail",
                "summary": (
                    "send_prompt: selector_not_found "
                    "(no element matched #prompt-textarea)"
                ),
                "duration_ms": 4200,
                "details": {
                    "phase": "send_prompt",
                    "code": "selector_not_found",
                    "agent_hint": "did the page change?",
                    "token": "PROBE-CHATGPT-T01-a3f9b2",
                    "tab_id": 12345,
                    "dom_excerpt": (
                        "<div class='wrap'><textarea "
                        "id='new-prompt-textarea'></textarea></div>"
                    ),
                },
            }
        )
        results.append(
            {
                "case_id": "T00",
                "site_name": "claude",
                "status": "skip",
                "summary": "not logged in / input not reachable",
                "duration_ms": 600,
                "details": {"phase": "login"},
            }
        )

    by_status: dict[str, int] = {}
    by_site: dict[str, int] = {}
    by_case: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_site[r["site_name"]] = by_site.get(r["site_name"], 0) + 1
        by_case[r["case_id"]] = by_case.get(r["case_id"], 0) + 1

    return {
        "started_at_utc": "20260429T120000",
        "duration_s": 187.4,
        "n_results": len(results),
        "by_status": by_status,
        "by_site": by_site,
        "by_case": by_case,
        "results": results,
    }


@pytest.fixture
def report_dir(tmp_path: Path) -> Path:
    """A scratch ``tests/e2e_probe/reports/<ts>/`` layout with a sites
    sibling so ``triage_summary`` can resolve ``edit_target`` paths.
    """
    e2e = tmp_path / "tests" / "e2e_probe"
    (e2e / "sites").mkdir(parents=True)
    (e2e / "sites" / "chatgpt.py").write_text("# stub", encoding="utf-8")
    (e2e / "sites" / "claude.py").write_text("# stub", encoding="utf-8")
    reports = e2e / "reports"
    reports.mkdir()
    return reports


def _write_summary(
    report_dir: Path,
    *,
    timestamp: str = "20260429T120000",
    body: dict | None = None,
    bom: bool = False,
) -> Path:
    run = report_dir / timestamp
    run.mkdir(parents=True, exist_ok=True)
    out = run / "summary.json"
    text = json.dumps(body if body is not None else _sample_summary(), indent=2)
    if bom:
        out.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    else:
        out.write_text(text, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# triage_summary \u2014 the core function
# ---------------------------------------------------------------------------


class TestTriageSummary:
    def test_happy_path_extracts_fail_only_by_default(self, report_dir: Path) -> None:
        summary_path = _write_summary(report_dir)
        sites_dir = report_dir.parent / "sites"

        out = triage_summary(summary_path, sites_dir=sites_dir)

        # PASS isn't a failure, SKIP excluded by default \u2192 only the FAIL row.
        assert isinstance(out["failures"], list)
        assert len(out["failures"]) == 1
        f = out["failures"][0]
        assert isinstance(f, TriagedFailure)
        assert f.cell_id == "chatgpt-T01"
        assert f.status == "fail"
        assert f.phase == "send_prompt"
        assert f.code == "selector_not_found"
        assert f.agent_hint == "did the page change?"
        assert f.replay_cmd == (
            'pytest tests/e2e_probe/test_matrix.py -k "chatgpt-T01" -v -s'
        )
        # edit_target points at the actual sites/chatgpt.py we created.
        assert f.edit_target.endswith("chatgpt.py")
        # dom_excerpt is preserved (under the truncation cap).
        assert "new-prompt-textarea" in f.evidence["dom_excerpt"]

    def test_include_skip_promotes_skip_rows(self, report_dir: Path) -> None:
        summary_path = _write_summary(report_dir)
        sites_dir = report_dir.parent / "sites"

        out = triage_summary(
            summary_path, sites_dir=sites_dir, include_skip=True
        )
        cells = sorted(f.cell_id for f in out["failures"])
        assert cells == ["chatgpt-T01", "claude-T00"]
        # SKIP row carries the right status string.
        skip = next(f for f in out["failures"] if f.cell_id == "claude-T00")
        assert skip.status == "skip"

    def test_no_failures_returns_empty_list(self, report_dir: Path) -> None:
        summary_path = _write_summary(
            report_dir, body=_sample_summary(with_fail=False)
        )
        sites_dir = report_dir.parent / "sites"
        out = triage_summary(summary_path, sites_dir=sites_dir)
        assert out["failures"] == []
        # by_status still flows through from the source.
        assert out["by_status"].get("pass") == 1

    def test_dom_excerpt_truncated_when_huge(self, report_dir: Path) -> None:
        body = _sample_summary()
        # Find the FAIL row and inflate its dom_excerpt.
        big = "x" * 5000
        for r in body["results"]:
            if r["status"] == "fail":
                r["details"]["dom_excerpt"] = big
        summary_path = _write_summary(report_dir, body=body)
        sites_dir = report_dir.parent / "sites"
        out = triage_summary(summary_path, sites_dir=sites_dir)
        excerpt = out["failures"][0].evidence["dom_excerpt"]
        # Triage cap is 600 chars + ellipsis marker; bigger than 600 should
        # have been clipped.
        assert len(excerpt) <= 700  # cap + a little slack for the ellipsis
        assert excerpt.endswith("\u2026")

    def test_recent_events_trimmed_to_last_ten(self, report_dir: Path) -> None:
        body = _sample_summary()
        # Stuff in 25 events; expect the triaged view to keep only 10.
        events = [
            {
                "kind": "PCE_CAPTURE",
                "host": "chatgpt.com",
                "provider": "openai",
                "session_hint": f"sess-{i}",
                "ts": 1700000000000 + i,
            }
            for i in range(25)
        ]
        for r in body["results"]:
            if r["status"] == "fail":
                r["details"]["recent_events"] = events
        summary_path = _write_summary(report_dir, body=body)
        sites_dir = report_dir.parent / "sites"
        out = triage_summary(summary_path, sites_dir=sites_dir)
        kept = out["failures"][0].evidence["recent_events"]
        assert len(kept) == 10
        # Triage keeps the LAST 10, so the first kept entry's session_hint
        # corresponds to events[15] = "sess-15".
        assert kept[0]["session_hint"] == "sess-15"

    def test_utf8_bom_is_tolerated(self, report_dir: Path) -> None:
        summary_path = _write_summary(report_dir, bom=True)
        sites_dir = report_dir.parent / "sites"
        # Should not raise json.decoder.JSONDecodeError on the BOM.
        out = triage_summary(summary_path, sites_dir=sites_dir)
        assert len(out["failures"]) == 1


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


class TestRenderers:
    def test_render_text_includes_replay_cmd_and_edit_target(
        self, report_dir: Path
    ) -> None:
        summary_path = _write_summary(report_dir)
        sites_dir = report_dir.parent / "sites"
        triaged = triage_summary(summary_path, sites_dir=sites_dir)
        text = render_text(triaged)
        assert "chatgpt-T01" in text
        assert "selector_not_found" in text
        assert 'pytest tests/e2e_probe/test_matrix.py -k "chatgpt-T01"' in text
        assert "chatgpt.py" in text

    def test_render_text_handles_empty_failures(self, report_dir: Path) -> None:
        summary_path = _write_summary(
            report_dir, body=_sample_summary(with_fail=False)
        )
        sites_dir = report_dir.parent / "sites"
        triaged = triage_summary(summary_path, sites_dir=sites_dir)
        text = render_text(triaged)
        assert "No failures" in text

    def test_render_json_is_well_formed(self, report_dir: Path) -> None:
        summary_path = _write_summary(report_dir)
        sites_dir = report_dir.parent / "sites"
        triaged = triage_summary(summary_path, sites_dir=sites_dir)
        out = render_json(triaged)
        parsed = json.loads(out)
        assert "report" in parsed
        assert "failures" in parsed
        assert parsed["failures"][0]["cell_id"] == "chatgpt-T01"


# ---------------------------------------------------------------------------
# _latest_summary \u2014 newest UTC-ts directory wins
# ---------------------------------------------------------------------------


class TestLatestSummary:
    def test_picks_lexicographically_newest_run(self, report_dir: Path) -> None:
        _write_summary(report_dir, timestamp="20260101T000000")
        _write_summary(report_dir, timestamp="20260601T120000")
        _write_summary(report_dir, timestamp="20260301T060000")
        latest = _latest_summary(report_dir)
        assert latest is not None
        assert latest.parent.name == "20260601T120000"

    def test_returns_none_when_dir_empty(self, tmp_path: Path) -> None:
        empty = tmp_path / "reports"
        empty.mkdir()
        assert _latest_summary(empty) is None

    def test_returns_none_when_dir_missing(self, tmp_path: Path) -> None:
        assert _latest_summary(tmp_path / "does-not-exist") is None

    def test_skips_run_dirs_without_summary_json(self, report_dir: Path) -> None:
        # A directory with no summary.json must not break the picker;
        # we should fall back to the next-newest one that does have it.
        (report_dir / "20260601T999999").mkdir()  # newer dir, but empty
        _write_summary(report_dir, timestamp="20260101T000000")
        latest = _latest_summary(report_dir)
        assert latest is not None
        assert latest.parent.name == "20260101T000000"


# ---------------------------------------------------------------------------
# CLI \u2014 exit code semantics
# ---------------------------------------------------------------------------


class TestCli:
    def test_exit_code_is_one_when_any_fail(
        self,
        report_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        summary_path = _write_summary(report_dir)
        sites_dir = report_dir.parent / "sites"
        rc = triage_main(
            [
                str(summary_path),
                "--sites-dir",
                str(sites_dir),
            ]
        )
        captured = capsys.readouterr()
        assert rc == 1
        assert "chatgpt-T01" in captured.out

    def test_exit_code_is_zero_when_no_fail(
        self,
        report_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        summary_path = _write_summary(
            report_dir, body=_sample_summary(with_fail=False)
        )
        sites_dir = report_dir.parent / "sites"
        rc = triage_main(
            [
                str(summary_path),
                "--sites-dir",
                str(sites_dir),
            ]
        )
        assert rc == 0

    def test_json_mode_emits_parseable_output(
        self,
        report_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        summary_path = _write_summary(report_dir)
        sites_dir = report_dir.parent / "sites"
        rc = triage_main(
            [
                str(summary_path),
                "--sites-dir",
                str(sites_dir),
                "--json",
            ]
        )
        out = capsys.readouterr().out
        parsed = json.loads(out)  # would raise if malformed
        assert rc == 1
        assert parsed["failures"][0]["cell_id"] == "chatgpt-T01"

    def test_explicit_summary_arg_wins_over_auto_locate(
        self,
        report_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Stage two different runs; pass the OLDER one explicitly. The
        # CLI must honour the explicit arg even though a newer run exists.
        old = _write_summary(
            report_dir,
            timestamp="20260101T000000",
            body=_sample_summary(with_fail=False),
        )
        _write_summary(report_dir, timestamp="20260601T120000")  # FAIL row
        sites_dir = report_dir.parent / "sites"
        rc = triage_main(
            [
                str(old),
                "--sites-dir",
                str(sites_dir),
            ]
        )
        # The older summary has no FAIL \u2192 exit code 0.
        assert rc == 0


# ---------------------------------------------------------------------------
# Module-as-script smoke (covers the ``if __name__ == "__main__"`` arm)
# ---------------------------------------------------------------------------


class TestModuleEntry:
    def test_main_with_explicit_args_does_not_raise(
        self,
        report_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        summary_path = _write_summary(report_dir)
        sites_dir = report_dir.parent / "sites"
        # Simulate ``python -m pce_probe.triage <args>`` argv.
        argv = [
            "pce_probe.triage",
            str(summary_path),
            "--sites-dir",
            str(sites_dir),
        ]
        old_argv = sys.argv
        try:
            sys.argv = argv
            rc = triage_main(argv[1:])
        finally:
            sys.argv = old_argv
        assert rc in (0, 1)
        capsys.readouterr()  # drain stdout
