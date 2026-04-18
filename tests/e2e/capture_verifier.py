# SPDX-License-Identifier: Apache-2.0
"""Verify that PCE Core actually captured data from browser interactions.

Queries the PCE API to check if captures/sessions arrived after a test
interaction. Used by test_capture.py to confirm end-to-end pipeline works.
"""

import json
import time
import logging
import requests

logger = logging.getLogger("pce.e2e.verifier")

PCE_BASE = "http://127.0.0.1:9800"

# Shared session that bypasses system proxy for localhost calls
_session = requests.Session()
_session.trust_env = False


def pce_is_running() -> bool:
    """Check if PCE Core server is reachable."""
    try:
        r = _session.get(f"{PCE_BASE}/api/v1/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def get_stats() -> dict:
    """Get current capture stats."""
    r = _session.get(f"{PCE_BASE}/api/v1/stats", timeout=5)
    r.raise_for_status()
    return r.json()


def get_recent_captures(last: int = 20, provider: str = None) -> list:
    """Get recent raw captures."""
    params = {"last": last}
    if provider:
        params["provider"] = provider
    r = _session.get(f"{PCE_BASE}/api/v1/captures", params=params, timeout=5)
    r.raise_for_status()
    return r.json()


def get_sessions(last: int = 20, provider: str = None) -> list:
    """Get recent sessions."""
    params = {"last": last}
    if provider:
        params["provider"] = provider
    r = _session.get(f"{PCE_BASE}/api/v1/sessions", params=params, timeout=5)
    r.raise_for_status()
    return r.json()


def get_session_messages(session_id: str) -> list:
    """Get messages for a session."""
    r = _session.get(f"{PCE_BASE}/api/v1/sessions/{session_id}/messages", timeout=5)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()


def extract_attachment_types_by_role(messages: list[dict]) -> dict[str, set[str]]:
    """Return attachment type sets keyed by message role."""
    by_role: dict[str, set[str]] = {}
    for msg in messages or []:
        role = msg.get("role", "unknown")
        by_role.setdefault(role, set())
        raw = msg.get("content_json")
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        for att in data.get("attachments") or []:
            if isinstance(att, dict) and att.get("type"):
                by_role[role].add(att["type"])
    return by_role


def _capture_messages(capture: dict) -> list[dict]:
    """Parse a raw conversation capture body into messages."""
    if not isinstance(capture, dict):
        return []
    body = capture.get("body_text_or_json")
    if not body:
        return []
    try:
        data = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return []
    messages = data.get("messages")
    return messages if isinstance(messages, list) else []


def extract_capture_attachment_types_by_role(capture: dict) -> dict[str, set[str]]:
    """Return attachment type sets keyed by role from a raw conversation capture."""
    by_role: dict[str, set[str]] = {}
    for msg in _capture_messages(capture):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "unknown")
        by_role.setdefault(role, set())
        for att in msg.get("attachments") or []:
            if isinstance(att, dict) and att.get("type"):
                by_role[role].add(att["type"])
    return by_role


def reset_baseline():
    """Clear all data (dev mode) so tests start clean."""
    r = _session.post(f"{PCE_BASE}/api/v1/dev/reset", timeout=5)
    r.raise_for_status()
    return r.json()


def wait_for_new_captures(
    initial_count: int,
    initial_provider_count: int = 0,
    timeout_s: float = 30,
    poll_interval: float = 2,
    min_new: int = 1,
    provider: str = None,
) -> dict:
    """Wait until new captures appear beyond initial_count.

    Returns a dict:
        {
            "success": bool,
            "new_count": int,
            "total": int,
            "captures": [...],
            "sessions": [...],
            "elapsed_s": float,
        }
    """
    start = time.time()
    while True:
        elapsed = time.time() - start
        try:
            stats = get_stats()
            total = stats["total_captures"]
            new = total - initial_count

            # Use exact provider count from stats (not capped list length)
            provider_new = new
            if provider and initial_provider_count > 0:
                current_provider_count = stats.get("by_provider", {}).get(provider, 0)
                provider_new = max(0, current_provider_count - initial_provider_count)

            if provider_new >= min_new:
                captures = get_recent_captures(last=max(provider_new + 5, 10), provider=provider)
                sessions = get_sessions(last=10, provider=provider)
                return {
                    "success": True,
                    "new_count": new,
                    "provider_new_count": provider_new,
                    "total": total,
                    "captures": captures,
                    "sessions": sessions,
                    "elapsed_s": round(elapsed, 1),
                }
        except Exception as e:
            logger.warning("Verifier poll error: %s", e)

        if elapsed >= timeout_s:
            # Final attempt
            try:
                final_stats = get_stats()
                final_total = final_stats["total_captures"]
                final_new = final_total - initial_count
                final_provider_new = final_new
                if provider and initial_provider_count > 0:
                    final_provider_new = max(
                        0,
                        final_stats.get("by_provider", {}).get(provider, 0) - initial_provider_count,
                    )
                captures = get_recent_captures(last=5, provider=provider)
                sessions = get_sessions(last=5, provider=provider)
            except Exception:
                final_total, final_new, final_provider_new = initial_count, 0, 0
                captures, sessions = [], []

            return {
                "success": False,
                "new_count": final_new,
                "provider_new_count": final_provider_new,
                "total": final_total,
                "captures": captures,
                "sessions": sessions,
                "elapsed_s": round(elapsed, 1),
            }

        time.sleep(poll_interval)


def wait_for_session_with_messages(
    provider: str,
    min_messages: int = 2,
    timeout_s: float = 30,
    poll_interval: float = 2,
    required_roles: set[str] | None = None,
) -> dict:
    """Wait for a session with at least N messages for a given provider.

    Returns:
        {
            "success": bool,
            "session": {...} or None,
            "messages": [...],
            "elapsed_s": float,
        }
    """
    start = time.time()
    last_candidate = None
    required_roles = set(required_roles or [])

    while True:
        elapsed = time.time() - start
        try:
            sessions = get_sessions(last=5, provider=provider)
            for sess in sessions:
                msgs = get_session_messages(sess["id"])
                if len(msgs) < min_messages:
                    continue
                roles = {m.get("role") for m in msgs}
                last_candidate = {"session": sess, "messages": msgs}
                if required_roles and not required_roles.issubset(roles):
                    continue
                if len(msgs) >= min_messages:
                    return {
                        "success": True,
                        "session": sess,
                        "messages": msgs,
                        "elapsed_s": round(elapsed, 1),
                    }
        except Exception as e:
            logger.warning("Session poll error: %s", e)

        if elapsed >= timeout_s:
            return {
                "success": False,
                "session": last_candidate["session"] if last_candidate else None,
                "messages": last_candidate["messages"] if last_candidate else [],
                "elapsed_s": round(elapsed, 1),
            }

        time.sleep(poll_interval)


def wait_for_session_matching(
    provider: str,
    *,
    contains_text: str | None = None,
    min_messages: int = 2,
    timeout_s: float = 40,
    poll_interval: float = 2,
    required_roles: set[str] | None = None,
    required_attachment_types: dict[str, set[str]] | None = None,
    started_after: float | None = None,
) -> dict:
    """Wait for a recent session that matches both text and attachment criteria."""
    start = time.time()
    last_candidate = None
    required_roles = set(required_roles or [])
    required_attachment_types = required_attachment_types or {}

    while True:
        elapsed = time.time() - start
        try:
            sessions = get_sessions(last=12, provider=provider)
            for sess in sessions:
                if started_after and sess.get("started_at") and sess["started_at"] < started_after:
                    continue

                msgs = get_session_messages(sess["id"])
                if len(msgs) < min_messages:
                    continue

                roles = {m.get("role") for m in msgs}
                by_role = extract_attachment_types_by_role(msgs)
                last_candidate = {
                    "session": sess,
                    "messages": msgs,
                    "attachment_types_by_role": by_role,
                }

                if required_roles and not required_roles.issubset(roles):
                    continue

                if contains_text:
                    joined = "\n".join((m.get("content_text") or "") for m in msgs)
                    if contains_text not in joined:
                        continue

                ok = True
                for role, types in required_attachment_types.items():
                    present = by_role.get(role, set())
                    if not set(types).issubset(present):
                        ok = False
                        break
                if not ok:
                    continue

                return {
                    "success": True,
                    "session": sess,
                    "messages": msgs,
                    "attachment_types_by_role": by_role,
                    "elapsed_s": round(elapsed, 1),
                }
        except Exception as e:
            logger.warning("Rich session poll error: %s", e)

        if elapsed >= timeout_s:
            return {
                "success": False,
                "session": last_candidate["session"] if last_candidate else None,
                "messages": last_candidate["messages"] if last_candidate else [],
                "attachment_types_by_role": (
                    last_candidate["attachment_types_by_role"] if last_candidate else {}
                ),
                "elapsed_s": round(elapsed, 1),
            }

        time.sleep(poll_interval)


def verify_message_quality(messages: list[dict]) -> dict:
    """Check that messages have meaningful content, not just placeholder/noise.

    Returns:
        {
            "ok": bool,
            "user_count": int,
            "assistant_count": int,
            "empty_content": int,
            "issues": [str, ...],
        }
    """
    issues: list[str] = []
    user_count = 0
    assistant_count = 0
    empty_content = 0

    for msg in messages:
        role = msg.get("role", "unknown")
        text = (msg.get("content_text") or "").strip()
        if role == "user":
            user_count += 1
        elif role == "assistant":
            assistant_count += 1
        elif role not in ("system",):
            issues.append(f"unexpected role: {role}")

        if role in ("user", "assistant") and not text:
            empty_content += 1
            issues.append(f"{role} message has empty content_text")

        if role == "assistant" and text and len(text) < 2:
            issues.append(f"assistant content suspiciously short: {text!r}")

    if user_count == 0:
        issues.append("no user message found")
    if assistant_count == 0:
        issues.append("no assistant message found")

    return {
        "ok": user_count >= 1 and assistant_count >= 1 and empty_content == 0 and not issues,
        "user_count": user_count,
        "assistant_count": assistant_count,
        "empty_content": empty_content,
        "issues": issues,
    }


def verify_rich_content(messages: list[dict]) -> dict:
    """Check whether messages contain any rich content (attachments in content_json).

    Returns:
        {
            "has_rich": bool,
            "total_attachments": int,
            "types_found": set[str],
            "by_role": {role: {type, ...}},
        }
    """
    total = 0
    types_found: set[str] = set()
    by_role = extract_attachment_types_by_role(messages)
    for role_types in by_role.values():
        total += len(role_types)
        types_found.update(role_types)

    return {
        "has_rich": total > 0,
        "total_attachments": total,
        "types_found": types_found,
        "by_role": by_role,
    }


def verify_dashboard_sessions(provider: str = None) -> dict:
    """Quick check that the dashboard API returns renderable session data.

    This is a lightweight proxy for 'render verification' — it confirms the
    API returns the same data the dashboard JS would fetch and render.

    Returns:
        {
            "ok": bool,
            "session_count": int,
            "renderable_sessions": int,
            "issues": [str, ...],
        }
    """
    issues: list[str] = []
    try:
        sessions = get_sessions(last=20, provider=provider)
    except Exception as e:
        return {"ok": False, "session_count": 0, "renderable_sessions": 0,
                "issues": [f"API error: {e}"]}

    renderable = 0
    for sess in sessions:
        sid = sess.get("id")
        if not sid:
            issues.append("session without id")
            continue
        try:
            msgs = get_session_messages(sid)
        except Exception as e:
            issues.append(f"session {sid[:8]}: message fetch error: {e}")
            continue
        if len(msgs) >= 2:
            roles = {m.get("role") for m in msgs}
            if "user" in roles and "assistant" in roles:
                # Check that content_text exists so dashboard can render it
                has_text = all(
                    (m.get("content_text") or "").strip()
                    for m in msgs
                    if m.get("role") in ("user", "assistant")
                )
                if has_text:
                    renderable += 1
                else:
                    issues.append(f"session {sid[:8]}: messages have empty content_text")
            else:
                issues.append(f"session {sid[:8]}: missing user or assistant role")
        else:
            issues.append(f"session {sid[:8]}: only {len(msgs)} messages")

    return {
        "ok": renderable > 0,
        "session_count": len(sessions),
        "renderable_sessions": renderable,
        "issues": issues,
    }


def wait_for_conversation_capture_matching(
    provider: str,
    *,
    contains_text: str | None = None,
    timeout_s: float = 30,
    poll_interval: float = 2,
    required_attachment_types: dict[str, set[str]] | None = None,
    created_after: float | None = None,
) -> dict:
    """Wait for a raw DOM conversation capture matching text and attachment criteria."""
    start = time.time()
    last_candidate = None
    required_attachment_types = required_attachment_types or {}

    while True:
        elapsed = time.time() - start
        try:
            captures = get_recent_captures(last=40, provider=provider)
            for cap in captures:
                if cap.get("direction") != "conversation":
                    continue
                if created_after and cap.get("created_at") and cap["created_at"] < created_after:
                    continue

                messages = _capture_messages(cap)
                if not messages:
                    continue

                joined = "\n".join(
                    str(msg.get("content") or msg.get("content_text") or "")
                    for msg in messages
                    if isinstance(msg, dict)
                )
                by_role = extract_capture_attachment_types_by_role(cap)
                last_candidate = {
                    "capture": cap,
                    "messages": messages,
                    "attachment_types_by_role": by_role,
                }

                if contains_text and contains_text not in joined:
                    continue

                ok = True
                for role, types in required_attachment_types.items():
                    present = by_role.get(role, set())
                    if not set(types).issubset(present):
                        ok = False
                        break
                if not ok:
                    continue

                return {
                    "success": True,
                    "capture": cap,
                    "messages": messages,
                    "attachment_types_by_role": by_role,
                    "elapsed_s": round(elapsed, 1),
                }
        except Exception as e:
            logger.warning("Conversation capture poll error: %s", e)

        if elapsed >= timeout_s:
            return {
                "success": False,
                "capture": last_candidate["capture"] if last_candidate else None,
                "messages": last_candidate["messages"] if last_candidate else [],
                "attachment_types_by_role": (
                    last_candidate["attachment_types_by_role"] if last_candidate else {}
                ),
                "elapsed_s": round(elapsed, 1),
            }

        time.sleep(poll_interval)
