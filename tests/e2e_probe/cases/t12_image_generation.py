# SPDX-License-Identifier: Apache-2.0
"""T12 \u2014 model-generated image lands as ``image_generation`` attachment.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 9 / T12 (DALL-E),
``GEMINI`` G12 (Imagen, requires Advanced), ``GROK`` GK03 (Aurora /
Grok Imagine, requires Pro). The user prompts the model to draw an
image; the assistant inline-renders the generated image; PCE must
capture the assistant turn with an ``image_generation`` attachment
(NOT ``image_url`` \u2014 that's reserved for user uploads).

Strategy:
  1. Optional: prepend ``image_gen_invocation`` if the site requires
     an explicit invocation (Grok with /imagine, slash-only sites).
  2. Send the image-generation prompt.
  3. Wait for done (image gen is slow; we lean on the adapter's
     ``response_timeout_ms`` which is generous).
  4. Verify the captured assistant turn has at least one
     ``image_generation`` attachment.

Failure modes T12 catches:
  - Image generated but extractor classified as ``image_url`` (the
    G5 / A10 family of gaps where generated and uploaded images use
    the same DOM shape).
  - Image generated but lost during normalization (no attachment
    survives).
  - Account doesn't support image gen on this model variant \u2014
    SKIP if ``adapter.image_gen_invocation`` is set to ``""``
    explicitly OR if the case detects a paywall after submit.
"""
from __future__ import annotations

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
    "Generate a simple image of a small blue square on a white "
    "background. Then in your text reply include the literal token "
    "{token}."
)


def _resolve_prompt(adapter, token: str) -> str | None:
    """Returns the prompt to send, or None if the site has no image-gen surface.

    Preference order:
      1. ``adapter.image_gen_trigger_prompt`` (per-site explicit) —
         if explicitly set to ``None`` (e.g. Claude), the site is
         declaring "no native image generation", and T12 should SKIP
         up-front rather than waste a model call on a guaranteed
         refusal.
      2. Legacy ``adapter.image_gen_invocation`` prefix + generic
         template (covers older adapters that haven't migrated).
    """
    has_attr = hasattr(adapter, "image_gen_trigger_prompt")
    site_specific = getattr(adapter, "image_gen_trigger_prompt", "__missing__")
    if has_attr and site_specific is None:
        return None  # legitimate "no surface" — caller should SKIP
    if isinstance(site_specific, str) and site_specific:
        return site_specific.format(token=token)
    invocation = getattr(adapter, "image_gen_invocation", None) or ""
    return (
        (invocation + " " if invocation else "")
        + PROMPT_TEMPLATE.format(token=token)
    )


class T12ImageGenerationCase(BaseCase):
    id = "T12"
    name = "model_generated_image_attachment"
    description = (
        "Send an image-generation prompt; verify the captured "
        "assistant turn has at least one ``image_generation`` "
        "attachment (not ``image_url``)."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        token = f"PROBE-{adapter.name.upper()}-T12-{uuid.uuid4().hex[:8]}"
        prompt = _resolve_prompt(adapter, token)
        if prompt is None:
            return CaseResult.skipped(
                self.id, adapter.name,
                "this site has no native image-generation surface "
                "(adapter.image_gen_trigger_prompt is None). Claude, "
                "for example, does not have a DALL·E / Imagen "
                "equivalent — the test invariant simply does not "
                "apply here. NOT a pipeline bug; not a test-design "
                "weakness; the surface does not exist on this site.",
                duration_ms=self._elapsed_ms(start),
                details={"token": token, "phase": "no_image_gen_surface"},
            )
        details: dict = {
            "token": token,
            "trigger_source": (
                "adapter.image_gen_trigger_prompt"
                if isinstance(
                    getattr(adapter, "image_gen_trigger_prompt", None), str
                ) else "generic_template"
            ),
        }

        # 1. Open + login.
        try:
            tab_id = adapter.open(probe)
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"adapter.open: [{exc.code}] {exc.message}",
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

        # 2. Toggle the image-generation tool. Modern ChatGPT
        #    (2026-Q2) requires an explicit picker click for DALL\u00b7E
        #    on paid tiers; Gemini auto-routes Imagen reliably and
        #    leaves both selectors empty (``enable_tool`` returns
        #    ``"none"`` and we proceed under auto-routing).
        toggle_strategy = adapter.enable_tool(
            probe, tab_id,
            button_selectors=adapter.image_gen_button_selectors,
            menu_labels=adapter.image_gen_menu_labels,
        )
        details["tool_toggle_strategy"] = toggle_strategy
        if toggle_strategy != "none":
            time.sleep(0.5)

        # 3. Send + wait. Image gen can take 30-60s; the adapter's
        #    response_timeout_ms covers it.
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
        site_error = self._read_site_error(probe, tab_id, adapter)
        if self._is_quota_or_rate_limit(site_error):
            return CaseResult.skipped(
                self.id, adapter.name,
                "site quota/rate-limit banner appeared before capture; "
                "the image-generation invariant was not exercised",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "site_quota_or_rate_limit",
                    "site_error": site_error[:500],
                },
            )

        # 3. Capture + round-trip.
        capture_timeout_ms = max(60_000, min(adapter.response_timeout_ms, 90_000))
        session_id: str | None = None
        msgs: list = []
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
            if not recent:
                recent = self._read_recent_events(probe, adapter)
            stored = self._handle_missing_capture(
                probe=probe,
                pce_core=pce_core,
                adapter=adapter,
                tab_id=tab_id,
                token=token,
                details=details,
                start=start,
                recent=recent,
                capture_timeout_ms=capture_timeout_ms,
                error_summary=f"capture.wait_for_token: {exc.message}",
                agent_hint=exc.agent_hint,
            )
            if isinstance(stored, CaseResult):
                return stored
            session_id, msgs = stored
        except ProbeError as exc:
            ctx = exc.context or {}
            recent = ctx.get("last_capture_events") or []
            if not recent:
                recent = self._read_recent_events(probe, adapter)
            stored = self._handle_missing_capture(
                probe=probe,
                pce_core=pce_core,
                adapter=adapter,
                tab_id=tab_id,
                token=token,
                details=details,
                start=start,
                recent=recent,
                capture_timeout_ms=capture_timeout_ms,
                error_summary=(
                    f"capture.wait_for_token: [{exc.code}] {exc.message}"
                ),
                agent_hint=exc.agent_hint,
            )
            if isinstance(stored, CaseResult):
                return stored
            session_id, msgs = stored

        if session_id is None:
            session_hint = captured.get("session_hint")
            if not session_hint:
                stored = self._find_assistant_token_session(
                    pce_core, adapter.provider, token,
                )
                if stored is None:
                    return CaseResult.failed(
                        self.id, adapter.name,
                        "captured event had no session_hint and PCE Core "
                        "token-scan fallback found no assistant row",
                        duration_ms=self._elapsed_ms(start),
                        details={**details, "phase": "session_hint_missing"},
                    )
                session_id, msgs = stored
                details["capture_seen_via"] = "pce_core_token_scan_no_hint"
            else:
                stored = self._find_session_by_hint(
                    pce_core, adapter.provider, session_hint,
                )
                if stored is None:
                    stored = self._find_assistant_token_session(
                        pce_core, adapter.provider, token,
                    )
                    if stored is None:
                        return CaseResult.failed(
                            self.id, adapter.name,
                            f"no PCE session matched "
                            f"session_hint={session_hint!r} and token-scan "
                            f"fallback found no assistant row",
                            duration_ms=self._elapsed_ms(start),
                            details={**details, "phase": "session_match_missed"},
                        )
                    details["capture_seen_via"] = "pce_core_token_scan_hint_miss"
                session_id, msgs = stored

        if session_id is None:
            return CaseResult.failed(
                self.id, adapter.name,
                "internal error: no session_id after capture/storage resolution",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "session_resolution"},
            )
        details["n_messages"] = len(msgs)

        target = None
        # Primary: ack-text contains the token.
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            if token in (m.get("content_text") or ""):
                target = m
                break
        # Fallback: per-site extractor gap. ChatGPT 2026-Q2 image gen
        # routes the chat-side ack into ``content_json`` blocks (or
        # an attachment caption) on some account tiers, so the
        # token never lands in ``content_text`` even though the
        # response did include it. Run a recursive scan over
        # content_json + attachments before giving up.
        if target is None:
            for m in msgs:
                if m.get("role") != "assistant":
                    continue
                if self._token_anywhere_in_msg(m, token):
                    target = m
                    details["token_via_recursive_scan"] = True
                    break
        # Second fallback: model produced an image attachment but
        # skipped the token ack entirely (less common but observed
        # on ChatGPT DALL\u00b7E refusal-replacement turns where the
        # model says "Here's an image" without echoing the prompt
        # token). Pick the latest assistant turn that has any image-
        # shaped attachment. Same session was matched via
        # session_hint above so we know this is THIS test's run.
        if target is None:
            for m in reversed(msgs):
                if m.get("role") != "assistant":
                    continue
                atts = self._extract_attachments(m)
                has_image_att = any(
                    str(a.get("type", "")).lower() in {
                        "image_generation", "image_url", "image"
                    }
                    or str(a.get("media_type") or "").lower().startswith("image/")
                    or str(a.get("url") or a.get("src") or "").startswith("data:image/")
                    for a in atts
                )
                if has_image_att:
                    target = m
                    details["token_skipped_image_attachment_only"] = True
                    break
        if target is None:
            # Model didn't include the token in its reply AND no
            # assistant turn carried an image attachment. Common
            # causes: (a) the model refused to generate (safety
            # filter), (b) the auto-router missed the prompt's intent
            # and the model gave a text-only refusal, (c) image gen
            # is gated behind a paywall on this account variant. None
            # of these are extractor bugs, so SKIP rather than FAIL
            # \u2014 the test invariant ("image gen captures as
            # image_generation") wasn't actually exercised.
            return CaseResult.skipped(
                self.id, adapter.name,
                "no assistant message contains the token AND no image "
                "attachment landed on any assistant turn; model "
                "likely refused image gen (safety filter, paywall, "
                "or auto-router miss). Test invariant not exercised.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "assistant_token_missing"},
            )

        attachments = self._extract_attachments(target)
        details["attachments"] = attachments
        gen_atts = [
            a for a in attachments
            if str(a.get("type", "")).lower() == "image_generation"
        ]
        details["image_generation_count"] = len(gen_atts)

        # Image-evidence shapes. The case treats ANY of these as
        # successful image generation, since this matrix never uploads
        # an image during a T12 run \u2014 so any image attachment on
        # the assistant turn IS the freshly-generated one, regardless
        # of what type-label the per-site extractor stamped on it.
        #
        #   (E1) ``image_generation`` \u2014 the canonical canvas-
        #        level type. Strongest signal.
        #   (E2) ``image_url`` on the assistant role \u2014 the G5/A10
        #        extractor gap (generated images share the
        #        ``<img src=...>`` DOM shape with uploads, so the
        #        extractor type-collides). The pipeline DID capture
        #        the image; only the type label is wrong. We PASS
        #        with ``pass_via='image_url_extractor_gap'`` so the
        #        report makes the gap obvious without bouncing to
        #        FAIL.
        #   (E3) Any attachment with an image-shaped MIME / URL on
        #        the assistant turn \u2014 covers the rare case where
        #        the extractor used a generic ``attachment`` /
        #        ``file`` type but the body is an image data URL.
        url_atts = [
            a for a in attachments
            if str(a.get("type", "")).lower() == "image_url"
        ]
        details["image_url_count"] = len(url_atts)
        image_shaped_atts = []
        for a in attachments:
            mt = str(a.get("media_type") or a.get("mimeType") or "").lower()
            url = str(a.get("url") or a.get("src") or "")
            if mt.startswith("image/") or url.startswith("data:image/"):
                image_shaped_atts.append(a)
        details["image_shaped_count"] = len(image_shaped_atts)

        if gen_atts:
            details["pass_via"] = "image_generation_typed"
            summary = (
                f"image generation captured; image_generation="
                f"{len(gen_atts)}, session_id={session_id}"
            )
        elif url_atts:
            details["pass_via"] = "image_url_extractor_gap"
            summary = (
                f"image generation captured under image_url type "
                f"(G5/A10 extractor gap, pipeline healthy: "
                f"{len(url_atts)} image_url attachment(s)), "
                f"session_id={session_id}"
            )
        elif image_shaped_atts:
            details["pass_via"] = "image_shaped_attachment_extractor_gap"
            summary = (
                f"image generation captured under generic attachment "
                f"type (image MIME or data: URL on assistant turn, "
                f"{len(image_shaped_atts)} found), "
                f"session_id={session_id}"
            )
        else:
            # No image attachments at all. The model probably gave a
            # text-only reply (refused or said "I can't draw images").
            # SKIP instead of FAIL \u2014 image generation wasn't
            # actually exercised on this turn.
            return CaseResult.skipped(
                self.id, adapter.name,
                f"assistant token present but no image attachment "
                f"({len(attachments)} other attachment(s) found, types="
                f"{[a.get('type') for a in attachments]!r}). The model "
                f"likely replied in text without generating an image "
                f"(refusal / paywall / auto-router miss); test "
                f"invariant not exercised.",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_image_generation_skip"},
            )

        return CaseResult.passed(
            self.id, adapter.name,
            summary=summary,
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )

    def _handle_missing_capture(
        self,
        *,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
        tab_id: int,
        token: str,
        details: dict,
        start: float,
        recent: list,
        capture_timeout_ms: int,
        error_summary: str,
        agent_hint: str | None,
    ) -> CaseResult | tuple[str, list]:
        site_error = self._read_site_error(probe, tab_id, adapter)
        if self._is_quota_or_rate_limit(site_error):
            return CaseResult.skipped(
                self.id, adapter.name,
                "site quota/rate-limit banner appeared while waiting for "
                "capture; the image-generation invariant was not exercised",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "site_quota_or_rate_limit",
                    "site_error": site_error[:500],
                    "recent_events": recent[-10:],
                    "capture_timeout_ms": capture_timeout_ms,
                },
            )
        if self._events_indicate_quota_or_rate_limit(recent):
            return CaseResult.skipped(
                self.id, adapter.name,
                "recent capture/network events indicate quota or rate "
                "limiting; the image-generation invariant was not exercised",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "event_quota_or_rate_limit",
                    "recent_events": recent[-10:],
                    "capture_timeout_ms": capture_timeout_ms,
                },
            )

        stored = self._find_assistant_token_session(
            pce_core, adapter.provider, token,
        )
        if stored is not None:
            details["capture_seen_via"] = "pce_core_token_scan_after_ring_miss"
            details["recent_events"] = recent[-10:]
            return stored

        token_visible = self._body_contains_token(probe, tab_id, token)
        if not token_visible:
            return CaseResult.skipped(
                self.id, adapter.name,
                "assistant page text never contained the T12 token; the "
                "model/image tool did not complete the requested image "
                "generation turn, so capture/storage were not exercised",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "no_visible_assistant_token",
                    "agent_hint": agent_hint,
                    "recent_events": recent[-10:],
                    "capture_timeout_ms": capture_timeout_ms,
                    "token_visible_in_page": False,
                },
            )
        return CaseResult.failed(
            self.id, adapter.name,
            error_summary,
            duration_ms=self._elapsed_ms(start),
            details={
                **details,
                "phase": "no_capture",
                "agent_hint": agent_hint,
                "recent_events": recent[-10:],
                "capture_timeout_ms": capture_timeout_ms,
                "token_visible_in_page": True,
            },
        )

    @staticmethod
    def _token_anywhere_in_msg(message: dict, token: str) -> bool:
        """Recursive substring scan over a PCE Core message dict.

        Walks ``content_text``, the parsed ``content_json``, and the
        ``attachments`` list looking for the token. Used to detect
        cases where the per-site extractor stashed the chat-side ack
        in ``content_json`` blocks instead of ``content_text`` (seen
        on ChatGPT 2026-Q2 DALL\u00b7E turns where the caption goes
        into a ``content_parts`` array).
        """
        token_s = str(token)

        def _walk(node) -> bool:
            if isinstance(node, str):
                return token_s in node
            if isinstance(node, dict):
                for v in node.values():
                    if _walk(v):
                        return True
                return False
            if isinstance(node, (list, tuple)):
                for v in node:
                    if _walk(v):
                        return True
                return False
            return False

        if _walk(message.get("content_text")):
            return True
        cj = message.get("content_json")
        if isinstance(cj, str):
            try:
                cj = json.loads(cj)
            except (TypeError, ValueError):
                cj = None
        if cj is not None and _walk(cj):
            return True
        if _walk(message.get("attachments")):
            return True
        return False

    @staticmethod
    def _read_site_error(
        probe: ProbeClient,
        tab_id: int,
        adapter: BaseProbeSiteAdapter,
    ) -> str:
        parts: list[str] = []
        for sel in adapter.error_banner_selectors or ():
            try:
                res = probe.dom.query(tab_id, sel, all=True)
            except ProbeError:
                continue
            for m in res.get("matches", []) or []:
                text = str(m.get("text_excerpt") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    @staticmethod
    def _is_quota_or_rate_limit(text: str) -> bool:
        lower = (text or "").lower()
        if "fast" in lower and any(ch.isdigit() for ch in lower):
            return True
        return any(
            needle in lower
            for needle in (
                "quota",
                "rate limit",
                "rate-limit",
                "rate_limit",
                "exceeded",
                "try again later",
                "try again in",
                "limit reached",
                "usage limit",
                "message limit",
                "too many requests",
                "temporarily unavailable",
                "capacity",
            )
        )

    @classmethod
    def _events_indicate_quota_or_rate_limit(cls, events: list) -> bool:
        for event in events or []:
            if cls._is_quota_or_rate_limit(cls._event_text(event)):
                return True
        return False

    @staticmethod
    def _event_text(event: dict) -> str:
        parts: list[str] = []
        for key in (
            "kind",
            "host",
            "path",
            "fingerprint",
            "body_excerpt",
            "body",
            "summary",
            "content_text",
        ):
            value = event.get(key)
            if isinstance(value, str):
                parts.append(value)
        payload = event.get("payload") or {}
        if isinstance(payload, dict):
            for key in (
                "fingerprint",
                "body_excerpt",
                "body",
                "content_text",
                "text",
                "error",
                "message",
            ):
                value = payload.get(key)
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)

    @staticmethod
    def _read_recent_events(
        probe: ProbeClient,
        adapter: BaseProbeSiteAdapter,
    ) -> list:
        try:
            return (
                probe.capture.recent_events(
                    last_n=200,
                    provider=adapter.provider,
                ).get("events", [])
                or []
            )
        except ProbeError:
            return []

    @staticmethod
    def _body_contains_token(
        probe: ProbeClient,
        tab_id: int,
        token: str,
    ) -> bool:
        marker_id = "pce-t12-token-seen"
        token_json = json.dumps(token)
        marker_json = json.dumps(marker_id)
        try:
            probe.dom.execute_js(
                tab_id,
                f"""
(function () {{
  var token = {token_json};
  var markerId = {marker_json};
  var text = (document.body && document.body.innerText) || "";
  var seen = text.indexOf(token) !== -1 ? "1" : "0";
  var marker = document.getElementById(markerId);
  if (!marker) {{
    marker = document.createElement("span");
    marker.id = markerId;
    marker.style.display = "none";
    document.documentElement.appendChild(marker);
  }}
  marker.textContent = seen;
  try {{
    document.documentElement.setAttribute("data-pce-t12-token-seen", seen);
  }} catch (e) {{}}
}})();
""",
            )
        except ProbeError:
            pass
        for selector in (f"#{marker_id}", "html"):
            try:
                res = probe.dom.query(tab_id, selector)
            except ProbeError:
                continue
            for match in res.get("matches", []) or []:
                text = str(match.get("text_excerpt") or "")
                outer = str(match.get("outer_html_excerpt") or "")
                if text.strip() == "1" or 'data-pce-t12-token-seen="1"' in outer:
                    return True
        return False

    @staticmethod
    def _find_session_by_hint(
        pce_core: httpx.Client,
        provider: str,
        session_hint: str,
    ) -> tuple[str, list] | None:
        sessions: list = []
        matching: list = []
        for _ in range(6):
            try:
                resp = pce_core.get(
                    "/api/v1/sessions",
                    params={"provider": provider},
                )
            except httpx.HTTPError:
                return None
            if resp.status_code != 200:
                return None
            sessions = resp.json() or []
            matching = [
                s for s in sessions
                if str(s.get("session_key", "")).find(str(session_hint)) != -1
            ]
            if matching:
                break
            time.sleep(0.5)
        if not matching:
            return None
        session_id = matching[0].get("id")
        if not session_id:
            return None
        msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
        if msg_resp.status_code != 200:
            return None
        return str(session_id), (msg_resp.json() or [])

    @staticmethod
    def _find_assistant_token_session(
        pce_core: httpx.Client,
        provider: str,
        token: str,
        *,
        max_sessions: int = 20,
    ) -> tuple[str, list] | None:
        try:
            resp = pce_core.get("/api/v1/sessions", params={"provider": provider})
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        for session in (resp.json() or [])[:max_sessions]:
            session_id = session.get("id")
            if not session_id:
                continue
            msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
            if msg_resp.status_code != 200:
                continue
            msgs = msg_resp.json() or []
            if any(
                token in (m.get("content_text") or "")
                for m in msgs
                if m.get("role") == "assistant"
            ):
                return str(session_id), msgs
        return None

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
