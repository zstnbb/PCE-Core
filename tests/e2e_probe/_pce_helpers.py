# SPDX-License-Identifier: Apache-2.0
"""PCE Core verification helpers \u2014 the **ground truth** for cases.

Per the redesign analysis (see ``Docs/testing/PCE-PROBE-AGENT-LOOP.md``),
cases must verify against PCE Core's ``/api/v1/sessions`` API rather than
the SW's debug ring. The SW ring is debug telemetry; PCE Core is what
actually persists the conversation. These helpers centralize that
contract so individual cases stop re-implementing it badly.

Three problems the helpers solve:

  1. **Race between SW and PCE Core ingest**: ``probe.capture.wait_for_token``
     returns when the SW *emits* a capture event, but PCE Core's ingest
     pipeline may take another few hundred ms to write to SQLite. Cases
     that immediately query ``/api/v1/sessions`` can race and miss it.

  2. **Empty ``session_hint``**: legitimate captures (T03 stop-mid-stream
     before URL transitions, T18 temporary chat) have ``session_hint=""``
     because the URL was still ``/`` at fingerprint time. Cases that
     keyed lookup on ``session_hint`` then false-FAIL.

  3. **Wrong field for structured content**: PCE Core stores rich
     content (code blocks, citations, attachments) in
     ``content_json.attachments[]``; ``content_text`` may have the
     markdown fence stripped during normalization. Cases that scan
     ``content_text`` for ``\u0060\u0060\u0060python`` then fail when the
     fence is correctly migrated into a ``code_block`` attachment.

The helpers in this module are deliberately permissive about WHERE the
evidence lives (text vs attachments, hinted session vs latest session)
and strict about WHEN it must arrive (PCE Core's API, within a sane
timeout). That's the right balance for a stability suite.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

import httpx


def wait_for_pce_message(
    pce_core: httpx.Client,
    *,
    provider: str,
    predicate: Callable[[dict, dict], bool],
    timeout_s: float = 45.0,
    poll_s: float = 0.75,
    inspect_last_n_sessions: int = 6,
    session_hint: Optional[str] = None,
) -> tuple[Optional[dict], Optional[dict]]:
    """Poll PCE Core until a message satisfying ``predicate`` exists.

    Args:
      pce_core: ``httpx.Client`` connected to PCE Core (the
        ``pce_core`` fixture from ``conftest.py``).
      provider: PCE provider key, e.g. ``"openai"``.
      predicate: ``(session_dict, message_dict) -> bool``. Return True
        when the message we want is found. The message dict is the
        full row PCE Core returns (id, role, content_text,
        content_json, ts, ...).
      timeout_s: total budget. Default 45 s; should comfortably exceed
        the SW's debounce window (3 s) plus PCE Core's ingest latency
        (typically <1 s).
      poll_s: poll interval. 750 ms is a good balance between
        responsiveness (a fast cell sees results in 750 ms) and load
        (a 45 s wait makes ~60 API calls, well within
        ``/api/v1/sessions``'s capacity).
      inspect_last_n_sessions: how many recent sessions to walk per
        poll. We always inspect the latest few, never the full history,
        to avoid O(N) blowup on long-running suites.
      session_hint: if non-empty, prefer sessions whose ``session_key``
        contains this string (the canonical capture hint). When empty
        / None, just walks the last N sessions (T03 stop-mid-stream
        case, where session_hint may be empty by design).

    Returns:
      ``(session_dict, message_dict)`` if found within budget, else
      ``(None, None)``. Cases use the return value to populate
      ``CaseResult.details`` and decide PASS / FAIL.

    Notes on idempotency:
      The poll is read-only; safe to call from any case at any phase.
      It does NOT mutate the SW capture ring or any extension state.
    """
    deadline = time.time() + timeout_s
    last_seen_session_count = 0
    last_error: Optional[str] = None

    while time.time() < deadline:
        try:
            resp = pce_core.get(
                "/api/v1/sessions",
                params={"provider": provider},
            )
        except httpx.HTTPError as exc:
            # PCE Core unreachable mid-poll \u2014 transient. Save the
            # last error message so the caller can surface it if the
            # full timeout elapses without ever getting a 200.
            last_error = f"sessions HTTP error: {exc!r}"
            time.sleep(poll_s)
            continue
        if resp.status_code != 200:
            last_error = f"sessions API returned {resp.status_code}"
            time.sleep(poll_s)
            continue

        sessions = resp.json() or []
        last_seen_session_count = len(sessions)

        # Determine which sessions to inspect this poll.
        # Priority 1: sessions whose key contains the hint (when
        # session_hint is provided + non-empty).
        # Priority 2: the most recent ``inspect_last_n_sessions``
        # \u2014 covers the empty-session-hint case (T03 stop, T18
        # temporary chat).
        #
        # IMPORTANT: PCE Core's ``/api/v1/sessions`` endpoint returns
        # results sorted ``ORDER BY started_at DESC`` (verified in
        # ``pce_core/db.py:query_sessions``). The NEWEST session is
        # therefore at index 0; the OLDEST at index -1. Earlier this
        # helper used ``sessions[-N:]`` to fetch "recent" sessions,
        # which actually fetched the OLDEST N \u2014 the exact
        # opposite of what we want for T03 / T18 / fresh-cell lookup.
        # Use ``sessions[:N]`` instead to slice from the newest end.
        candidates: list[dict] = []
        if session_hint:
            for s in sessions:
                if str(s.get("session_key", "")).find(str(session_hint)) != -1:
                    candidates.append(s)
        if not candidates:
            candidates = list(sessions[:inspect_last_n_sessions])

        for sess in candidates:
            sid = sess.get("id")
            if not sid:
                continue
            try:
                mr = pce_core.get(f"/api/v1/sessions/{sid}/messages")
            except httpx.HTTPError as exc:
                last_error = f"messages HTTP error sid={sid}: {exc!r}"
                continue
            if mr.status_code != 200:
                last_error = f"messages status {mr.status_code} sid={sid}"
                continue
            msgs = mr.json() or []
            for m in msgs:
                try:
                    if predicate(sess, m):
                        return sess, m
                except Exception as exc:
                    # Predicate raised \u2014 don't crash the poll;
                    # treat as "this message doesn't match" and keep
                    # going. The case can fail explicitly afterwards.
                    last_error = f"predicate raised: {exc!r}"
                    continue
        time.sleep(poll_s)

    # Timed out. Return None so the caller can FAIL with rich detail
    # using the diagnostic info it already has.
    _ = last_error  # kept for debugger; suppress unused-warning
    _ = last_seen_session_count
    return None, None


def predicate_token_in_assistant(token: str) -> Callable[[dict, dict], bool]:
    """Common predicate: assistant message whose ``content_text`` or
    ``content_json`` contains ``token``. Most cases use this shape.

    Looks at BOTH ``content_text`` (the plain markdown column) AND
    serialized ``content_json`` so a model that placed the token
    inside a code-block attachment (rather than the prose around it)
    still qualifies. Order matters: text first, then JSON, since
    ``content_text`` is cheap to scan.
    """

    def _check(_session: dict, message: dict) -> bool:
        if message.get("role") != "assistant":
            return False
        text = message.get("content_text") or ""
        if isinstance(text, str) and token in text:
            return True
        cj = message.get("content_json")
        if isinstance(cj, str) and token in cj:
            return True
        return False

    return _check


def predicate_token_in_any_role(token: str) -> Callable[[dict, dict], bool]:
    """Permissive predicate: token appears in any role's content. Useful
    for T03 stop-mid-stream where the partial may have cut off before
    the token rendered in the assistant turn but the token is still
    present in the user's prompt within the captured session.
    """

    def _check(_session: dict, message: dict) -> bool:
        text = message.get("content_text") or ""
        if isinstance(text, str) and token in text:
            return True
        cj = message.get("content_json")
        if isinstance(cj, str) and token in cj:
            return True
        return False

    return _check


def extract_attachments(message: dict) -> list[dict]:
    """Return the message's attachments as a list (always a list,
    possibly empty). Handles three storage shapes PCE Core emits:

      1. ``content_json`` is a JSON string with ``{"attachments": [...]}``
      2. ``content_json`` is already a parsed dict.
      3. ``content_json`` is missing or non-dict \u2014 returns ``[]``.

    Per ``pce_core/rich_content.py:_ATTACHMENT_BLOCK_TYPES``, the
    canonical attachment ``type`` values include ``image_url``,
    ``image_generation``, ``code_block``, ``code_output``,
    ``tool_call``, ``tool_result``, etc. Cases that need a specific
    attachment type filter the returned list themselves; this helper
    just unwraps the storage envelope.
    """
    cj = message.get("content_json")
    if isinstance(cj, str):
        try:
            cj = json.loads(cj)
        except (TypeError, ValueError):
            return []
    if not isinstance(cj, dict):
        return []
    attachments = cj.get("attachments")
    if isinstance(attachments, list):
        return [a for a in attachments if isinstance(a, dict)]
    return []


def has_attachment_of_type(message: dict, *types: str) -> bool:
    """Return True iff ``message`` has any attachment whose ``type``
    field matches one of ``types`` (case-insensitive).
    """
    wanted = {t.lower() for t in types}
    for att in extract_attachments(message):
        att_type = str(att.get("type") or "").lower()
        if att_type in wanted:
            return True
    return False
