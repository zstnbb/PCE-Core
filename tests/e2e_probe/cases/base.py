# SPDX-License-Identifier: Apache-2.0
"""Base case for the probe-driven E2E matrix.

A "case" is a single behavioural assertion (basic chat, regenerate,
image upload, branch flip, ...) that should hold against ANY site
exposing the ``BaseProbeSiteAdapter`` contract. The matrix runner
opens one fresh tab per (site, case) pair, hands the case both the
``ProbeClient`` and the site adapter, and lets the case decide how to
exercise + verify the site.

A case's ``run()`` returns a ``CaseResult`` with a ``CaseStatus``. The
matrix runner translates these into pytest verdicts:

    PASS   -> assertion in the test body passes
    FAIL   -> ``pytest.fail(result.summary)``
    SKIP   -> ``pytest.skip(result.summary)``  (e.g. not logged in)
    XFAIL  -> ``pytest.xfail(result.summary)`` (e.g. known UI quirk)

The case body should NEVER catch ``Exception`` blindly — let probe
errors propagate so the report carries their full ``agent_hint`` /
``context``. Wrap site-specific recoverable cases (login wall) in the
``CaseStatus.SKIP`` path.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Optional

if TYPE_CHECKING:
    import httpx
    from pce_probe import ProbeClient
    from ..sites.base import BaseProbeSiteAdapter


class CaseStatus(str, Enum):
    """Outcome of a single (site, case) pair."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    XFAIL = "xfail"


@dataclass
class CaseResult:
    """Result of running one (site, case) combination.

    ``summary`` is the one-line message surfaced into pytest's verdict
    (skip reason / fail message). ``details`` holds machine-readable
    context the matrix may want to dump into a JSON summary file.
    """

    case_id: str
    site_name: str
    status: CaseStatus
    summary: str = ""
    duration_ms: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def passed(
        cls,
        case_id: str,
        site_name: str,
        *,
        summary: str = "",
        duration_ms: int = 0,
        details: Optional[dict[str, Any]] = None,
    ) -> "CaseResult":
        return cls(
            case_id=case_id,
            site_name=site_name,
            status=CaseStatus.PASS,
            summary=summary or "ok",
            duration_ms=duration_ms,
            details=details or {},
        )

    @classmethod
    def failed(
        cls,
        case_id: str,
        site_name: str,
        summary: str,
        *,
        duration_ms: int = 0,
        details: Optional[dict[str, Any]] = None,
    ) -> "CaseResult":
        return cls(
            case_id=case_id,
            site_name=site_name,
            status=CaseStatus.FAIL,
            summary=summary,
            duration_ms=duration_ms,
            details=details or {},
        )

    @classmethod
    def skipped(
        cls,
        case_id: str,
        site_name: str,
        summary: str,
        *,
        duration_ms: int = 0,
        details: Optional[dict[str, Any]] = None,
    ) -> "CaseResult":
        return cls(
            case_id=case_id,
            site_name=site_name,
            status=CaseStatus.SKIP,
            summary=summary,
            duration_ms=duration_ms,
            details=details or {},
        )


class BaseCase:
    """Abstract per-case driver.

    Subclasses MUST set ``id`` (e.g. ``"T01"``) and ``name`` (short
    human-readable label) and implement ``run()``.
    """

    id: ClassVar[str] = "T??"
    name: ClassVar[str] = "unnamed"
    description: ClassVar[str] = ""

    def run(
        self,
        probe: "ProbeClient",
        pce_core: "httpx.Client",
        adapter: "BaseProbeSiteAdapter",
    ) -> CaseResult:  # pragma: no cover - subclass responsibility
        """Execute the case against ``adapter`` (already on the active
        site) and return a ``CaseResult``.

        Implementations get:

          * ``probe``    — connected ``ProbeClient`` (extension attached)
          * ``pce_core`` — ``httpx.Client`` to ``http://127.0.0.1:9800``
          * ``adapter``  — site adapter instance (selectors etc.)

        Implementations are responsible for opening a tab, doing the
        site-specific dance, and verifying via PCE Core's API. The
        runner closes the tab afterwards, so cases needn't clean up.
        """
        raise NotImplementedError

    # ---- helpers shared by case implementations --------------------------

    @staticmethod
    def _start_timer() -> float:
        return time.time()

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.time() - start) * 1_000)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} id={self.id!r}>"
