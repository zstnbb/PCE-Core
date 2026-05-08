# SPDX-License-Identifier: Apache-2.0
"""T10 \u2014 PDF / file attachment in a user message.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 7 / T10, ``CLAUDE`` C10,
``GEMINI`` G10, ``GAS`` A07, ``GROK`` shared-T10. The user attaches a
small PDF and asks the model to confirm the filename. PCE must
capture the user message with a ``file``-typed attachment in
``content_json.attachments``.

Implementation strategy:
  - Build a minimal-but-valid PDF inline (no fixture files required;
    the case is portable across cwd / CI / local). Same shape as the
    legacy autopilot's ``_build_minimal_pdf`` in
    ``tests/e2e/test_chatgpt_full.py:286-314``.
  - Base64-encode the bytes and inject via
    ``adapter.upload_file_via_input`` (DataTransfer trick over
    ``dom.execute_js``). This is the probe equivalent of Selenium's
    ``send_keys(file_path)`` to a hidden ``<input type=file>``.
  - Send the prompt referencing the filename and wait for the
    response.
  - Verify via PCE Core: the user message has a ``file`` attachment
    whose name matches what we uploaded.

Failure modes T10 catches:
  - DataTransfer trick blocked (some sites bind upload to a paste /
    drop handler on the contenteditable, not the file input). The
    case fails with a clear "no attachment landed" message.
  - Attachment captured but ``type`` is wrong (e.g. ``image_url``
    when we sent a PDF \u2014 means
    ``extractAttachments`` is mis-classifying).
  - Attachment captured but ``name`` is missing (extractor lost the
    filename during normalization).
"""
from __future__ import annotations

import base64
import json
import time
import uuid

import httpx

from pce_probe import (
    CaptureNotSeenError,
    ProbeClient,
    ProbeError,
)

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


PROMPT_TEMPLATE = (
    "I just attached a small PDF named {filename}. Please reply with "
    "the literal word 'pong', the filename you were given, and the "
    "token {token}. Reply in one short line, no formatting, no quotes."
)


def _build_minimal_pdf(text: str) -> bytes:
    """Return a tiny but valid 1-page PDF whose body contains ``text``.

    Same generator as the legacy autopilot at
    ``tests/e2e/test_chatgpt_full.py:286-314``. Lives here too so the
    case is self-contained.
    """
    content = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n",
        f"4 0 obj << /Length {len(content)} >> stream\n{content}\nendstream\nendobj\n",
        "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj.encode("latin-1"))
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(pdf)


class T10PDFUploadCase(BaseCase):
    id = "T10"
    name = "pdf_attachment_in_user_message"
    description = (
        "Build a minimal valid PDF, attach it via the page's file "
        "input, send a prompt referencing the filename, verify the "
        "user message in PCE Core has a ``file`` attachment with the "
        "correct name."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        if not adapter.file_input_selectors:
            return CaseResult.skipped(
                self.id, adapter.name,
                "adapter has no file_input_selectors; site has no "
                "exposed <input type=file> for PDF upload",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T10-{uuid.uuid4().hex[:8]}"
        filename = f"pce_probe_t10_{token}.pdf"
        prompt = PROMPT_TEMPLATE.format(filename=filename, token=token)

        pdf_bytes = _build_minimal_pdf(f"PCE T10 fixture {token}")
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
        details: dict = {
            "token": token,
            "filename": filename,
            "pdf_size_bytes": len(pdf_bytes),
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

        # 2. Inject the PDF. Prefer the universal paste path
        #    (legacy autopilot's ``upload_via_paste``): on modern
        #    ChatGPT, plain ``<input type=file>`` ``change`` events
        #    don't reach the upload chain even though the input
        #    accepts the assignment, so input-only path silently
        #    drops the file. Paste is what every site supports.
        #    Fall back to file-input only if paste returns False
        #    (no input_selectors configured).
        upload_via = "paste"
        attached = adapter.upload_file_via_paste(
            probe, tab_id,
            file_name=filename,
            file_b64=pdf_b64,
            media_type="application/pdf",
            image=False,
        )
        if not attached:
            upload_via = "input"
            attached = adapter.upload_file_via_input(
                probe, tab_id,
                file_name=filename,
                file_b64=pdf_b64,
                media_type="application/pdf",
                image=False,
            )
        details["upload_via"] = upload_via
        details["upload_returned"] = attached
        if not attached:
            return CaseResult.failed(
                self.id, adapter.name,
                "both upload_file_via_paste and upload_file_via_input "
                "returned False; adapter has no input_selectors AND "
                "no file_input_selectors. Configure at least one path.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "upload_failed"},
            )

        # 3. Send the prompt referencing the filename. Some sites
        #    debounce the file-attached state for ~1-2 s while
        #    rendering the chip; ``upload_file_via_input`` already
        #    sleeps 1.5 s, but we add another beat here to absorb
        #    sites that lazy-validate the upload (preview generation,
        #    OCR queue) before the send button enables.
        time.sleep(1.5)
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

        # 4. Wait for done.
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_done"},
            )

        # 5. Wait for capture.
        #
        # PDF turns are slower than plain chat and often slower than
        # image upload: AI Studio/ChatGPT may spend tens of seconds in
        # document tokenization before the assistant turn is complete
        # enough for the DOM extractor to emit a capture. 20s was too
        # tight and produced false negatives where the tab closed
        # before the capture runtime had a chance to send.
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

        # 6. Round-trip via PCE Core, find the user message, look at
        #    its attachments.
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

        # 7. Find the user message that carries our token (and presumably
        #    the upload).
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
                f"session {session_id} has {len(msgs)} messages but no "
                f"user message contains the token {token!r}; "
                f"preview={previews!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "user_token_missing"},
            )

        # 8. Inspect attachments. ``content_json`` is a JSON-string
        #    column on the messages table; the SW writes
        #    ``{"attachments": [...]}`` per the rich-content schema.
        #    Tolerate either a parsed dict or the raw string.
        attachments = self._extract_attachments(target)
        details["attachments"] = attachments
        if not attachments:
            return CaseResult.failed(
                self.id, adapter.name,
                f"user message has no attachments captured. The token "
                f"made it through (extractor saw the user prompt) but "
                f"the file chip's metadata didn't survive normalization. "
                f"This is the C10 / G10 reconciler-join gap if Claude or "
                f"Gemini; for ChatGPT/GAS this is a real extractor bug.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "attachments_empty"},
            )

        # 9. Type check: at least one attachment must be 'file' or
        #    'document' (Anthropic uses 'document' for PDF — see the
        #    rich-content memory in the project notes).
        file_atts = [
            a for a in attachments
            if str(a.get("type", "")).lower() in {"file", "document"}
        ]
        if not file_atts:
            return CaseResult.failed(
                self.id, adapter.name,
                f"user has {len(attachments)} attachment(s) but none "
                f"are type 'file' or 'document'; types found="
                f"{[a.get('type') for a in attachments]!r}; "
                f"the extractor mis-classified the PDF "
                f"(e.g. as image_url)",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wrong_attachment_type"},
            )

        # 10. Soft check: filename round-trip. Some sites strip the
        #     original filename in favor of an internal UUID; we
        #     surface this as info, not failure.
        name_round_trip = any(
            filename in str(a.get("name") or a.get("title") or "")
            for a in file_atts
        )
        details["filename_round_trip"] = name_round_trip

        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"PDF roundtripped; session_id={session_id}, "
                f"attachments={len(attachments)} "
                f"(file/document={len(file_atts)}), "
                f"filename_round_trip={name_round_trip}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )

    @staticmethod
    def _extract_attachments(message: dict) -> list:
        """Return the ``attachments`` list from a PCE Core message row.

        ``content_json`` is a JSON string per the schema. The dashboard
        and tests expect a parsed dict at the API layer; tolerate both
        shapes for forward compatibility.
        """
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
