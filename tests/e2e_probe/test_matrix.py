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

import pytest

from pce_probe import ProbeClient

from .cases import ALL_CASES, BaseCase, CaseStatus
from .cases.base import CaseResult
from .sites import ALL_SITES, BaseProbeSiteAdapter

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
    for site_cls in ALL_SITES
    for case_cls in ALL_CASES
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
    probe_matrix_summary.append(result)

    if result.status == CaseStatus.PASS:
        return
    if result.status == CaseStatus.SKIP:
        pytest.skip(result.summary)
    if result.status == CaseStatus.XFAIL:
        pytest.xfail(result.summary)
    # CaseStatus.FAIL falls through to here.
    pytest.fail(result.summary)
