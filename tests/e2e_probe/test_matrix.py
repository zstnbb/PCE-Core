# SPDX-License-Identifier: Apache-2.0
"""PCE Probe E2E matrix — site x case cross-product.

This is the ENTRY POINT for the full-coverage suite. It pulls every
adapter from ``sites/`` and every case from ``cases/`` and emits one
pytest test per pair, e.g. ``test_matrix[chatgpt-T01]``.

Running modes:

    pytest tests/e2e_probe/ --collect-only
        Enumerate the matrix without executing. Useful sanity check
        that every (site, case) pair is registered.

    pytest tests/e2e_probe/
        Run the matrix. Cases that need a logged-in browser SKIP
        cleanly when the user isn't signed in.

    pytest tests/e2e_probe/ -k "chatgpt-T01"
        Run a single cell.

    pytest tests/e2e_probe/ -k "chatgpt"
        Run all cases for one site.

    pytest tests/e2e_probe/ -k "T01"
        Run T01 across every site (= the basic-chat regression matrix).

The matrix produces a JSON summary at
``tests/e2e_probe/reports/<timestamp>/summary.json`` so a CI runner /
agent can ingest the result without parsing pytest output. The
summary writer fixture is in ``conftest.py``.
"""
from __future__ import annotations

import os
import time

import pytest

from pce_probe import ProbeClient

from .cases import ALL_CASES, BaseCase, CaseStatus
from .cases.base import CaseResult
from .execution_standard import (
    STANDARD_VERSION,
    is_external_skip,
    standard_for_case,
)
from .sites import ALL_SITES, BaseProbeSiteAdapter, site_by_name

# Inter-cell pacing (seconds). Sleep after each cell to keep us under
# rate-limit / anti-bot thresholds. Each fresh tab open + login +
# prompt + capture cycle is roughly 30-60 s; adding 3 s between cells
# yields ~1 cell / 35-65 s = 1-2 cells/min, well within the load any
# of the five sites tolerates from a logged-in user.
#
# Override via ``PCE_PROBE_INTER_CELL_PACING_S`` for slower hardware
# (e.g. CI containers behind a VPN). 0 disables pacing entirely
# (don't do this on a vertical run \u2014 see Pathology 3 in
# ``Docs/testing/PCE-PROBE-AGENT-LOOP.md``).
_INTER_CELL_PACING_S = float(
    os.environ.get("PCE_PROBE_INTER_CELL_PACING_S", "3.0")
)

_FIRST_ROUND_SITE_NAMES = (
    "chatgpt",
    "claude",
    "gemini",
    "grok",
    "googleaistudio",
)

_STRICT_STANDARD = os.environ.get("PCE_PROBE_STRICT_STANDARD", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _csv_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def _selected_sites() -> list[type[BaseProbeSiteAdapter]]:
    """Resolve the site set for this matrix run.

    Defaults to the registered full matrix. For the current v1 first
    pass, set ``PCE_PROBE_SITE_SET=first5`` or explicitly set
    ``PCE_PROBE_SITES=chatgpt,claude,gemini,grok,googleaistudio``.
    """
    names = _csv_env("PCE_PROBE_SITES")
    if not names and os.environ.get("PCE_PROBE_SITE_SET", "").lower() in {
        "first5",
        "core5",
        "v1",
    }:
        names = list(_FIRST_ROUND_SITE_NAMES)
    if not names:
        return list(ALL_SITES)

    selected: list[type[BaseProbeSiteAdapter]] = []
    missing: list[str] = []
    for name in names:
        try:
            selected.append(site_by_name(name))
        except KeyError:
            missing.append(name)
    if missing:
        known = ", ".join(cls.name for cls in ALL_SITES)
        raise ValueError(
            f"PCE_PROBE_SITES contains unknown site(s): {missing}; "
            f"known sites: {known}"
        )
    return selected


def _selected_cases() -> list[type[BaseCase]]:
    """Resolve the case set for this matrix run.

    ``PCE_PROBE_CASES=T00,T01,T10`` is intentionally supported as a
    first-class filter so attachment-only triage does not accidentally
    collect every advanced or account-scoped case.
    """
    ids = _csv_env("PCE_PROBE_CASES")
    if not ids:
        return list(ALL_CASES)
    selected: list[type[BaseCase]] = []
    missing: list[str] = []
    by_id = {cls.id.lower(): cls for cls in ALL_CASES}
    for case_id in ids:
        case_cls = by_id.get(case_id)
        if case_cls is None:
            missing.append(case_id)
        else:
            selected.append(case_cls)
    if missing:
        known = ", ".join(cls.id for cls in ALL_CASES)
        raise ValueError(
            f"PCE_PROBE_CASES contains unknown case id(s): {missing}; "
            f"known cases: {known}"
        )
    return selected


def _attach_execution_standard(result: CaseResult) -> CaseResult:
    std = standard_for_case(result.case_id)
    if std is None:
        return result
    result.details = dict(result.details or {})
    result.details.setdefault("execution_standard_version", STANDARD_VERSION)
    result.details.setdefault("execution_standard", std.brief())
    return result


def _apply_strict_standard(result: CaseResult) -> CaseResult:
    """Escalate standard-gated SKIPs when strict mode is enabled.

    This does not turn provider quota/login/account limitations into
    failures. It does prevent structured cases such as regenerate and
    branch flip from passing the run as "covered" when the missing part is
    our capture/storage/render contract.
    """
    if not _STRICT_STANDARD or result.status != CaseStatus.SKIP:
        return result
    std = standard_for_case(result.case_id)
    if std is None or not std.strict_gap_on_skip:
        return result
    if is_external_skip(result.case_id, result.summary):
        return result

    details = dict(result.details or {})
    details["strict_standard_escalated"] = True
    details["original_status"] = result.status.value
    details["original_summary"] = result.summary
    return CaseResult.failed(
        result.case_id,
        result.site_name,
        (
            f"strict execution standard not met for {result.case_id}: "
            f"{result.summary}"
        ),
        duration_ms=result.duration_ms,
        details=details,
    )

# ---------------------------------------------------------------------------
# Parametrization
# ---------------------------------------------------------------------------

# Generate one pytest.param per (site, case) cell. The id format
# ``<site>-<case>`` matches the convention used by the legacy Selenium
# suite reports so triagers can grep across both.
_MATRIX = [
    pytest.param(
        site_cls,
        case_cls,
        id=f"{site_cls.name}-{case_cls.id}",
    )
    for site_cls in _selected_sites()
    for case_cls in _selected_cases()
]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.pce_probe
@pytest.mark.parametrize("site_cls,case_cls", _MATRIX)
def test_probe_matrix(
    pce_probe_fresh: ProbeClient,
    pce_core,
    site_cls: type[BaseProbeSiteAdapter],
    case_cls: type[BaseCase],
    probe_matrix_summary,
) -> None:
    """One cell of the site x case matrix.

    Uses ``pce_probe_fresh`` so any tabs the case opens are closed
    before the next cell runs — otherwise a 14x20 matrix would leave
    280 stale AI tabs in the attached browser by session end.
    """
    adapter = site_cls()
    case = case_cls()

    result: CaseResult = case.run(pce_probe_fresh, pce_core, adapter)
    result = _attach_execution_standard(result)
    result = _apply_strict_standard(result)
    probe_matrix_summary.append(result)

    # Pacing: hold a moment before yielding back to pytest so the next
    # cell's tab.open doesn't slam the site immediately. Per redesign
    # Pathology 3, this matters most for ChatGPT (Cloudflare) and
    # Claude (Cloudflare). Skipped cells pay the toll too \u2014 a
    # SKIP usually involves a tab.open that already touched the site,
    # so the site has already noticed us.
    pacing_s = (
        adapter.inter_cell_pacing_s
        if adapter.inter_cell_pacing_s is not None
        else _INTER_CELL_PACING_S
    )
    if pacing_s > 0:
        time.sleep(pacing_s)

    if result.status == CaseStatus.PASS:
        return
    if result.status == CaseStatus.SKIP:
        pytest.skip(result.summary)
    if result.status == CaseStatus.XFAIL:
        pytest.xfail(result.summary)
    # CaseStatus.FAIL falls through to here.
    pytest.fail(result.summary)
