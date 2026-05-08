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
from .t02_streaming_complete import T02StreamingCompleteCase
from .t03_streaming_stop import T03StreamingStopCase
from .t04_new_chat_url import T04NewChatUrlCase
from .t05_code_block import T05CodeBlockCase
from .t06_thinking_model import T06ThinkingModelCase
from .t07_edit_user_message import T07EditUserMessageCase
from .t08_regenerate import T08RegenerateCase
from .t09_branch_flip import T09BranchFlipCase
from .t10_pdf_upload import T10PDFUploadCase
from .t11_image_upload import T11ImageUploadCase
from .t12_image_generation import T12ImageGenerationCase
from .t13_code_interpreter import T13CodeInterpreterCase
from .t14_web_search import T14WebSearchCase
from .t15_canvas import T15CanvasCase
from .t16_custom_gpt import T16CustomGPTCase
from .t17_project_chat import T17ProjectChatCase
from .t18_temporary_chat import T18TemporaryChatCase
from .t19_error_state import T19ErrorStateCase
from .t20_settings_silent import T20SettingsSilentCase

# Order matters: pytest collection / matrix output uses this ordering.
# Smoke first so a borked adapter fails fast before T01 even runs;
# then T01 (basic chat happy-path) before any of the surrounding
# T-cases that build on send_prompt + capture working. T20 sits last
# because a leaky T20 cannot be diagnosed without confirming the
# capture pipeline works at all (T01) \u2014 if T01 fails for a site,
# T20 is uninterpretable.
#
# Stage ordering rationale:
#   Stage 0 (smoke):                     T00
#   Stage 1 (chat lifecycle):            T01, T02, T03, T04, T05
#   Stage 2 (interaction):               T07, T08, T09
#   Stage 3 (attachments):               T10, T11
#   Stage 4 (advanced / paid features):  T06, T12, T13, T14, T15, T19
#   Stage 5 (account-scoped + privacy):  T16, T17, T18
#   Stage 0 (silence):                   T20
ALL_CASES: list[type[BaseCase]] = [
    T00SmokeCase,
    T01BasicChatCase,
    T02StreamingCompleteCase,
    T03StreamingStopCase,
    T04NewChatUrlCase,
    T05CodeBlockCase,
    T06ThinkingModelCase,
    T07EditUserMessageCase,
    T08RegenerateCase,
    T09BranchFlipCase,
    T10PDFUploadCase,
    T11ImageUploadCase,
    T12ImageGenerationCase,
    T13CodeInterpreterCase,
    T14WebSearchCase,
    T15CanvasCase,
    T16CustomGPTCase,
    T17ProjectChatCase,
    T18TemporaryChatCase,
    T19ErrorStateCase,
    T20SettingsSilentCase,
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
