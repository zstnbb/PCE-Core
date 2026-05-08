# SPDX-License-Identifier: Apache-2.0
"""T05 \u2014 fenced code-block extraction.

T01 verifies prompt -> capture -> ingest works for plain text. T05
verifies the same pipeline preserves a fenced code block intact: the
prompt asks for a Python hello-world, the assistant replies with a
``\u0060\u0060\u0060python\\nprint(...)\\n\u0060\u0060\u0060`` fence, and PCE Core's
ingested message must contain the fence verbatim.

What T05 catches that T01 doesn't:

  - **Triple-backtick mangling** \u2014 some content scripts'
    ``normalizeText`` strips backticks; PCE Core stores ``python\\n
    print(...)\\n`` with NO fence. Code-block round-trip is broken.
  - **Language tag dropped** \u2014 the assistant emitted ``python``
    after the opening fence; capture stored only ``\u0060\u0060\u0060\\n
    print(...)\\n\u0060\u0060\u0060``. PCE Core's downstream attachment
    extractor can't classify it.
  - **Inline-code mangling** \u2014 single-backtick spans inside the
    body get stripped or doubled.

T05 is intentionally **content-text-only**: we don't poke
``message.attachments[].type == "code_block"`` because attachment
classification varies per provider/site; the canonical signal is the
fenced block surviving through ``content_text`` verbatim. A site that
classifies the block as a ``code_block`` attachment is fine; a site
that leaves it inline is fine too. What T05 forbids is mangling.

Idempotent. Inherits T01's send_prompt + wait_for_token + PCE Core
verification machinery; the only deltas are the prompt and the
content-text post-condition.
"""
from __future__ import annotations

import time
import uuid

import httpx

from pce_probe import (
    CaptureNotSeenError,
    ProbeClient,
    ProbeError,
)

from .._pce_helpers import (
    extract_attachments,
    has_attachment_of_type,
    predicate_token_in_assistant,
    wait_for_pce_message,
)
from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


PROMPT_TEMPLATE = (
    "Write a Python hello world program. Reply with ONLY a fenced "
    "code block tagged 'python' that contains exactly one line: "
    "print('hello {token} world'). After the closing fence, write "
    "nothing. Do not add commentary."
)


class T05CodeBlockCase(BaseCase):
    id = "T05"
    name = "code_block_roundtrip"
    description = (
        "Prompt for a fenced Python code block; verify the captured "
        "assistant content_text retains the triple-backtick fence with "
        "the 'python' language tag and the print() body containing "
        "the test token."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        token = f"PROBE-{adapter.name.upper()}-T05-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details: dict = {"token": token}

        # 1. Open + login.
        try:
            tab_id = adapter.open(probe)
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"adapter.open failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "open"},
            )
        login = adapter.check_logged_in(probe, tab_id)
        details["login_detail"] = login.detail
        if not login.logged_in:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                f"not logged in: {login.detail}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "login"},
            )

        # 2. Send.
        try:
            adapter.send_prompt(probe, tab_id, prompt)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"send_prompt: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "send_prompt",
                    "send_prompt_context": exc.context or {},
                },
            )

        # 3. Wait for capture (the canonical post-stream PCE_CAPTURE).
        #    We use the full ``response_timeout_ms`` because some
        #    sites take longer to render code fences (extra DOM
        #    construction for syntax highlighting on Gemini /
        #    AI Studio).
        try:
            captured = probe.capture.wait_for_token(
                token,
                timeout_ms=adapter.response_timeout_ms,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
        except CaptureNotSeenError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"capture.wait_for_token: {exc.message}; "
                f"hint={exc.agent_hint!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_token"},
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"capture.wait_for_token: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_token_error"},
            )

        # 4. Locate the assistant turn via PCE Core (ground truth).
        #
        # Per the redesign analysis (Pathology 4, Principle E):
        # PCE Core stores rich content in ``content_json.attachments[]``
        # alongside ``content_text``. Earlier the case scanned only the
        # markdown text for ``\u0060\u0060\u0060python`` fences \u2014
        # but PCE Core's normalizer + the ChatGPT extractor both
        # legitimately migrate fenced code into a ``code_block``
        # attachment, leaving the text without the fence wrapper.
        # Restricting T05 to text-only created false negatives.
        session_hint = captured.get("session_hint") or ""
        details["session_hint"] = session_hint
        sess, target = wait_for_pce_message(
            pce_core,
            provider=adapter.provider,
            predicate=predicate_token_in_assistant(token),
            timeout_s=20.0,
            session_hint=session_hint or None,
        )
        if sess is None or target is None:
            return CaseResult.failed(
                self.id, adapter.name,
                f"no PCE Core assistant message contained token {token!r} "
                f"within 20 s; session_hint={session_hint!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_pce_message_with_token"},
            )

        session_id = sess.get("id")
        text = str(target.get("content_text") or "")
        attachments = extract_attachments(target)
        attachment_types = [str(a.get("type") or "").lower() for a in attachments]
        details["assistant_text_len"] = len(text)
        details["assistant_text_head"] = text[:300]
        details["attachment_types"] = attachment_types

        # 5. The PASS shape is: code-block survival, located in EITHER
        #    text or attachments. We accept three storage layouts:
        #
        #      A) Markdown text contains a fence + print(token)
        #         (typical legacy + Claude/Gemini extractor output)
        #      B) ``content_json.attachments[]`` contains a
        #         ``code_block`` block whose body has print(token)
        #         (typical ChatGPT structured-attachment output)
        #      C) Both A and B \u2014 the strongest evidence
        #
        #    What we still FAIL on: ``content_text`` lacks the body
        #    AND no code_block attachment carries it. That's
        #    real mangling of code-block round-trip.
        body_in_text = (
            ("```python" in text.lower() or "``` python" in text.lower())
            and "print(" in text
            and token in text
        )

        body_in_attachment = False
        for att in attachments:
            if str(att.get("type") or "").lower() != "code_block":
                continue
            # ``code_block`` attachments may store body under ``code``,
            # ``content``, ``text``, or nested ``data.code`` depending
            # on the upstream extractor. Try them all.
            for key in ("code", "content", "text"):
                v = att.get(key)
                if isinstance(v, str) and "print(" in v and token in v:
                    body_in_attachment = True
                    break
            if not body_in_attachment:
                data = att.get("data")
                if isinstance(data, dict):
                    for key in ("code", "content", "text"):
                        v = data.get(key)
                        if isinstance(v, str) and "print(" in v and token in v:
                            body_in_attachment = True
                            break
            if body_in_attachment:
                break

        details["body_in_text"] = body_in_text
        details["body_in_attachment"] = body_in_attachment

        if not body_in_text and not body_in_attachment:
            return CaseResult.failed(
                self.id, adapter.name,
                f"code-block round-trip mangled: neither content_text "
                f"nor any code_block attachment carries the print() "
                f"body with token. text_head={text[:200]!r}, "
                f"attachment_types={attachment_types!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "code_block_lost"},
            )

        return CaseResult.passed(
            self.id,
            adapter.name,
            summary=(
                f"code block survived; in_text={body_in_text}, "
                f"in_attachment={body_in_attachment}, "
                f"attachments={attachment_types!r}, session_id={session_id}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )
