# SPDX-License-Identifier: Apache-2.0
"""ChatGPT — T01 ported through the PCE Probe path.

This is the **proof** that the new probe vertical works end-to-end:

    pytest -> pce_probe (Python) -> ws://127.0.0.1:9888 -> PCE extension
    -> chrome.tabs.create -> chatgpt.com tab -> chrome.scripting -> typing
    -> ChatGPT streams reply -> chatgpt.content.ts captures -> PCE_CAPTURE
    -> probe observer matches token -> capture.wait_for_token resolves
    -> pytest queries PCE Core API -> assertion passes.

Skips cleanly when:

  * Chrome with the PCE extension isn't attached
    (``--pce-probe-skip``, or the ``pce_probe`` fixture's attach
    timeout expires);
  * PCE Core isn't running on 127.0.0.1:9800
    (handled at module level by ``conftest.py``);
  * The ChatGPT account on the attached browser is not logged in
    (the prompt textarea won't render — surfaces a
    ``selector_not_found`` with the agent_hint to log in manually).

The test is intentionally narrow: send one prompt, observe one
capture, verify one message round-tripped. Broader coverage (T02
streaming, T08 regenerate, etc.) is out of scope for the proof.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from pce_probe import (
    CaptureNotSeenError,
    ProbeClient,
    SelectorNotFoundError,
)

CHATGPT_URL = "https://chatgpt.com/"
PROMPT_SELECTOR_PRIMARY = "#prompt-textarea"
PROMPT_SELECTOR_FALLBACK = '[contenteditable="true"][data-virtualkeyboard="true"]'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_prompt_selector(probe: ProbeClient, tab_id: int) -> str:
    """Try the primary then the fallback selector. Returns whichever
    matched. Raises ``SelectorNotFoundError`` (with rich DOM excerpt)
    if neither does, so the agent can read the failure and update the
    selector list.
    """
    try:
        probe.dom.wait_for_selector(tab_id, PROMPT_SELECTOR_PRIMARY, timeout_ms=15_000)
        return PROMPT_SELECTOR_PRIMARY
    except SelectorNotFoundError:
        # Try the fallback before surfacing failure.
        probe.dom.wait_for_selector(tab_id, PROMPT_SELECTOR_FALLBACK, timeout_ms=5_000)
        return PROMPT_SELECTOR_FALLBACK


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.pce_probe
def test_chatgpt_t01_send_and_capture(
    pce_probe_fresh: ProbeClient,
    pce_core: httpx.Client,
) -> None:
    """T01 — vanilla send-and-receive on ChatGPT, observed via probe.

    Steps:
        1. Open https://chatgpt.com/ in a fresh tab.
        2. Wait for the prompt textarea (logged-in indicator).
        3. Type a unique-token prompt and submit.
        4. Wait for the PCE capture pipeline to flush a row whose body
           contains the token.
        5. Query the PCE Core sessions API and assert the captured
           session has a message containing the token.
    """
    probe = pce_probe_fresh

    # Sanity: extension actually advertised our verbs.
    hello = probe.extension_hello
    assert hello is not None, "probe attached but no hello payload"
    assert "dom.type" in hello.capabilities
    assert "capture.wait_for_token" in hello.capabilities

    # Unique probe token so the capture observer can match exactly this
    # turn (not a previous run's leftover).
    token = f"PROBE-T01-{int(time.time())}"
    prompt = (
        f"Please reply with the literal word 'hello' and the token "
        f"{token}. Reply in one short line, no formatting."
    )

    # 1. Open ChatGPT in a fresh tab.
    open_result = probe.tab.open(CHATGPT_URL, active=True)
    tab_id = int(open_result["tab_id"])
    probe.tab.wait_for_load(tab_id, timeout_ms=20_000)

    # 2. Wait for the prompt textarea. This is also our login probe:
    #    if the user isn't signed in, the selector never appears.
    try:
        selector = _find_prompt_selector(probe, tab_id)
    except SelectorNotFoundError as exc:
        pytest.skip(
            "ChatGPT prompt textarea not found — most likely the "
            "attached browser session is not logged in. "
            f"agent_hint: {exc.agent_hint!r}",
        )
        return  # pragma: no cover

    # 3. Type the prompt and submit. ``submit=True`` dispatches Enter
    #    after typing so the page sends the request.
    probe.dom.type(tab_id, selector, prompt, clear=True, submit=True)

    # 4. Wait for the capture pipeline to observe a row containing the
    #    token. 60s is generous; if ChatGPT itself takes longer the
    #    test should fail loudly because that's a UX regression worth
    #    investigating, not a probe bug.
    try:
        captured = probe.capture.wait_for_token(
            token,
            timeout_ms=60_000,
            provider="openai",
        )
    except CaptureNotSeenError as exc:
        # The error context already contains last_capture_events;
        # surface it in the failure message so the human-or-agent
        # reading the test output can decide what to investigate.
        ctx = exc.context or {}
        recent = ctx.get("last_capture_events", [])
        recent_summary = ", ".join(
            f"{e.get('kind')}@{e.get('host')}" for e in recent[-5:]
        )
        pytest.fail(
            f"capture.wait_for_token timed out for {token!r}. "
            f"agent_hint: {exc.agent_hint!r}. "
            f"recent events ({len(recent)}): {recent_summary}",
        )

    assert captured.get("session_hint"), "captured event had no session_hint"
    session_hint = str(captured["session_hint"])

    # 5. Verify via PCE Core HTTP API. The session_hint from the
    #    extension is the conversation UUID (chatgpt path /c/<uuid>).
    #    Sessions API is keyed on this.
    sessions_resp = pce_core.get(
        "/api/v1/sessions",
        params={"provider": "openai"},
    )
    assert sessions_resp.status_code == 200, sessions_resp.text
    sessions = sessions_resp.json()
    matching = [
        s
        for s in sessions
        if str(s.get("session_key", "")).find(session_hint) != -1
    ]
    assert matching, (
        f"no PCE Core session matched session_hint={session_hint!r}; "
        f"this means the extension's PCE_CAPTURE arrived at the "
        f"observer (so wait_for_token resolved) but the row didn't "
        f"land in PCE Core's storage. Check ingest path."
    )

    session_id = matching[0]["id"]
    msg_resp = pce_core.get(f"/api/v1/sessions/{session_id}/messages")
    assert msg_resp.status_code == 200, msg_resp.text
    msgs = msg_resp.json()
    assert any(
        token in (m.get("content_text") or "") for m in msgs
    ), (
        f"session {session_id} has {len(msgs)} messages but none contain "
        f"the token {token!r}. content_text values: "
        f"{[(m.get('role'), (m.get('content_text') or '')[:80]) for m in msgs]}"
    )
