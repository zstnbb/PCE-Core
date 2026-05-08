# SPDX-License-Identifier: Apache-2.0
"""T11 \u2014 image (vision) attachment in a user message.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 8 / T11, ``CLAUDE`` C11,
``GEMINI`` G11, ``GAS`` A08, ``GROK`` shared-T11. The user attaches a
small image and asks the model to describe it. PCE must capture the
user message with an ``image_url``-typed attachment in
``content_json.attachments``.

Uses the same DataTransfer-via-execute_js trick as T10 but routed to
``image_input_selectors`` (falling back to ``file_input_selectors``
when the site uses one shared input). The tiny PNG is built inline
using ``zlib`` + ``struct`` from the stdlib so the case is
fixture-free and portable.

Failure modes T11 catches:
  - DataTransfer trick blocked or wrong input selector \u2014 same
    diagnosis as T10.
  - Attachment captured but ``type`` is ``file`` instead of
    ``image_url`` (extractor didn't notice the MIME type).
  - Image upload routed via paste/drop on the contenteditable, not
    file input (Gemini Quill / Claude TipTap may need this) \u2014
    surfaces as ``no attachment landed`` and the matrix triage knows
    to prefer a paste-based path for that adapter.
"""
from __future__ import annotations

import base64
import json
import struct
import time
import uuid
import zlib

import httpx

from pce_probe import (
    CaptureNotSeenError,
    ProbeClient,
    ProbeError,
)

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


PROMPT_TEMPLATE = (
    "I just attached a small image named {filename}. Please reply "
    "with the literal word 'pong', a one-word color guess for the "
    "image, and the token {token}. Reply in one short line, no "
    "formatting, no quotes."
)


def _build_minimal_png(
    *,
    width: int = 64,
    height: int = 64,
    color: tuple = (220, 38, 38),
) -> bytes:
    """Return a uniformly-colored RGB PNG of the given size.

    Inline because we don't want to depend on Pillow in the test
    environment. The PNG format is small enough to construct by hand
    using stdlib ``struct`` + ``zlib``.

    Default 64x64 (verified 2026-05-03): 16x16 was historically
    enough for ChatGPT / Claude / Gemini, but Grok's upload pipeline
    silently rejects sub-32x32 images by leaving the send button
    ``disabled=""`` even after chip mount + composer text — diagnosis
    in ``.diag_grok_send_state.py``. 64x64 is small enough to be
    fixture-free (~190 bytes after IDAT compression) but large
    enough that every vision model tested accepts it.
    """
    raw = b""
    row = bytes(color) * width
    for _ in range(height):
        raw += b"\x00" + row  # leading filter byte (None) per PNG spec
    compressed = zlib.compress(raw)

    def _chunk(typ: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(typ + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", crc)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(
        ">IIBBBBB",
        width, height,
        8,    # bit depth
        2,    # color type 2 = RGB (no alpha)
        0,    # compression
        0,    # filter
        0,    # interlace
    )
    return (
        signature
        + _chunk(b"IHDR", ihdr_data)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


class T11ImageUploadCase(BaseCase):
    id = "T11"
    name = "image_attachment_in_user_message"
    description = (
        "Build a tiny RGB PNG, attach it via the page's image / file "
        "input, send a prompt referencing the filename, verify the "
        "user message in PCE Core has an ``image_url`` attachment."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        # T11 falls back to ``file_input_selectors`` if no dedicated
        # image input is configured. Both empty \u2014 SKIP.
        if not (adapter.image_input_selectors or adapter.file_input_selectors):
            return CaseResult.skipped(
                self.id, adapter.name,
                "adapter has neither image_input_selectors nor "
                "file_input_selectors; site has no exposed file input",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T11-{uuid.uuid4().hex[:8]}"
        filename = f"pce_probe_t11_{token}.png"
        prompt = PROMPT_TEMPLATE.format(filename=filename, token=token)

        png_bytes = _build_minimal_png()
        png_b64 = base64.b64encode(png_bytes).decode("ascii")
        details: dict = {
            "token": token,
            "filename": filename,
            "png_size_bytes": len(png_bytes),
        }

        # 1. Open + login.
        try:
            tab_id = adapter.open(probe)
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"adapter.open failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "open"},
            )
        login = adapter.check_logged_in(probe, tab_id)
        details["login_detail"] = login.detail
        if not login.logged_in:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"not logged in: {login.detail}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "login"},
            )

        # 2. Inject the PNG. Prefer paste (universal — every site
        #    that supports screenshot paste handles this path); fall
        #    back to file-input only if paste returns False. See
        #    T10 for the rationale: modern ChatGPT silently drops
        #    plain ``<input type=file>`` ``change`` events.
        upload_via = "paste"
        attached = adapter.upload_file_via_paste(
            probe, tab_id,
            file_name=filename,
            file_b64=png_b64,
            media_type="image/png",
            image=True,
        )
        if not attached:
            upload_via = "input"
            attached = adapter.upload_file_via_input(
                probe, tab_id,
                file_name=filename,
                file_b64=png_b64,
                media_type="image/png",
                image=True,
            )
        details["upload_via"] = upload_via
        details["upload_returned"] = attached
        if not attached:
            return CaseResult.failed(
                self.id, adapter.name,
                "both upload_file_via_paste and upload_file_via_input "
                "returned False; adapter has no input_selectors AND "
                "no image_input_selectors / file_input_selectors",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "upload_failed"},
            )

        # 3. Send the prompt and wait for the assistant to finish
        #    describing the image. Vision processing is slower than
        #    plain chat (~10-25 s typical); we keep the standard
        #    response_timeout_ms in adapter and let wait_for_done
        #    do its thing.
        time.sleep(2.0)  # let preview thumbnail mount
        try:
            adapter.send_prompt(probe, tab_id, prompt)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"send_prompt: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "send_prompt",
                    "send_prompt_context": exc.context or {},
                    "send_prompt_agent_hint": exc.agent_hint,
                },
            )
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_done"},
            )

        # 4. Wait for capture. Image+vision turns can take 30-60 s
        #    under load (OCR / preview upload / vision routing), and
        #    the SW capture only fires after the assistant turn is
        #    complete enough for the DOM extractor. A fixed 45 s
        #    window is still tight for Grok during a cross-site run,
        #    so mirror T10 and scale by the adapter's response budget.
        capture_timeout_ms = max(60_000, min(adapter.response_timeout_ms, 90_000))
        try:
            captured = probe.capture.wait_for_token(
                token,
                timeout_ms=capture_timeout_ms,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
        except CaptureNotSeenError as exc:
            ctx = exc.context or {}
            recent = ctx.get("last_capture_events") or []
            return CaseResult.failed(
                self.id, adapter.name,
                f"capture.wait_for_token: {exc.message}; "
                f"recent({len(recent)})",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "no_capture",
                    "agent_hint": exc.agent_hint,
                    "recent_events": recent[-10:],
                },
            )
        except ProbeError as exc:
            ctx = exc.context or {}
            recent = ctx.get("last_capture_events") or []
            if not recent:
                try:
                    recent = (
                        probe.capture.recent_events(
                            last_n=200,
                            provider=adapter.provider,
                        ).get("events", [])
                        or []
                    )
                except ProbeError:
                    recent = []
            return CaseResult.failed(
                self.id, adapter.name,
                f"capture.wait_for_token: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "wait_for_token_error",
                    "recent_events": recent[-10:],
                    "capture_timeout_ms": capture_timeout_ms,
                },
            )

        # 5. Round-trip via PCE Core.
        session_hint = captured.get("session_hint")
        if not session_hint:
            return CaseResult.failed(
                self.id, adapter.name,
                "captured event had no session_hint",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "session_hint_missing"},
            )

        sessions: list = []
        matching: list = []
        for _attempt in range(6):
            try:
                resp = pce_core.get(
                    "/api/v1/sessions",
                    params={"provider": adapter.provider},
                )
            except httpx.HTTPError as exc:
                return CaseResult.failed(
                    self.id, adapter.name,
                    f"PCE Core /api/v1/sessions failed: {exc!r}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_request"},
                )
            if resp.status_code != 200:
                return CaseResult.failed(
                    self.id, adapter.name,
                    f"sessions API returned {resp.status_code}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_status"},
                )
            sessions = resp.json() or []
            matching = [
                s for s in sessions
                if str(s.get("session_key", "")).find(str(session_hint)) != -1
            ]
            if matching:
                break
            time.sleep(0.5)
        if not matching:
            return CaseResult.failed(
                self.id, adapter.name,
                f"no PCE Core session matched session_hint={session_hint!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "session_match_missed"},
            )

        session_id = matching[0].get("id")
        msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
        if msg_resp.status_code != 200:
            return CaseResult.failed(
                self.id, adapter.name,
                f"messages API returned {msg_resp.status_code}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "messages_status"},
            )
        msgs = msg_resp.json() or []
        details["n_messages"] = len(msgs)

        # 6. Find the user message and inspect attachments.
        target = None
        for m in msgs:
            if m.get("role") != "user":
                continue
            if token in (m.get("content_text") or ""):
                target = m
                break
        if target is None:
            previews = [
                (m.get("role"), (m.get("content_text") or "")[:80])
                for m in msgs
            ]
            return CaseResult.failed(
                self.id, adapter.name,
                f"no user message contains the token {token!r}; "
                f"preview={previews!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "user_token_missing"},
            )

        attachments = self._extract_attachments(target)
        details["attachments"] = attachments
        if not attachments:
            return CaseResult.failed(
                self.id, adapter.name,
                "user message has no attachments captured. The image "
                "was attached at the DOM level (chip rendered, prompt "
                "sent successfully) but the SW extractor lost the "
                "attachment metadata during normalization. This is the "
                "C11 / G11 reconciler-join gap if Claude/Gemini, or "
                "an extractor regression for ChatGPT/GAS",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "attachments_empty"},
            )

        # 7. Type check: at least one attachment must be 'image_url'
        #    (the canonical type for vision attachments per the
        #    rich-content schema).
        image_atts = [
            a for a in attachments
            if str(a.get("type", "")).lower() == "image_url"
        ]
        if not image_atts:
            return CaseResult.failed(
                self.id, adapter.name,
                f"user has {len(attachments)} attachment(s) but none "
                f"are type 'image_url'; types found="
                f"{[a.get('type') for a in attachments]!r}; "
                f"the extractor mis-classified the image (e.g. as "
                f"'file' or generic 'document')",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wrong_attachment_type"},
            )

        # 8. Soft check: media_type round-trip. Some extractors leave
        #    media_type as null even when the type itself is correct.
        media_round_trip = any(
            "image" in str(a.get("media_type") or "").lower()
            for a in image_atts
        )
        details["media_type_round_trip"] = media_round_trip

        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"image roundtripped; session_id={session_id}, "
                f"attachments={len(attachments)} "
                f"(image_url={len(image_atts)}), "
                f"media_type_round_trip={media_round_trip}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )

    @staticmethod
    def _extract_attachments(message: dict) -> list:
        cj = message.get("content_json")
        if isinstance(cj, str):
            try:
                cj = json.loads(cj)
            except (TypeError, ValueError):
                return []
        if not isinstance(cj, dict):
            return []
        atts = cj.get("attachments") or []
        return atts if isinstance(atts, list) else []
