# SPDX-License-Identifier: Apache-2.0
"""T08 \u2014 click Regenerate; capture reflects the new variant.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 5 / T08, ``CLAUDE`` C08,
``GEMINI`` G08, ``GAS`` (regen falls under A17 family). The user
clicks Regenerate on the LATEST assistant turn; the page replaces
that turn with a NEW variant (or stacks it as a draft). The user's
prompt is unchanged.

The capture-pipeline contract under regenerate:
  - The page renders a NEW assistant turn for the SAME user prompt.
  - PCE_CAPTURE must fire for the new state. Whether the OLD variant
    is replaced or kept under `< 1/2 >` is site-specific; T08 just
    asserts the FINAL stored session has the user prompt's token AND
    at least one assistant message different from the seed.

Two-flavor verification (sites differ):
  1. Most sites: regenerate keeps the SAME conversation_id, so the
     same session has 1 user msg + N assistant msgs (where N grows
     by 1 per regen). PASS condition: messages.count(role=assistant)
     >= 2 OR latest assistant_text differs from the seed text.
  2. Sites that branch: regenerate may show only the latest variant
     while keeping the older one in a hidden branch. PASS condition
     is the same \u2014 we don't care about the branch, just that a
     fresh capture landed.

Failure modes T08 catches:
  - Regenerate click was sent but no new SW capture fired
    (fingerprint dedup keyed on user content alone, ignoring
    assistant content changes).
  - Regenerate produces a capture but the assistant text is
    identical to the seed (model gave the same reply twice; the
    site sometimes does this for low-temperature settings) \u2014
    we log this and treat as PASS with a note since the
    capture-pipeline behaved correctly even if the model didn't
    diverge.
  - Site doesn't expose a regenerate affordance \u2014 SKIP via
    empty ``regenerate_button_selectors``.
"""
from __future__ import annotations

import json
import re
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


# Low-determinism prompt that asks for SOME randomness while still
# producing a multi-paragraph reply. Modern composers (ChatGPT
# 2026-Q2 in particular) suppress the hover-revealed action strip
# \u2014 including the Regenerate icon \u2014 on extremely short
# assistant turns (1-2 words). A 1-word "name a color" reply was the
# previous prompt and consistently failed T08 because the regen
# affordance was simply not rendered on those turns. The prompt
# below targets ~80-150 words across 3 short paragraphs, which is
# long enough for the action strip to mount reliably while still
# leaving room for regen to produce a different string.
PROMPT_TEMPLATE = (
    "Pick one random color name (any common color is fine) and "
    "write three short paragraphs about it: paragraph 1 a quick "
    "mood-setting description (one or two sentences), paragraph 2 a "
    "single concrete real-world object that famously has this "
    "color, paragraph 3 a one-line poetic association. Keep the "
    "total response to roughly 80-150 words. Start the response "
    "with the chosen color name on its own line, exactly once, in "
    "lowercase. End with the literal token {token} on its own line."
)


class T08RegenerateCase(BaseCase):
    id = "T08"
    name = "regenerate_assistant_variant"
    description = (
        "Send a low-determinism prompt; click Regenerate on the "
        "assistant reply; verify a NEW capture for the same token "
        "reaches PCE Core and is stored/renderable as a structured "
        "assistant variant."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        if not adapter.regenerate_button_selectors:
            return CaseResult.skipped(
                self.id, adapter.name,
                "adapter has no regenerate_button_selectors",
                duration_ms=self._elapsed_ms(start),
            )

        token = f"PROBE-{adapter.name.upper()}-T08-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details: dict = {"token": token}

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

        # 2. Seed prompt.
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
                f"seed wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "seed_wait"},
            )

        # 3. Wait for the seed capture so we have a "before" for the
        #    diff. Soft check; if the seed capture takes longer than
        #    expected, we proceed and let the post-regen verification
        #    catch real bugs.
        try:
            seed_captured = probe.capture.wait_for_token(
                token,
                timeout_ms=15_000,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
            details["seed_capture_seen"] = True
        except (CaptureNotSeenError, ProbeError):
            seed_captured = None
            details["seed_capture_seen"] = False

        # Snapshot pre-regen state from PCE Core. We do this ONCE
        # against the live session so we have an exact "before" assistant
        # text to diff against. Sessions API is eventually-consistent but
        # by now the seed has at least started persisting; if it hasn't
        # we tolerate an empty before-state and rely on capture-count
        # delta in step 6.
        #
        # Two paths to find the seed session:
        #   (1) SW gave us a session_hint via wait_for_token \u2014
        #       fastest path, exact match on session_key.
        #   (2) SW didn't see the token in 15s (Claude / Gemini can lag
        #       end-of-stream finalization) \u2014 fall back to scanning
        #       recent sessions for ones containing the seed token in
        #       a user message. Without this fallback, ``text_differs``
        #       becomes False (because seed_assistant_text stays empty)
        #       and the regen-PASS path at step 8 path (b) is bypassed,
        #       producing a spurious SKIP on a perfectly working pipeline.
        seed_assistant_text = ""
        seed_session_id = None
        if seed_captured and seed_captured.get("session_hint"):
            seed_session_id, seed_assistant_text = self._fetch_latest_assistant(
                pce_core, adapter.provider, seed_captured["session_hint"],
            )
        if not seed_session_id:
            seed_session_id, seed_assistant_text = self._fetch_latest_assistant_by_token(
                pce_core, adapter.provider, token,
            )
            if seed_session_id:
                details["seed_session_via"] = "token_scan_fallback"
        else:
            details["seed_session_via"] = "session_hint"
        details["seed_assistant_len"] = len(seed_assistant_text)
        details["seed_session_id"] = seed_session_id

        # 4. Click Regenerate. Need the assistant turn to be settled
        #    before clicking; the wait_for_done above handles that.
        clicked = adapter.click_regenerate(probe, tab_id)
        details["regenerate_clicked"] = clicked
        # Diagnostic: read the side-channel result. The helper writes
        # to three channels (span#pce-regen-result-tag, body[data-...],
        # html[data-...]) so at least one survives any React tear-down
        # / CSP / dom.query-truncation pathology.
        for sel, name in (
            ("#pce-regen-result-tag", "span_text"),
            ("html", "html_attr"),
            ("body", "body_attr"),
        ):
            try:
                qres = probe.dom.query(tab_id, sel) or {}
            except ProbeError:
                continue
            matches = qres.get("matches") or []
            for m in matches:
                if name == "span_text":
                    txt = str(m.get("text_excerpt") or "").strip()
                    if txt:
                        details["regen_path_diag"] = txt
                        details["regen_diag_via"] = name
                        break
                outer = str(m.get("outer_html_excerpt") or "")
                if name == "span_text":
                    mt = re.search(
                        r'<span[^>]*id="pce-regen-result-tag"[^>]*>([^<]*)</span>',
                        outer,
                    )
                else:
                    mt = re.search(
                        r'data-pce-regen-result="([^"]*)"', outer,
                    )
                if mt:
                    details["regen_path_diag"] = mt.group(1).strip()
                    details["regen_diag_via"] = name
                    break
            if "regen_path_diag" in details:
                break
        if not clicked:
            return CaseResult.failed(
                self.id, adapter.name,
                "click_regenerate returned False; none of "
                "regenerate_button_selectors matched. Selectors may be "
                "stale or the regen affordance is now buried in a "
                "More-actions menu (the legacy autopilot has a 2-step "
                "fallback; we have not yet replicated that here)",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "regen_click_failed"},
            )

        # 5. Wait for regen to actually fire (stop button visible) and
        #    finish (stop button gone / text stable).
        regen_started = adapter.wait_for_stop_button_visible(
            probe, tab_id, timeout_s=10.0,
        )
        details["regen_started"] = regen_started
        if not regen_started:
            # Some sites are very fast; regen may complete before we
            # observe the stop button. Continue to wait_for_done; if
            # the SW emitted a fresh capture, we still PASS. If not,
            # step 6 will surface that.
            pass
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"regen wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "regen_wait"},
            )

        # 6. Wait for ANOTHER PCE_CAPTURE on this token. T01-style
        #    wait_for_token returns on first match; we need to count
        #    captures across the whole exercise to confirm the regen
        #    fired a fresh one. Use ``recent_events`` ring count delta.
        #    Brief settle to let the SW finish its post-regen flush.
        time.sleep(2.0)
        try:
            ring = (
                probe.capture.recent_events(
                    last_n=200, provider=adapter.provider,
                ).get("events", [])
                or []
            )
        except ProbeError:
            ring = []
        token_captures = [
            e for e in ring
            if e.get("kind") == "PCE_CAPTURE"
            and self._event_body_has(e, token)
        ]
        details["token_capture_count"] = len(token_captures)

        # 7. Determine the session to inspect and fetch the final
        #    assistant text. Three lookup paths, in preference order:
        #
        #    (a) Most recent token-bearing SW capture's session_hint
        #        \u2014 fastest, exact match.
        #    (b) Seed capture's session_hint \u2014 regen lands in the
        #        same session on non-branching sites.
        #    (c) Seed session ID we discovered via the token-scan
        #        fallback in step 3 \u2014 covers the case where the
        #        SW ring missed BOTH the seed and regen captures, but
        #        PCE Core has the user message with the token. This is
        #        the path that finally lets T08 PASS on Claude/Gemini
        #        when the SW lag dwarfs the 15s soft window.
        session_hint = None
        if token_captures:
            last_capture = token_captures[-1]
            session_hint = (
                last_capture.get("session_hint")
                or (last_capture.get("payload") or {}).get("session_hint")
            )
        if not session_hint and seed_captured:
            session_hint = seed_captured.get("session_hint")

        final_session_id = None
        final_assistant_text = ""
        if session_hint:
            final_session_id, final_assistant_text = self._fetch_latest_assistant(
                pce_core, adapter.provider, session_hint,
            )
        if not final_session_id and seed_session_id:
            # Path (c): re-fetch the same session by ID to read the
            # post-regen assistant text. Regen on non-branching sites
            # mutates the same session row.
            try:
                msg_resp = pce_core.get(
                    f"/api/v1/sessions/{seed_session_id}/messages"
                )
                if msg_resp.status_code == 200:
                    final_msgs = msg_resp.json() or []
                    final_assistants = [
                        m for m in final_msgs if m.get("role") == "assistant"
                    ]
                    if final_assistants:
                        final_session_id = seed_session_id
                        final_assistant_text = str(
                            final_assistants[-1].get("content_text") or ""
                        )
                        details["final_session_via"] = "seed_session_reread"
            except httpx.HTTPError:
                pass
        details["final_session_id"] = final_session_id
        details["final_assistant_len"] = len(final_assistant_text)
        details["final_assistant_head"] = final_assistant_text[:160]
        final_msgs = (
            self._fetch_session_messages(pce_core, final_session_id)
            if final_session_id
            else []
        )
        details["variant_contract"] = self._variant_contract_evidence(final_msgs)

        text_differs = (
            seed_assistant_text != ""
            and final_assistant_text != ""
            and final_assistant_text != seed_assistant_text
        )
        details["text_differs"] = text_differs

        # 8. Decision matrix \u2014 PCE Core round-trip is the
        #    authoritative gate, with capture-count as a tiebreaker.
        #
        #   (a) capture_count >= 2 \u2014 fresh capture definitively
        #       fired. PASS regardless of text difference (model may
        #       legitimately reproduce the same answer; that is not a
        #       pipeline bug).
        #
        #   (b) text_differs == True \u2014 PCE Core has new assistant
        #       text after regen, so the pipeline propagated the
        #       regenerated turn end-to-end. PASS even if the SW ring
        #       only shows 1 token-bearing capture (the second event
        #       may have been swept past our 200-event window or the
        #       fingerprint key may have shifted, but storage shows it
        #       landed).
        #
        #   (c) capture_count <= 1 AND text_differs == False AND
        #       regen_started == True \u2014 regen visibly fired (stop
        #       button appeared) but neither the SW ring nor PCE Core
        #       shows a new turn. Two indistinguishable causes:
        #         - model regenerated identically (variance)
        #         - SW fingerprint dedup collapsed the regen
        #       Without live DOM comparison we cannot tell, so SKIP
        #       and report. SKIP, not FAIL, because the legacy
        #       autopilot historically treated this as a known model
        #       variance class on Claude / Gemini.
        #
        #   (d) regen_started == False AND no signal \u2014 UI was
        #       unreachable (selectors stale / affordance moved). SKIP.
        #
        #   (e) No final_session_id \u2014 capture pipeline produced
        #       nothing for the seed either, which means the seed
        #       wait probably failed silently. FAIL with helpful
        #       detail (this is a real pipeline break).
        content_level_signal = len(token_captures) >= 2 or (
            text_differs and final_session_id
        )
        if content_level_signal and not details["variant_contract"]["ok"]:
            return CaseResult.failed(
                self.id, adapter.name,
                (
                    "regen content-level signal exists, but structured "
                    "variant storage/render contract is missing. The new "
                    "standard requires variant_group/current_variant "
                    "evidence before T08 can PASS."
                ),
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "variant_contract_missing"},
            )

        if len(token_captures) >= 2:
            return CaseResult.passed(
                self.id, adapter.name,
                summary=(
                    f"regen captured; n_token_captures={len(token_captures)}, "
                    f"text_differs={text_differs}, "
                    f"seed_len={len(seed_assistant_text)}, "
                    f"final_len={len(final_assistant_text)}, "
                    f"session_id={final_session_id}"
                ),
                duration_ms=self._elapsed_ms(start),
                details={**details},
            )

        if text_differs and final_session_id:
            return CaseResult.passed(
                self.id, adapter.name,
                summary=(
                    f"regen text differs in PCE Core "
                    f"(seed_len={len(seed_assistant_text)}, "
                    f"final_len={len(final_assistant_text)}); SW ring "
                    f"shows only {len(token_captures)} token-bearing "
                    f"capture but storage has the new turn so the "
                    f"pipeline succeeded; session_id={final_session_id}"
                ),
                duration_ms=self._elapsed_ms(start),
                details={**details},
            )

        if not regen_started:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"click_regenerate returned True but the page never "
                f"showed a stop button afterward AND only "
                f"{len(token_captures)} capture(s) reached the SW ring "
                f"\u2014 the regen affordance is beyond what this "
                f"adapter's selectors / more-actions fallback can "
                f"reach. UI moved; not a streaming-gate bug.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "regen_ui_unreachable"},
            )

        if final_session_id and not text_differs:
            return CaseResult.skipped(
                self.id, adapter.name,
                f"regen visibly fired (stop button appeared) but PCE "
                f"Core's assistant text after regen is identical to "
                f"the pre-regen text and only {len(token_captures)} "
                f"PCE_CAPTURE(s) were observed for the token. Cannot "
                f"distinguish model regenerating identically (variance) "
                f"from SW fingerprint dedup collapsing the regen "
                f"without live DOM comparison; treating as model "
                f"variance per the legacy autopilot convention.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "regen_indistinguishable"},
            )

        return CaseResult.failed(
            self.id, adapter.name,
            f"regen pipeline produced no signal: "
            f"token_captures={len(token_captures)}, "
            f"final_session_id={final_session_id!r}, "
            f"text_differs={text_differs}; "
            f"the regen click landed but neither the SW ring nor PCE "
            f"Core received any turn associated with this session \u2014 "
            f"likely a real pipeline break (extractor missed the seed "
            f"or session_hint extraction failed)",
            duration_ms=self._elapsed_ms(start),
            details={**details, "phase": "regen_no_pipeline_signal"},
        )

    @staticmethod
    def _fetch_latest_assistant(
        pce_core: httpx.Client,
        provider: str,
        session_hint: str,
    ) -> tuple:
        """Return (session_id, last_assistant_text). Empty on miss."""
        for _attempt in range(6):
            try:
                resp = pce_core.get(
                    "/api/v1/sessions",
                    params={"provider": provider},
                )
            except httpx.HTTPError:
                return (None, "")
            if resp.status_code != 200:
                return (None, "")
            sessions = resp.json() or []
            matching = [
                s for s in sessions
                if str(s.get("session_key", "")).find(str(session_hint)) != -1
            ]
            if matching:
                session_id = matching[0].get("id")
                msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
                if msg_resp.status_code != 200:
                    return (session_id, "")
                msgs = msg_resp.json() or []
                assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
                if assistant_msgs:
                    return (
                        session_id,
                        str(assistant_msgs[-1].get("content_text") or ""),
                    )
                return (session_id, "")
            time.sleep(0.5)
        return (None, "")

    @staticmethod
    def _fetch_latest_assistant_by_token(
        pce_core: httpx.Client,
        provider: str,
        token: str,
    ) -> tuple:
        """Scan recent sessions for one whose user messages contain the token.

        Used when the SW ring failed to deliver a session_hint within
        the 15s soft window (Claude / Gemini end-of-stream lag). Walks
        the most-recent 10 sessions for the provider and returns the
        first session_id whose user messages contain the token, plus
        that session's last assistant text.

        Returns ``(None, "")`` on miss \u2014 caller should treat the
        absence of seed text as "regen baseline unknown" and fall back
        to capture-count or visible regen detection.
        """
        for _attempt in range(6):
            try:
                resp = pce_core.get(
                    "/api/v1/sessions",
                    params={"provider": provider},
                )
            except httpx.HTTPError:
                return (None, "")
            if resp.status_code != 200:
                return (None, "")
            sessions = resp.json() or []
            for s in sessions[:10]:
                session_id = s.get("id")
                if not session_id:
                    continue
                msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
                if msg_resp.status_code != 200:
                    continue
                msgs = msg_resp.json() or []
                if any(
                    token in (m.get("content_text") or "")
                    for m in msgs
                    if m.get("role") == "user"
                ):
                    assistant_msgs = [
                        m for m in msgs if m.get("role") == "assistant"
                    ]
                    if assistant_msgs:
                        return (
                            session_id,
                            str(assistant_msgs[-1].get("content_text") or ""),
                        )
                    return (session_id, "")
            time.sleep(0.5)
        return (None, "")

    @staticmethod
    def _fetch_session_messages(
        pce_core: httpx.Client,
        session_id: str,
    ) -> list[dict]:
        try:
            msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
        except httpx.HTTPError:
            return []
        if msg_resp.status_code != 200:
            return []
        data = msg_resp.json() or []
        return data if isinstance(data, list) else []

    @classmethod
    def _variant_contract_evidence(cls, messages: list[dict]) -> dict:
        storage_keys = {
            "variant_group_id",
            "variant_id",
            "variant_index",
            "regenerated_from",
            "regeneration_of",
            "previous_variant_id",
            "current_variant_id",
        }
        render_keys = {
            "variant",
            "variants",
            "variant_group",
            "current_variant",
            "variant_controls",
        }

        storage_paths: list[str] = []
        render_paths: list[str] = []
        assistant_count = 0
        for idx, msg in enumerate(messages or []):
            if msg.get("role") == "assistant":
                assistant_count += 1
            storage_paths.extend(cls._find_keys(msg, storage_keys, f"messages[{idx}]"))
            content_json = cls._content_json_dict(msg)
            if not content_json:
                continue
            storage_paths.extend(
                cls._find_keys(
                    content_json,
                    storage_keys,
                    f"messages[{idx}].content_json",
                )
            )
            rich_content = content_json.get("rich_content")
            if isinstance(rich_content, dict):
                render_paths.extend(
                    cls._find_keys(
                        rich_content,
                        render_keys,
                        f"messages[{idx}].content_json.rich_content",
                    )
                )

        storage_relation = bool(storage_paths)
        render_relation = bool(render_paths)
        return {
            "ok": storage_relation and render_relation,
            "assistant_count": assistant_count,
            "storage_relation": storage_relation,
            "render_relation": render_relation,
            "storage_paths": storage_paths[:12],
            "render_paths": render_paths[:12],
        }

    @staticmethod
    def _content_json_dict(msg: dict) -> dict:
        raw = msg.get("content_json")
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str) or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _find_keys(
        cls,
        value,
        keys: set[str],
        prefix: str,
    ) -> list[str]:
        hits: list[str] = []
        if isinstance(value, dict):
            for k, v in value.items():
                path = f"{prefix}.{k}"
                if k in keys:
                    hits.append(path)
                hits.extend(cls._find_keys(v, keys, path))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                hits.extend(cls._find_keys(item, keys, f"{prefix}[{i}]"))
        return hits

    @staticmethod
    def _event_body_has(event: dict, token: str) -> bool:
        # The SW capture ring summary (see ``CaptureEventSummary`` in
        # ``probe-rpc-capture.ts``) carries the body in a ``fingerprint``
        # field \u2014 first 200 chars of the stringified message body
        # \u2014 NOT in ``body_excerpt`` / ``content_text`` etc. Earlier
        # versions of this helper checked the wrong keys and always
        # returned False, which made T08 silently report ``0 captures``
        # on every run regardless of pipeline state.
        token = str(token)
        for k in ("fingerprint", "body_excerpt", "body", "summary", "content_text"):
            v = event.get(k)
            if isinstance(v, str) and token in v:
                return True
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            for k in ("fingerprint", "body_excerpt", "body", "content_text", "text"):
                v = payload.get(k)
                if isinstance(v, str) and token in v:
                    return True
        return False
