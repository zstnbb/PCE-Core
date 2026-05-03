# SPDX-License-Identifier: Apache-2.0
"""Pytest config for the probe-driven E2E suite.

Activates the ``pce_probe`` plugin so tests can request the
``pce_probe`` / ``pce_probe_fresh`` fixtures. Agents that drive these
tests can opt out cleanly by passing ``--pce-probe-skip`` (defined in
``pce_probe.pytest_plugin``).

The PCE Core API (``http://127.0.0.1:9800``) is the verification
endpoint for round-trip assertions. We expose a thin ``pce_core``
fixture that returns an ``httpx.Client`` configured to bypass any
system proxy (the L1 mitmproxy proxy may otherwise loop traffic back
through itself).

Matrix summary
--------------
The ``probe_matrix_summary`` fixture (session-scoped) collects every
``CaseResult`` produced by ``test_matrix.py`` and writes a JSON
summary to ``tests/e2e_probe/reports/<UTC-timestamp>/summary.json`` at
session end. The format mirrors the legacy Selenium suite reports so a
single triage script can ingest both.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

import httpx
import pytest

from .execution_standard import STANDARD_VERSION, standards_for_case_ids

# Activate the probe plugin for this directory.
pytest_plugins = ["pce_probe.pytest_plugin"]


PCE_CORE_BASE_URL = os.environ.get("PCE_CORE_BASE_URL", "http://127.0.0.1:9800")
REPORTS_DIR = Path(__file__).resolve().parent / "reports"


@pytest.fixture(scope="session")
def pce_core() -> Iterator[httpx.Client]:
    """HTTP client for talking to PCE Core's local ingest/query API.

    ``trust_env=False`` deliberately bypasses HTTP_PROXY etc. so that
    when the L1 mitmproxy is on, our verification reads go straight to
    the loopback API instead of being captured back through the proxy
    (which would otherwise produce a recursive capture event).
    """
    with httpx.Client(
        base_url=PCE_CORE_BASE_URL,
        trust_env=False,
        timeout=10.0,
    ) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def _verify_pce_core_reachable(pce_core: httpx.Client) -> None:
    """Soft check — the probe E2E suite is meaningless if PCE Core
    isn't running, so we surface a clear skip rather than letting
    every test drown in connection errors.
    """
    try:
        r = pce_core.get("/api/v1/health", timeout=2.0)
        if r.status_code != 200:
            pytest.skip(
                f"PCE Core /api/v1/health returned {r.status_code}; "
                "start the server with `python -m pce_core.server`",
                allow_module_level=True,
            )
    except Exception as exc:
        pytest.skip(
            f"PCE Core not reachable at {PCE_CORE_BASE_URL} ({exc}); "
            "start the server with `python -m pce_core.server`",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# Matrix summary writer
# ---------------------------------------------------------------------------


class _MatrixSummary:
    """Append-only collector for ``CaseResult`` records.

    The session-scoped ``probe_matrix_summary`` fixture wraps one of
    these and flushes to JSON at teardown so a CI runner / agent can
    read the entire matrix outcome without parsing pytest output.
    Designed to be safe against partial writes — the JSON file is
    written atomically (via tmp + replace).
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.results: list[dict] = []
        self.started_at = time.time()

    def append(self, result) -> None:
        # ``CaseResult`` is a dataclass; ``CaseStatus`` is a str-enum.
        try:
            row = asdict(result)
        except TypeError:
            # Defensive: caller passed a non-dataclass; coerce to dict.
            row = dict(result.__dict__)  # type: ignore[arg-type]
        # Convert enum -> its string value so JSON serialises cleanly.
        status = row.get("status")
        if hasattr(status, "value"):
            row["status"] = status.value
        self.results.append(row)

    def write(self) -> Path | None:
        if not self.results:
            return None
        ts = time.strftime(
            "%Y%m%dT%H%M%S",
            time.gmtime(self.started_at),
        )
        run_dir = self.out_dir / ts
        run_dir.mkdir(parents=True, exist_ok=True)
        out_path = run_dir / "summary.json"
        tmp_path = run_dir / "summary.json.tmp"
        body = {
            "started_at_utc": ts,
            "duration_s": time.time() - self.started_at,
            "execution_standard_version": STANDARD_VERSION,
            "case_standards": standards_for_case_ids(
                sorted({str(r.get("case_id")) for r in self.results})
            ),
            "n_results": len(self.results),
            "by_status": _count_by(self.results, "status"),
            "by_site": _count_by(self.results, "site_name"),
            "by_case": _count_by(self.results, "case_id"),
            "results": self.results,
        }
        tmp_path.write_text(
            json.dumps(body, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(out_path)
        return out_path


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        v = r.get(key)
        if hasattr(v, "value"):
            v = v.value
        v = str(v) if v is not None else "<none>"
        counts[v] = counts.get(v, 0) + 1
    return counts


@pytest.fixture(scope="session")
def probe_matrix_summary() -> Iterator[_MatrixSummary]:
    """Session-scoped accumulator. Test cells append ``CaseResult``;
    teardown writes a single ``summary.json`` to ``reports/<ts>/``.

    Also prints a one-line summary to stdout at session end so a quick
    eyeball is enough to triage the run.
    """
    summary = _MatrixSummary(REPORTS_DIR)
    yield summary
    out = summary.write()
    if out is not None:
        by_status = _count_by(summary.results, "status")
        print(
            f"\nMATRIX SUMMARY: {len(summary.results)} cells, "
            f"by_status={by_status}, summary={out}",
        )
        # Surface the triage command verbatim so an agent reading
        # this output knows exactly what to run next -- no need to
        # remember the module path or hunt for the latest report dir.
        # See ``Docs/testing/PCE-PROBE-AGENT-LOOP.md`` section 1 step 2.
        if int(by_status.get("fail", 0)) > 0:
            print(
                "TRIAGE: python -m pce_probe.triage   "
                "(--include-skip to also surface SKIPs, --json for "
                "machine-readable output)"
            )
