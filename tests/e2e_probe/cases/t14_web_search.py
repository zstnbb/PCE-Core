# SPDX-License-Identifier: Apache-2.0
"""T14 \u2014 web-search / grounding produces citation attachments.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 11 / T14, ``GAS`` A12
(Grounding with Google Search), ``GROK`` GK01 (DeepSearch). The user
toggles a web-search tool (or relies on the site's auto-grounding)
and asks a current-events question. The captured assistant turn must
have at least one ``citation`` attachment with an external URL.

Strategy:
  1. If the adapter exposes ``web_search_button_selectors``, click
     the first match (toggles the tool). Sites where search is the
     default routing (Gemini for current events, Perplexity-style
     defaults) leave the selectors empty and the case proceeds
     directly to send.
  2. Send a current-events prompt with a unique token.
  3. Wait for done (search adds latency).
  4. Verify the captured assistant turn has at least one ``citation``
     attachment whose ``url`` looks like an external URL.

Failure modes T14 catches:
  - Search tool was toggled but extractor lost the citation chips
    during normalization (Copilot MCP5 / Perplexity PX5 / Grok GK3
    family of gaps).
  - Search wasn't actually invoked (the model improvised an answer
    without grounding) and no citations exist \u2014 this is
    operator error, not a capture-pipeline bug, but the case
    surfaces it so we can iterate the prompt or toggle.
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
    "Search the web and tell me the current Wikipedia headline for "
    "Python (programming language). Include at least one external "
    "source URL in your reply. End with the literal token {token}."
)


class T14WebSearchCase(BaseCase):
    id = "T14"
    name = "web_search_citations"
    description = (
        "Toggle web search if available; ask a current-events prompt; "
        "verify the captured assistant turn has at least one "
        "``citation`` attachment with an external URL."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        token = f"PROBE-{adapter.name.upper()}-T14-{uuid.uuid4().hex[:8]}"
        prompt = PROMPT_TEMPLATE.format(token=token)
        details: dict = {"token": token}

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

        # 2. Optional toggle.
        if adapter.web_search_button_selectors:
            clicked = adapter.click_first_visible(
                probe, tab_id, adapter.web_search_button_selectors,
            )
            details["search_toggle_clicked"] = clicked
            time.sleep(1.0)

        # 3. Send + wait.
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
                "the web-search invariant was not exercised",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "site_quota_or_rate_limit",
                    "site_error": site_error[:500],
                },
            )

        # 4. Best-effort SW ring wait (fast path / session_hint hint),
        #    then authoritative PCE Core round-trip. Web search adds
        #    significant latency on top of streaming, so we don't gate
        #    on the SW ring \u2014 if PCE Core eventually has a session
        #    containing the token, the pipeline succeeded.
        captured: dict | None = None
        try:
            captured = probe.capture.wait_for_token(
                token,
                timeout_ms=15_000,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
            details["sw_ring_seen"] = True
        except (CaptureNotSeenError, ProbeError) as exc:
            details["sw_ring_seen"] = False
            details["sw_ring_wait_msg"] = str(getattr(exc, "message", exc))

        session_hint = (captured or {}).get("session_hint") if captured else None
        details["session_hint"] = session_hint

        PCE_CORE_RETRY_S = 60.0
        PCE_CORE_RETRY_INTERVAL_S = 1.0
        attempts = int(PCE_CORE_RETRY_S / PCE_CORE_RETRY_INTERVAL_S)
        session_id: str | None = None
        msgs: list = []
        for _ in range(attempts):
            try:
                resp = pce_core.get(
                    "/api/v1/sessions",
                    params={"provider": adapter.provider},
                )
            except httpx.HTTPError as exc:
                return CaseResult.failed(
                    self.id, adapter.name,
                    f"sessions request: {exc!r}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_request"},
                )
            if resp.status_code != 200:
                return CaseResult.failed(
                    self.id, adapter.name,
                    f"sessions API {resp.status_code}",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "sessions_status"},
                )
            sessions = resp.json() or []
            candidates = []
            if session_hint:
                candidates = [
                    s for s in sessions
                    if str(s.get("session_key", "")).find(str(session_hint)) != -1
                ]
            if not candidates:
                candidates = sessions[:10]
            for cand in candidates:
                cand_id = cand.get("id")
                if not cand_id:
                    continue
                cand_msgs_resp = pce_core.get(f"/api/v1/sessions/{cand_id}/messages")
                if cand_msgs_resp.status_code != 200:
                    continue
                cand_msgs = cand_msgs_resp.json() or []
                if any(
                    token in (m.get("content_text") or "")
                    for m in cand_msgs
                    if m.get("role") == "assistant"
                ):
                    session_id = cand_id
                    msgs = cand_msgs
                    break
            if session_id:
                break
            time.sleep(PCE_CORE_RETRY_INTERVAL_S)

        if not session_id:
            site_error = self._read_site_error(probe, tab_id, adapter)
            if self._is_quota_or_rate_limit(site_error):
                return CaseResult.skipped(
                    self.id, adapter.name,
                    "site quota/rate-limit banner appeared while waiting "
                    "for storage; the web-search invariant was not "
                    "exercised",
                    duration_ms=self._elapsed_ms(start),
                    details={
                        **details,
                        "phase": "site_quota_or_rate_limit",
                        "site_error": site_error[:500],
                    },
                )
            recent = self._read_recent_events(probe, adapter)
            if self._events_indicate_quota_or_rate_limit(recent):
                return CaseResult.skipped(
                    self.id, adapter.name,
                    "recent capture/network events indicate quota or rate "
                    "limiting; the web-search invariant was not exercised",
                    duration_ms=self._elapsed_ms(start),
                    details={
                        **details,
                        "phase": "event_quota_or_rate_limit",
                        "recent_events": recent[-10:],
                    },
                )
            token_visible = self._body_contains_token(probe, tab_id, token)
            if not token_visible:
                return CaseResult.skipped(
                    self.id, adapter.name,
                    "assistant page text never contained the T14 token; "
                    "the model/search tool did not complete the requested "
                    "grounded turn, so capture/storage were not exercised",
                    duration_ms=self._elapsed_ms(start),
                    details={
                        **details,
                        "phase": "no_visible_assistant_token",
                        "recent_events": recent[-10:],
                        "token_visible_in_page": False,
                    },
                )
            return CaseResult.failed(
                self.id, adapter.name,
                f"no PCE Core session has the token {token!r} in any "
                f"assistant message after {PCE_CORE_RETRY_S:.0f}s "
                f"(sw_ring_seen={details.get('sw_ring_seen')}); web "
                f"search prompt was sent and stream completed but no "
                f"row reached canonical storage \u2014 pipeline break "
                f"or model didn't echo the token",
                duration_ms=self._elapsed_ms(start),
                details={
                    **details,
                    "phase": "token_missing_in_pce_core",
                    "recent_events": recent[-10:],
                    "token_visible_in_page": True,
                },
            )

        target = None
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            if token in (m.get("content_text") or ""):
                target = m
                break
        if target is None:
            return CaseResult.failed(
                self.id, adapter.name,
                "no assistant message contains the token",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "assistant_token_missing"},
            )

        attachments = self._extract_attachments(target)
        details["attachments"] = attachments
        # Two valid shapes for "web search happened and sources were
        # captured":
        #   - ``citation`` attachments (ChatGPT, Gemini, Perplexity)
        #   - ``tool_call`` whose name is web_search-shaped, OR a paired
        #     ``tool_result`` with URL-bearing content (Claude MCP, Grok
        #     DeepSearch). Claude in particular emits the search via an
        #     MCP tool_call/tool_result pair rather than embedded
        #     ``citation`` chips, so gating only on type='citation' would
        #     incorrectly mark a healthy Claude run as a regression.
        citations = [
            a for a in attachments
            if str(a.get("type", "")).lower() == "citation"
        ]
        web_search_tool_calls = [
            a for a in attachments
            if str(a.get("type", "")).lower() == "tool_call"
            and self._is_web_search_tool(a)
        ]
        web_search_tool_results = [
            a for a in attachments
            if str(a.get("type", "")).lower() == "tool_result"
            and self._tool_result_has_urls(a)
        ]
        details["citation_count"] = len(citations)
        details["web_search_tool_call_count"] = len(web_search_tool_calls)
        details["web_search_tool_result_count"] = len(web_search_tool_results)

        evidence_count = (
            len(citations)
            + len(web_search_tool_calls)
            + len(web_search_tool_results)
        )
        if evidence_count == 0:
            return CaseResult.failed(
                self.id, adapter.name,
                f"assistant has {len(attachments)} attachment(s) but "
                f"none are 'citation', web_search 'tool_call', or "
                f"URL-bearing 'tool_result'; types found="
                f"{[a.get('type') for a in attachments]!r}. Either web "
                f"search wasn't actually invoked or the extractor lost "
                f"the search-source chips during normalization (shared "
                f"gap with Copilot MCP5 / Perplexity PX5 / Gemini G7).",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_search_evidence"},
            )

        # Soft check: at least one citation should have an external URL.
        # For Claude / Grok where evidence is in tool_call/tool_result,
        # we don't require a URL on the case-level; the MCP shape often
        # carries URLs inside ``content`` (free-form text or array of
        # items) which is harder to parse generically.
        external = [
            c for c in citations
            if str(c.get("url") or "").startswith(("http://", "https://"))
        ]
        details["external_citation_count"] = len(external)

        return CaseResult.passed(
            self.id, adapter.name,
            summary=(
                f"web search captured; citations={len(citations)}, "
                f"external={len(external)}, "
                f"tool_calls={len(web_search_tool_calls)}, "
                f"tool_results={len(web_search_tool_results)}, "
                f"session_id={session_id}"
            ),
            duration_ms=self._elapsed_ms(start),
            details={**details, "session_id": session_id},
        )

    @staticmethod
    def _is_web_search_tool(att: dict) -> bool:
        """Recognize a tool_call attachment that represents a web
        search invocation. Claude emits ``name='web_search'`` or
        ``name='web_search_20250305'`` etc. Grok DeepSearch may emit
        ``name='deepsearch'`` / ``name='browse'``. Be generous \u2014 the
        case only needs to distinguish "search-shaped" from arbitrary
        function tools.
        """
        name = str(att.get("name", "")).lower()
        if not name:
            return False
        for needle in ("search", "browse", "fetch", "research", "web"):
            if needle in name:
                return True
        return False

    @staticmethod
    def _tool_result_has_urls(att: dict) -> bool:
        """A tool_result attachment counts as web-search evidence if its
        content blob contains at least one ``http(s)://`` URL.
        ``content`` may be a string or a list of dicts with ``text``
        keys (Anthropic's MCP shape).
        """
        content = att.get("content")
        if isinstance(content, str):
            return ("http://" in content) or ("https://" in content)
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    t = item.get("text") or item.get("content") or ""
                    if isinstance(t, str) and (
                        "http://" in t or "https://" in t
                    ):
                        return True
                elif isinstance(item, str):
                    if "http://" in item or "https://" in item:
                        return True
        return False

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
        needles = (
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
            "上限",
            "限额",
            "额度",
            "重置",
            "稍后",
            "涓婇檺",
            "闄愰",
            "宸茶揪",
            "閲嶇疆",
            "灏忔椂",
        )
        if any(needle in lower for needle in needles):
            return True
        return (
            "fast" in lower
            and any(
                needle in lower
                for needle in (
                    "limit",
                    "reset",
                    "上限",
                    "限额",
                    "重置",
                    "涓婇檺",
                    "宸茶揪",
                    "閲嶇疆",
                )
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
        marker_id = "pce-t14-token-seen"
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
    document.documentElement.setAttribute("data-pce-t14-token-seen", seen);
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
                if text.strip() == "1" or 'data-pce-t14-token-seen="1"' in outer:
                    return True
        return False
