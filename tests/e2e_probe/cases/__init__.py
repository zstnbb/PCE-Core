# SPDX-License-Identifier: Apache-2.0
"""Site-agnostic case templates for the PCE Probe E2E matrix.

Each case is a single ``BaseCase`` subclass that knows how to verify
ONE behaviour (basic chat, image upload, regenerate, branch flip, etc.)
against ANY site that exposes the standard ``BaseProbeSiteAdapter``
contract. The matrix runner takes the cross-product of ``ALL_SITES`` x
``ALL_CASES`` and runs each combination as a separate pytest item.

Adding a new case:
    1. Drop a new ``tNN_xxx.py`` next to the existing ones.
    2. Define ``TNNCase(BaseCase)`` with at minimum a unique ``id`` /
       ``name`` and a ``run()`` implementation.
    3. Append ``TNNCase`` to ``ALL_CASES`` below.

Cases SHOULD be idempotent and should NOT depend on previous-case
state. The matrix opens a fresh tab per (site, case) pair so every
case starts from a known-good entry URL.
"""
from __future__ import annotations

from .base import BaseCase, CaseResult, CaseStatus
from .t00_smoke import T00SmokeCase
from .t01_basic_chat import T01BasicChatCase

# Order matters: pytest collection / matrix matrix output uses this
# ordering. Smoke first so a borked adapter fails fast before T01 even
# runs.
ALL_CASES: list[type[BaseCase]] = [
    T00SmokeCase,
    T01BasicChatCase,
]


def case_by_id(case_id: str) -> type[BaseCase]:
    """Return the case class whose ``id`` attribute matches.

    Raises ``KeyError`` if no match.
    """
    for cls in ALL_CASES:
        if cls.id == case_id:
            return cls
    raise KeyError(f"no case registered with id={case_id!r}")


__all__ = [
    "ALL_CASES",
    "BaseCase",
    "CaseResult",
    "CaseStatus",
    "case_by_id",
]
