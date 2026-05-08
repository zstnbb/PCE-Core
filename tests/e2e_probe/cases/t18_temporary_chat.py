# SPDX-License-Identifier: Apache-2.0
"""T18 \u2014 captures still flow through on a temporary / privacy-mode chat.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 17 / T18. ChatGPT's
"Temporary chat" mode (``?temporary-chat=true``) is the only stable
example today \u2014 Gemini's G24 is deferred to v1.1, Claude / GAS /
Grok have no equivalent surface. The behavioral contract is:

  - The page renders a near-identical chat UI but does NOT save the
    conversation to history.
  - PCE_CAPTURE must STILL fire \u2014 the user's local memory layer
    captures regardless of the upstream save state. The path
    whitelist must NOT block the temporary URL pattern.

A T18 PASS means the path whitelist + ``requireSessionHint`` defense-
in-depth combo (closed for B1 in the Claude doc) does not over-block.
A T18 SKIP means the site has no temporary surface configured (most
non-ChatGPT sites today).

Failure modes T18 catches:
  - Path-whitelist regression: a future tightening of the URL filter
    accidentally rejects ``?temporary-chat=true`` because the path
    component matches ``/`` and the query string is filtered out
    before regex testing.
  - Modal-dismissal flake: ChatGPT shows a "Welcome to Temporary
    Chat" dialog on first use; this case dismisses it before sending
    so the prompt actually goes through.
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
    predicate_token_in_any_role,
    wait_for_pce_message,
)
from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the temporary "
    "token {token}. Reply in one short line, no formatting, no quotes."
)

# JS dispatched to dismiss the welcome modal. Looks for ANY visible
# ``[role=dialog]`` with a button whose text contains a known confirm
# label, and clicks it. Multiple labels covered for locale variants.
_DISMISS_MODAL_JS = r"""
(function () {
  var labels = ["continue", "got it", "start", "begin", "\u7ee7\u7eed", "\u5f00\u59cb"];
  var dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
  for (var d = 0; d < dialogs.length; d++) {
    var dialog = dialogs[d];
    var style = window.getComputedStyle(dialog);
    if (style.display === "none" || style.visibility === "hidden") continue;
    var buttons = Array.from(dialog.querySelectorAll('button'));
    for (var b = 0; b < buttons.length; b++) {
      var btn = buttons[b];
      var text = (btn.innerText || btn.textContent || btn.getAttribute('aria-label') || "")
        .trim().toLowerCase();
      for (var l = 0; l < labels.length; l++) {
        if (text.indexOf(labels[l]) !== -1) {
          try { btn.click(); } catch (e) {}
          return;
        }
      }
    }
  }
})();
"""


class T18TemporaryChatCase(BaseCase):
    id = "T18"
    name = "temporary_chat_capture_still_fires"
    description = (
        "Navigate to the site's temporary-chat URL; dismiss any welcome "
        "modal; send a prompt; verify a normal capture flows through "
        "to PCE Core (path whitelist must not over-block)."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        if not adapter.temporary_chat_url:
            return CaseResult.skipped(
                self.id, adapter.name,
                "adapter has no temporary_chat_url; site has no "
                "stable temporary-chat surface (Gemini G24 is deferred "
                "to v1.1; Claude / GAS / Grok have no equivalent)",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T18-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details: dict = {
            "token": token,
            "temporary_chat_url": adapter.temporary_chat_url,
        }

        # 1. Open a fresh tab DIRECTLY at the temporary URL. We don't
        #    use ``adapter.open`` here because that goes to the entry
        #    URL; we want to enter via the temporary route so the path
        #    whitelist is exercised on the right path from frame load.
        try:
            res = probe.tab.open(adapter.temporary_chat_url, active=True)
            tab_id = int(res["tab_id"])
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"tab.open temporary URL failed: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "open_temporary"},
            )
        try:
            probe.tab.wait_for_load(
                tab_id, timeout_ms=adapter.page_load_timeout_ms,
            )
        except ProbeError:
            # SPA never fires complete; proceed and let
            # check_logged_in arbitrate.
            pass

        # 2. Dismiss the welcome modal if any (safe no-op if absent).
        try:
            probe.dom.execute_js(tab_id, _DISMISS_MODAL_JS)
        except ProbeError:
            pass
        time.sleep(1.0)
        # Sometimes the modal is two-step (intro + privacy). Run again.
        try:
            probe.dom.execute_js(tab_id, _DISMISS_MODAL_JS)
        except ProbeError:
            pass
        time.sleep(0.5)

        # 3. Login check on the temporary tab itself.
        login = adapter.check_logged_in(probe, tab_id)
        details["login_detail"] = login.detail
        if not login.logged_in:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"not logged in on temporary tab: {login.detail}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "login"},
            )

        # 4. Confirm the URL still has the temporary marker. ChatGPT
        #    redirects to the entry URL after the modal closes for
        #    some accounts; if so the test is no longer exercising
        #    the temporary surface and we SKIP rather than spuriously
        #    PASS on a normal-mode capture.
        cur_url = adapter.current_url(probe, tab_id) or ""
        details["current_url_after_modal"] = cur_url
        if "temporary" not in cur_url.lower() and "?temporary-chat=true" not in cur_url.lower():
            return CaseResult.skipped(
                self.id, adapter.name,
                f"temporary URL marker lost after modal dismiss "
                f"(current_url={cur_url!r}); the site moved us off "
                f"the temporary surface so the test is no longer "
                f"exercising what it was meant to",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "temporary_marker_lost"},
            )

        # 5. Send.
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

        # 6. Wait for done.
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_done"},
            )

        # 7. Wait for the capture. This is the BUSINESS assertion of
        #    T18: the temporary-chat surface must NOT be filtered out
        #    by the path whitelist. If wait_for_token times out we
        #    have a regression.
        try:
            captured = probe.capture.wait_for_token(
                token,
                timeout_ms=20_000,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
        except CaptureNotSeenError as exc:
            ctx = exc.context or {}
            recent = ctx.get("last_capture_events") or []
            return CaseResult.failed(
                self.id, adapter.name,
                f"NO PCE_CAPTURE for the token on the temporary-chat "
                f"surface within 20s. The capture pipeline is being "
                f"blocked by something in the temporary route's URL "
                f"or shell. Likely culprits: path whitelist over-blocks "
                f"``?temporary-chat=true`` query, or content_script "
                f"matches exclude this URL pattern. "
                f"recent({len(recent)})",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details, "phase": "no_capture_temporary",
                    "agent_hint": exc.agent_hint,
                    "recent_events": recent[-10:],
                },
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"capture.wait_for_token: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "wait_for_token_error"},
            )

        # 8. Round-trip via PCE Core. Even temporary-chat captures
        #    persist to the user's local memory (that's the whole
        #    point of PCE \u2014 capture even when the upstream
        #    didn't).
        #
        # IMPORTANT: temporary-chat surfaces have NO ``/c/<uuid>`` URL
        # by design (the upstream never assigns a conversation id).
        # Therefore ``session_hint`` is legitimately empty for T18
        # captures \u2014 just like T03 stop-mid-stream. Earlier the
        # case treated empty session_hint as a hard FAIL; that's a
        # case-design bug, not a path-whitelist regression. Use
        # ``wait_for_pce_message`` to find the message via PCE Core
        # ground truth regardless of session_hint state. The ACTUAL
        # T18 invariant we care about is "PCE_CAPTURE fired and the
        # token reached PCE Core" \u2014 which both
        # ``capture.wait_for_token`` (above) and the PCE Core poll
        # below verify.
        session_hint = captured.get("session_hint") or ""
        details["session_hint"] = session_hint
        sess, target = wait_for_pce_message(
            pce_core,
            provider=adapter.provider,
            predicate=predicate_token_in_any_role(token),
            timeout_s=20.0,
            session_hint=session_hint or None,
        )
        if sess is None or target is None:
            return CaseResult.failed(
                self.id, adapter.name,
                f"no PCE Core message contained token {token!r} within "
                f"20s; the path whitelist may be over-blocking the "
                f"temporary-chat URL after all (the SW ring saw the "
                f"capture but it didn't reach PCE Core ingest)",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_pce_message_with_token"},
            )

        session_id = sess.get("id")
        msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
        msgs = msg_resp.json() if msg_resp.status_code == 200 else []
        details["n_messages"] = len(msgs)

        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"temporary-chat capture round-tripped; "
                f"session_id={session_id}, n_messages={len(msgs)}, "
                f"current_url={cur_url}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )
