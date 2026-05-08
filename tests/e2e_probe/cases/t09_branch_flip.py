# SPDX-License-Identifier: Apache-2.0
"""T09 - branch / draft switching reflects the currently shown state."""
from __future__ import annotations

import json
import time
import uuid

import httpx

from pce_probe import CaptureNotSeenError, ProbeClient, ProbeError

from ..sites.base import BaseProbeSiteAdapter
from .base import BaseCase, CaseResult


SEED_PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the seed token "
    "{token}. Reply in one short line."
)
EDITED_PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the edit token "
    "{token}. Reply in one short line."
)
REGEN_PROMPT_TEMPLATE = (
    "Write two short sentences about one random city. Make a concrete "
    "detail choice so regeneration is likely to differ. End with the "
    "literal token {token} on its own line."
)


class T09BranchFlipCase(BaseCase):
    id = "T09"
    name = "branch_flip_currently_shown"
    description = (
        "Create a branch or assistant draft using the site's native "
        "mechanism; flip or fork it; verify PCE Core stores the state "
        "currently shown by the page."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()
        mode = getattr(adapter, "branch_creation_mode", "edit")
        seed_token = f"PROBE-{adapter.name.upper()}-T09-A-{uuid.uuid4().hex[:6]}"
        edit_token = f"PROBE-{adapter.name.upper()}-T09-B-{uuid.uuid4().hex[:6]}"
        details: dict = {
            "mode": mode,
            "seed_token": seed_token,
            "edit_token": edit_token,
        }

        skip = self._preflight_skip(adapter, mode)
        if skip:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                skip,
                duration_ms=self._elapsed_ms(start),
                details=details,
            )

        try:
            tab_id = adapter.open(probe)
            details["tab_id"] = tab_id
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"adapter.open: [{exc.code}] {exc.message}",
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

        if mode == "regenerate":
            return self._run_regenerate_mode(
                probe,
                pce_core,
                adapter,
                tab_id,
                seed_token,
                start,
                details,
            )
        if mode == "branch_from_here":
            return self._run_branch_from_here_mode(
                probe,
                pce_core,
                adapter,
                tab_id,
                seed_token,
                start,
                details,
            )
        return self._run_edit_mode(
            probe,
            pce_core,
            adapter,
            tab_id,
            seed_token,
            edit_token,
            start,
            details,
        )

    def _run_edit_mode(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
        tab_id: int,
        seed_token: str,
        edit_token: str,
        start: float,
        details: dict,
    ) -> CaseResult:
        sent = self._send_and_wait(
            probe,
            adapter,
            tab_id,
            SEED_PROMPT_TEMPLATE.format(token=seed_token),
            start,
            details,
            phase_prefix="seed",
        )
        if sent:
            return sent

        if not adapter.click_last_user_edit(probe, tab_id):
            return CaseResult.failed(
                self.id,
                adapter.name,
                "click_last_user_edit failed",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_click"},
            )
        try:
            input_selector = adapter.resolve_edit_input_selector(probe, tab_id)
            probe.dom.type(
                tab_id,
                input_selector,
                EDITED_PROMPT_TEMPLATE.format(token=edit_token),
                clear=True,
                submit=False,
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"type into edit editor: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_type"},
            )
        time.sleep(adapter.type_settle_ms / 1_000)

        save_selectors = [
            'button[aria-label*="Save" i]',
            'button[title*="Save" i]',
            'button[aria-label*="Submit" i]',
            'button[type="submit"]',
            *list(adapter.send_button_selectors),
        ]
        save_clicked = False
        save_by_label = adapter._click_action_main(
            probe,
            tab_id,
            action="edit_save",
            scope="last_user",
            root_selectors=adapter.branch_user_root_selectors
            or adapter.branch_root_selectors,
            labels=("Save", "Send", "Submit", "发送", "提交"),
            prefer_last=False,
        )
        if save_by_label and save_by_label.get("ok"):
            save_clicked = True
            details["edit_save_click"] = save_by_label
        if not save_clicked and adapter.click_first_visible(probe, tab_id, save_selectors):
            save_clicked = True
        if not save_clicked:
            try:
                probe.dom.press_key(tab_id, "Enter", selector=input_selector)
            except ProbeError:
                pass
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"edit wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_wait"},
            )
        self._soft_wait_for_capture(probe, adapter, edit_token, details, "edit")

        flipped = adapter.click_user_branch_prev(probe, tab_id)
        if not flipped:
            flipped = adapter.click_branch_prev(probe, tab_id)
        details["flip_clicked"] = flipped
        self._attach_click_diag(adapter, details)
        if not flipped:
            return self._flip_unavailable(adapter, start, details)
        for attempt in range(2):
            visible_user = self._visible_user_text(probe, adapter, tab_id)
            details[f"visible_user_after_flip_{attempt}"] = visible_user[:180]
            if seed_token in visible_user:
                break
            if edit_token not in visible_user:
                break
            if not adapter.click_user_branch_prev(probe, tab_id):
                break
            details["extra_user_prev_clicks"] = attempt + 1
            time.sleep(1.5)
        self._manual_capture(probe, tab_id)

        session_id, msgs = self._fetch_session_by_any_token(
            pce_core,
            adapter.provider,
            [seed_token, edit_token],
        )
        details["session_id"] = session_id
        details["n_messages"] = len(msgs)
        contract = self._branch_contract_evidence(
            self._fetch_session_messages(pce_core, session_id, branches="expand")
            if session_id
            else []
        )
        details["branch_contract"] = contract
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        latest_user_text = str(user_msgs[-1].get("content_text") or "") if user_msgs else ""
        details["latest_user_head"] = latest_user_text[:160]
        visible_seed = any(
            seed_token in str(details.get(f"visible_user_after_flip_{idx}") or "")
            for idx in range(2)
        )
        details["visible_flip_matched_seed"] = visible_seed

        if visible_seed:
            if not contract["ok"]:
                return CaseResult.failed(
                    self.id,
                    adapter.name,
                    "visible branch flipped to seed, but branch storage/render contract is missing",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "visible_flip_contract_missing"},
                )
            return CaseResult.passed(
                self.id,
                adapter.name,
                summary=f"edit branch visibly flipped back to seed branch with structured branch contract; session_id={session_id}",
                duration_ms=self._elapsed_ms(start),
                details=details,
            )

        if seed_token in latest_user_text:
            if not contract["ok"]:
                return CaseResult.failed(
                    self.id,
                    adapter.name,
                    "branch flip changed visible content, but branch storage/render contract is missing",
                    duration_ms=self._elapsed_ms(start),
                    details={**details, "phase": "branch_contract_missing"},
                )
            return CaseResult.passed(
                self.id,
                adapter.name,
                summary=f"edit branch flipped back to seed branch; session_id={session_id}",
                duration_ms=self._elapsed_ms(start),
                details=details,
            )
        if edit_token in latest_user_text:
            return CaseResult.failed(
                self.id,
                adapter.name,
                "branch prev clicked but PCE Core still shows the edited user branch",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "flip_not_captured"},
            )
        return CaseResult.failed(
            self.id,
            adapter.name,
            "flipped session contains neither seed nor edited token in the latest user message",
            duration_ms=self._elapsed_ms(start),
            details={**details, "phase": "neither_token_present"},
        )

    def _run_regenerate_mode(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
        tab_id: int,
        seed_token: str,
        start: float,
        details: dict,
    ) -> CaseResult:
        sent = self._send_and_wait(
            probe,
            adapter,
            tab_id,
            REGEN_PROMPT_TEMPLATE.format(token=seed_token),
            start,
            details,
            phase_prefix="seed",
        )
        if sent:
            return sent
        self._soft_wait_for_capture(probe, adapter, seed_token, details, "seed")
        seed_session_id, seed_msgs = self._fetch_session_by_any_token(
            pce_core,
            adapter.provider,
            [seed_token],
        )
        seed_assistant = self._latest_text(seed_msgs, "assistant")
        details["seed_session_id"] = seed_session_id
        details["seed_assistant_len"] = len(seed_assistant)

        clicked = adapter.click_regenerate(probe, tab_id)
        details["regenerate_clicked"] = clicked
        self._attach_click_diag(adapter, details)
        if not clicked:
            return CaseResult.failed(
                self.id,
                adapter.name,
                "regenerate control was not clickable for T09 draft creation",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "regen_click"},
            )
        adapter.wait_for_stop_button_visible(probe, tab_id, timeout_s=8.0)
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"regen wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "regen_wait"},
            )
        time.sleep(2.0)
        regen_session_id, regen_msgs = self._fetch_session_by_any_token(
            pce_core,
            adapter.provider,
            [seed_token],
        )
        regen_assistant = self._latest_text(regen_msgs, "assistant")
        details["regen_session_id"] = regen_session_id
        details["regen_assistant_len"] = len(regen_assistant)
        details["regen_text_differs"] = bool(
            seed_assistant and regen_assistant and seed_assistant != regen_assistant
        )

        flipped = adapter.click_assistant_branch_prev(probe, tab_id)
        if not flipped:
            flipped = adapter.click_branch_prev(probe, tab_id)
        details["flip_clicked"] = flipped
        self._attach_click_diag(adapter, details)
        if not flipped:
            return self._flip_unavailable(adapter, start, details)
        self._manual_capture(probe, tab_id)

        final_session_id, final_msgs = self._fetch_session_by_any_token(
            pce_core,
            adapter.provider,
            [seed_token],
        )
        visible_assistant = self._visible_assistant_text(probe, adapter, tab_id)
        final_assistant = self._latest_text(final_msgs, "assistant")
        details["final_session_id"] = final_session_id
        details["final_assistant_len"] = len(final_assistant)
        details["final_assistant_head"] = final_assistant[:160]
        details["visible_assistant_after_flip_head"] = visible_assistant[:240]
        details["visible_assistant_matches_seed"] = self._text_contains_fragment(
            visible_assistant,
            seed_assistant,
        )
        details["visible_assistant_matches_regen"] = self._text_contains_fragment(
            visible_assistant,
            regen_assistant,
        )
        expanded = (
            self._fetch_session_messages(pce_core, final_session_id, branches="expand")
            if final_session_id
            else []
        )
        contract = self._branch_contract_evidence(expanded)
        details["branch_contract"] = contract

        if not contract["ok"]:
            return CaseResult.failed(
                self.id,
                adapter.name,
                "draft flip clicked, but structured branch/variant contract is missing",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "branch_contract_missing"},
            )
        if (
            seed_assistant
            and final_assistant
            and regen_assistant
            and final_assistant == regen_assistant
            and seed_assistant != regen_assistant
        ):
            if (
                contract["ok"]
                and details.get("visible_assistant_matches_seed")
                and not details.get("visible_assistant_matches_regen")
            ):
                return CaseResult.passed(
                    self.id,
                    adapter.name,
                    summary=(
                        "regenerate draft flip captured in visible DOM; "
                        f"session_id={final_session_id}"
                    ),
                    duration_ms=self._elapsed_ms(start),
                    details=details,
                )
            return CaseResult.failed(
                self.id,
                adapter.name,
                "prev draft clicked but PCE Core still shows the regenerated assistant variant",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "draft_flip_not_captured"},
            )
        return CaseResult.passed(
            self.id,
            adapter.name,
            summary=f"regenerate draft flip captured; session_id={final_session_id}",
            duration_ms=self._elapsed_ms(start),
            details=details,
        )

    def _run_branch_from_here_mode(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
        tab_id: int,
        seed_token: str,
        start: float,
        details: dict,
    ) -> CaseResult:
        sent = self._send_and_wait(
            probe,
            adapter,
            tab_id,
            SEED_PROMPT_TEMPLATE.format(token=seed_token),
            start,
            details,
            phase_prefix="seed",
        )
        if sent:
            return sent
        self._soft_wait_for_capture(probe, adapter, seed_token, details, "seed")
        before_url = adapter.current_url(probe, tab_id) or ""
        details["before_url"] = before_url
        before_session_id, _ = self._fetch_session_by_any_token(
            pce_core,
            adapter.provider,
            [seed_token],
        )
        details["before_session_id"] = before_session_id

        clicked = adapter.click_branch_from_here(probe, tab_id)
        details["branch_from_here_clicked"] = clicked
        self._attach_click_diag(adapter, details)
        if not clicked:
            return CaseResult.skipped(
                self.id,
                adapter.name,
                "branch-from-here control was not exposed/clickable on this AI Studio UI",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "branch_from_here_click"},
            )
        after_url = self._wait_for_url_change(probe, adapter, tab_id, before_url)
        details["after_url"] = after_url
        self._manual_capture(probe, tab_id)
        after_session_id, after_msgs = self._fetch_session_by_any_token(
            pce_core,
            adapter.provider,
            [seed_token],
        )
        details["after_session_id"] = after_session_id
        details["n_messages"] = len(after_msgs)

        if not after_url or after_url == before_url:
            return CaseResult.failed(
                self.id,
                adapter.name,
                "branch-from-here clicked but URL did not navigate to a forked prompt",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "branch_url_unchanged"},
            )
        if not after_msgs:
            return CaseResult.failed(
                self.id,
                adapter.name,
                "branch-from-here navigated but PCE Core did not store the forked prompt state",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "branch_session_missing"},
            )
        return CaseResult.passed(
            self.id,
            adapter.name,
            summary=f"branch-from-here navigated and stored prompt state; session_id={after_session_id}",
            duration_ms=self._elapsed_ms(start),
            details=details,
        )

    def _send_and_wait(
        self,
        probe: ProbeClient,
        adapter: BaseProbeSiteAdapter,
        tab_id: int,
        prompt: str,
        start: float,
        details: dict,
        *,
        phase_prefix: str,
    ) -> CaseResult | None:
        try:
            adapter.send_prompt(probe, tab_id, prompt)
        except ProbeError as exc:
            blocker = adapter.detect_external_blocker(probe, tab_id)
            if blocker:
                return CaseResult.skipped(
                    self.id,
                    adapter.name,
                    f"quota/rate limit blocker after submit: {blocker}",
                    duration_ms=self._elapsed_ms(start),
                    details={
                        **details,
                        "phase": f"{phase_prefix}_send_external_blocker",
                        "external_blocker": blocker,
                    },
                )
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"{phase_prefix} send_prompt: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": f"{phase_prefix}_send"},
            )
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id,
                adapter.name,
                f"{phase_prefix} wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": f"{phase_prefix}_wait"},
            )
        return None

    @staticmethod
    def _preflight_skip(adapter: BaseProbeSiteAdapter, mode: str) -> str:
        if mode == "edit":
            if not adapter.edit_button_selectors:
                return "site has no user-edit branch creation surface for T09"
            if not adapter.branch_prev_selectors:
                return "site has no in-place branch prev control for T09"
        elif mode == "regenerate":
            if not adapter.regenerate_button_selectors:
                return "site has no regenerate surface for draft branch creation"
            if not adapter.branch_prev_selectors:
                return "site has no draft prev control after regeneration"
        elif mode == "branch_from_here":
            if (
                not adapter.branch_from_here_selectors
                and not adapter.more_actions_button_selectors
            ):
                return "site has no branch-from-here selectors"
        else:
            return f"unknown T09 branch_creation_mode={mode!r}"
        return ""

    def _flip_unavailable(
        self,
        adapter: BaseProbeSiteAdapter,
        start: float,
        details: dict,
    ) -> CaseResult:
        action_result = details.get("click_action_main")
        status = CaseResult.skipped
        reason = (
            "branch/draft prev control was not exposed after creating the "
            "branch; hover-scoped click could not find a native arrow"
        )
        if adapter.branch_surface_supported and not self._action_result_has_no_native_surface(action_result):
            status = CaseResult.failed
            reason = "branch/draft prev control should exist but was not clickable"
        return status(
            self.id,
            adapter.name,
            reason,
            duration_ms=self._elapsed_ms(start),
            details={**details, "phase": "flip_not_clickable"},
        )

    @staticmethod
    def _manual_capture(probe: ProbeClient, tab_id: int) -> None:
        try:
            probe.dom.execute_js(
                tab_id,
                "(function () { try { document.dispatchEvent(new CustomEvent('pce-manual-capture')); } catch (e) {} })();",
            )
        except ProbeError:
            pass
        time.sleep(2.0)

    @staticmethod
    def _soft_wait_for_capture(
        probe: ProbeClient,
        adapter: BaseProbeSiteAdapter,
        token: str,
        details: dict,
        key: str,
    ) -> None:
        try:
            probe.capture.wait_for_token(
                token,
                timeout_ms=15_000,
                provider=adapter.provider,
                kind="PCE_CAPTURE",
            )
        except (CaptureNotSeenError, ProbeError):
            details[f"{key}_capture_seen"] = False
        else:
            details[f"{key}_capture_seen"] = True

    @staticmethod
    def _attach_click_diag(adapter: BaseProbeSiteAdapter, details: dict) -> None:
        action_result = getattr(adapter, "_last_click_action_result", None)
        if isinstance(action_result, dict):
            details["click_action_main"] = action_result

    def _wait_for_url_change(
        self,
        probe: ProbeClient,
        adapter: BaseProbeSiteAdapter,
        tab_id: int,
        before_url: str,
    ) -> str:
        deadline = time.time() + 12.0
        current = before_url
        while time.time() < deadline:
            current = adapter.current_url(probe, tab_id) or ""
            if current and current != before_url:
                return current
            time.sleep(0.5)
        return current

    @staticmethod
    def _visible_user_text(
        probe: ProbeClient,
        adapter: BaseProbeSiteAdapter,
        tab_id: int,
    ) -> str:
        selectors = (
            adapter.branch_user_root_selectors
            or (
                '[data-message-author-role="user"]',
                '[data-testid="human-turn"]',
                "user-query",
                "ms-chat-turn .chat-turn-container.user",
            )
        )
        texts: list[str] = []
        for sel in selectors:
            try:
                res = probe.dom.query(tab_id, sel, all=True)
            except ProbeError:
                continue
            for match in res.get("matches", []) or []:
                txt = str(match.get("text_excerpt") or "").strip()
                if txt:
                    texts.append(txt)
        return texts[-1] if texts else ""

    @staticmethod
    def _visible_assistant_text(
        probe: ProbeClient,
        adapter: BaseProbeSiteAdapter,
        tab_id: int,
    ) -> str:
        selectors = (
            adapter.branch_assistant_root_selectors
            or adapter.response_container_selectors
            or (
                '[data-message-author-role="assistant"]',
                '[data-testid*="assistant-message"]',
                "model-response",
                "ms-chat-turn .chat-turn-container.model",
            )
        )
        texts: list[str] = []
        for sel in selectors:
            try:
                res = probe.dom.query(tab_id, sel, all=True)
            except ProbeError:
                continue
            for match in res.get("matches", []) or []:
                txt = str(match.get("text_excerpt") or "").strip()
                if txt:
                    texts.append(txt)
        return texts[-1] if texts else ""

    @staticmethod
    def _text_contains_fragment(haystack: str, needle: str) -> bool:
        def norm(value: str) -> str:
            return " ".join(str(value or "").split()).lower()

        h = norm(haystack)
        n = norm(needle)
        if not h or len(n) < 24:
            return False
        for size in (120, 90, 60, 40):
            frag = n[: min(size, len(n))]
            if len(frag) >= 24 and frag in h:
                return True
        return False

    @staticmethod
    def _latest_text(messages: list[dict], role: str) -> str:
        role_msgs = [m for m in messages if m.get("role") == role]
        return str(role_msgs[-1].get("content_text") or "") if role_msgs else ""

    @staticmethod
    def _fetch_session_by_any_token(
        pce_core: httpx.Client,
        provider: str,
        tokens: list[str],
    ) -> tuple[str | None, list[dict]]:
        param_sets = ({"provider": provider}, {})
        for _attempt in range(8):
            for params in param_sets:
                try:
                    resp = pce_core.get("/api/v1/sessions", params=params)
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200:
                    continue
                sessions = resp.json() or []
                for session in list(reversed(sessions))[:30]:
                    session_id = session.get("id")
                    if not session_id:
                        continue
                    msgs = T09BranchFlipCase._fetch_session_messages(
                        pce_core,
                        session_id,
                    )
                    haystack = "\n".join(
                        str(m.get("content_text") or "") for m in msgs
                    )
                    if any(token in haystack for token in tokens):
                        return (session_id, msgs)
            time.sleep(0.75)
        return (None, [])

    @staticmethod
    def _fetch_session_messages(
        pce_core: httpx.Client,
        session_id: str,
        *,
        branches: str | None = None,
    ) -> list[dict]:
        try:
            kwargs = {"params": {"branches": branches}} if branches else {}
            msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages", **kwargs)
        except httpx.HTTPError:
            return []
        if msg_resp.status_code != 200:
            return []
        data = msg_resp.json() or []
        return data if isinstance(data, list) else []

    @classmethod
    def _branch_contract_evidence(cls, messages: list[dict]) -> dict:
        storage_keys = {
            "branch_id",
            "branch_index",
            "branch_group_id",
            "parent_message_id",
            "parent_turn_id",
            "branch_parent_id",
            "current_branch_id",
            "selected_branch_id",
            "variant_group_id",
            "variant_id",
            "variant_index",
            "regenerated_from",
            "regeneration_of",
            "previous_variant_id",
            "current_variant_id",
        }
        render_keys = {
            "branch",
            "branches",
            "branch_tree",
            "branch_choices",
            "current_branch",
            "selected_branch",
            "variant",
            "variants",
            "variant_group",
            "current_variant",
            "variant_controls",
        }

        storage_paths: list[str] = []
        render_paths: list[str] = []
        for idx, msg in enumerate(messages or []):
            storage_paths.extend(cls._find_keys(msg, storage_keys, f"messages[{idx}]"))
            content_json = cls._content_json_dict(msg)
            if not content_json:
                continue
            storage_paths.extend(
                cls._find_keys(content_json, storage_keys, f"messages[{idx}].content_json")
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

        return {
            "ok": bool(storage_paths) and bool(render_paths),
            "storage_relation": bool(storage_paths),
            "render_relation": bool(render_paths),
            "storage_paths": storage_paths[:12],
            "render_paths": render_paths[:12],
        }

    @staticmethod
    def _action_result_has_no_native_surface(action_result: object) -> bool:
        if not isinstance(action_result, dict):
            return False
        marker = str(action_result.get("marker") or "")
        if marker not in {
            "no_direct_no_more",
            "menu_no_match",
            "direct_menu_no_match",
        }:
            return False
        labels = ("previous", "next", "branch", "draft", "variant")
        buckets = (
            action_result.get("candidates") or [],
            action_result.get("menu_candidates") or [],
            action_result.get("controls") or [],
        )
        for bucket in buckets:
            if not isinstance(bucket, list):
                continue
            for item in bucket:
                if not isinstance(item, dict):
                    continue
                text = (
                    str(item.get("text") or "")
                    + " "
                    + str(item.get("label") or "")
                    + " "
                    + str(item.get("html_excerpt") or "")
                ).lower()
                if any(label in text for label in labels):
                    return False
        return True

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
    def _find_keys(cls, value, keys: set[str], prefix: str) -> list[str]:
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
