# SPDX-License-Identifier: Apache-2.0
"""T09 \u2014 flip between branches; capture reflects the current branch.

Per ``CHATGPT-FULL-COVERAGE.md`` Surface 5 / T09, ``CLAUDE`` C09,
``GEMINI`` G09. After T07 (edit user message) creates a branch tree,
the page renders ``< 1/2 >`` arrows that navigate between the
original branch and the edited branch. PCE must capture the
CURRENTLY-SHOWN branch \u2014 not stale state from the other branch.

Strategy:
  1. Send seed prompt with token A.
  2. Edit the user message to a different prompt with token B
     (creates branch B; original is now branch 1, edited is branch 2).
  3. Click ``branch_prev_selectors[0]`` to flip back to branch 1
     (which shows the token-A user prompt).
  4. Wait for the SW to fingerprint + capture the flipped state.
  5. Verify the latest PCE Core session has user-msg containing
     token A (the flipped-to branch), not B.

Failure modes T09 catches:
  - Branch flip click works but SW doesn't re-fingerprint (the
    fingerprint is keyed only on the latest URL or assistant text;
    branch flip changes neither URL nor latest assistant turn
    content).
  - Branch flip works AND fires a capture but with the WRONG branch
    text (the extractor took stale assistant text from the old
    branch's hidden subtree).

Most accounts won't see branch arrows render in the UI today (the
"current ChatGPT UI" memory notes T09 is UI-blocked); empty
``branch_prev_selectors`` \u2014 SKIP.
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


SEED_PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the seed token "
    "{token}. Reply in one short line."
)
EDITED_PROMPT_TEMPLATE = (
    "Please reply with the literal word 'pong' and the edit token "
    "{token}. Reply in one short line."
)


class T09BranchFlipCase(BaseCase):
    id = "T09"
    name = "branch_flip_currently_shown"
    description = (
        "Edit a user message to create a branch tree; flip back to "
        "the original branch via the prev arrow; verify PCE Core's "
        "latest user message reflects the flipped-to branch and stores "
        "a renderable branch contract."
    )

    def run(
        self,
        probe: ProbeClient,
        pce_core: httpx.Client,
        adapter: BaseProbeSiteAdapter,
    ) -> CaseResult:
        start = self._start_timer()

        if not adapter.edit_button_selectors:
            return CaseResult.skipped(
                self.id, adapter.name,
                "no edit_button_selectors; T09 cannot create a branch tree",
                duration_ms=self._elapsed_ms(start),
            )
        if not adapter.branch_prev_selectors:
            return CaseResult.skipped(
                self.id, adapter.name,
                "no branch_prev_selectors; site UI does not render "
                "branch-flip arrows on this account (per memory: "
                "ChatGPT T09 has been UI-blocked on most accounts)",
                duration_ms=self._elapsed_ms(start),
            )

        seed_token = f"PROBE-{adapter.name.upper()}-T09-A-{uuid.uuid4().hex[:6]}"
        edit_token = f"PROBE-{adapter.name.upper()}-T09-B-{uuid.uuid4().hex[:6]}"
        details: dict = {"seed_token": seed_token, "edit_token": edit_token}

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

        # 2. Seed prompt + wait + capture.
        try:
            adapter.send_prompt(
                probe, tab_id,
                SEED_PROMPT_TEMPLATE.format(token=seed_token),
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"seed send_prompt: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "seed_send"},
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

        # 3. Edit the user message to create branch B.
        if not adapter.click_last_user_edit(probe, tab_id):
            return CaseResult.failed(
                self.id, adapter.name,
                "click_last_user_edit failed",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_click"},
            )
        try:
            input_selector = adapter.resolve_input_selector(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"input not reachable after edit click: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "post_edit_input"},
            )
        try:
            probe.dom.type(
                tab_id, input_selector,
                EDITED_PROMPT_TEMPLATE.format(token=edit_token),
                clear=True, submit=False,
            )
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"type into edit editor: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_type"},
            )
        time.sleep(adapter.type_settle_ms / 1_000)

        save_selectors = list(adapter.send_button_selectors) + [
            'button[aria-label*="Save" i]',
            'button[aria-label*="\u4fdd\u5b58"]',
            'button[type="submit"]',
        ]
        if not adapter.click_first_visible(probe, tab_id, save_selectors):
            try:
                probe.dom.press_key(tab_id, "Enter", selector=input_selector)
            except ProbeError:
                pass
        try:
            adapter.wait_for_done(probe, tab_id)
        except ProbeError as exc:
            return CaseResult.failed(
                self.id, adapter.name,
                f"edit wait_for_done: [{exc.code}] {exc.message}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "edit_wait"},
            )

        # Wait for the edit capture so PCE Core has both branches.
        try:
            probe.capture.wait_for_token(
                edit_token, timeout_ms=15_000,
                provider=adapter.provider, kind="PCE_CAPTURE",
            )
        except (CaptureNotSeenError, ProbeError):
            details["edit_capture_seen"] = False
        else:
            details["edit_capture_seen"] = True

        # 4. Click the prev arrow to flip BACK to branch A.
        flipped = adapter.click_first_visible(
            probe, tab_id, adapter.branch_prev_selectors,
        )
        details["flip_clicked"] = flipped
        if not flipped:
            return CaseResult.skipped(
                self.id, adapter.name,
                "branch_prev_selectors did not click; the prev arrow "
                "is not rendered (branch tree may not have been "
                "created or the UI doesn't expose flip arrows on "
                "this account / model variant)",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "flip_not_clickable"},
            )

        # Give the SW time to re-fingerprint + capture the flipped
        # state. Branch flip doesn't trigger a new request, just a
        # DOM swap, so debounce + dedup must NOT collapse it.
        time.sleep(3.5)

        # 5. Trigger a manual capture as a safety net (the runtime
        #    SHOULD already have refingerprinted, but if it didn't we
        #    want to know whether the manual-capture bridge can save
        #    us; that's a softer regression than the auto-capture
        #    failing).
        try:
            probe.dom.execute_js(
                tab_id,
                "(function () { try { window.dispatchEvent(new CustomEvent('pce-manual-capture')); } catch (e) {} })();",
            )
        except ProbeError:
            pass
        time.sleep(2.0)

        # 6. Read PCE Core sessions to see which branch is current.
        #    We don't have a stable session_hint to query against
        #    (both branches share the same conversation_id), so we
        #    query the most recent session for this provider and
        #    look at the most recent user message.
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
        if not sessions:
            return CaseResult.failed(
                self.id, adapter.name,
                "no sessions for provider",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "no_sessions"},
            )
        # Sort by created_at if available, else take the last entry.
        latest_session = sessions[-1]
        session_id = latest_session.get("id")
        msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
        if msg_resp.status_code != 200:
            return CaseResult.failed(
                self.id, adapter.name,
                f"messages API {msg_resp.status_code}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "messages_status"},
            )
        msgs = msg_resp.json() or []
        details["n_messages"] = len(msgs)
        details["branch_contract"] = self._branch_contract_evidence(msgs)

        user_msgs = [m for m in msgs if m.get("role") == "user"]
        details["latest_user_head"] = (
            (user_msgs[-1].get("content_text") if user_msgs else "") or ""
        )[:160]
        latest_user_text = (
            user_msgs[-1].get("content_text") if user_msgs else ""
        ) or ""

        # Branch flip succeeded if the latest user content reflects
        # the SEED token (we flipped BACK to branch A) rather than
        # the EDIT token. If both tokens appear (full-history capture
        # mode like Claude's), check that seed_token appears in the
        # LAST user message specifically.
        if seed_token in latest_user_text:
            if not details["branch_contract"]["ok"]:
                return CaseResult.failed(
                    self.id, adapter.name,
                    (
                        "branch current-content signal exists, but "
                        "structured branch storage/render contract is "
                        "missing. The new standard requires branch_id/"
                        "parent/current_branch evidence before T09 can PASS."
                    ),
                    duration_ms=self._elapsed_ms(start),
                    details={
                        **details,
                        "session_id": session_id,
                        "phase": "branch_contract_missing",
                    },
                )
            return CaseResult.passed(
                self.id, adapter.name,
                summary=(
                    f"branch flip captured; latest user has seed_token, "
                    f"session_id={session_id}"
                ),
                duration_ms=self._elapsed_ms(start),
                details={**details, "session_id": session_id},
            )
        if edit_token in latest_user_text:
            return CaseResult.failed(
                self.id, adapter.name,
                f"branch flip click succeeded but latest user message "
                f"still reflects the EDITED branch (token B), not the "
                f"flipped-to original (token A). The SW didn't "
                f"re-fingerprint after the DOM swap. head="
                f"{latest_user_text[:120]!r}",
                duration_ms=self._elapsed_ms(start),
                details={**details, "phase": "flip_not_captured"},
            )
        return CaseResult.failed(
            self.id, adapter.name,
            f"latest user message has neither seed nor edit token; "
            f"head={latest_user_text[:120]!r}",
            duration_ms=self._elapsed_ms(start),
            details={**details, "phase": "neither_token_present"},
        )

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
        }
        render_keys = {
            "branch",
            "branches",
            "branch_tree",
            "branch_choices",
            "current_branch",
            "selected_branch",
        }

        storage_paths: list[str] = []
        render_paths: list[str] = []
        for idx, msg in enumerate(messages or []):
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
