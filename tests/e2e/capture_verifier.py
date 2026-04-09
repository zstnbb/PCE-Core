"""Verify that PCE Core actually captured data from browser interactions.

Queries the PCE API to check if captures/sessions arrived after a test
interaction. Used by test_capture.py to confirm end-to-end pipeline works.
"""

import time
import logging
import requests

logger = logging.getLogger("pce.e2e.verifier")

PCE_BASE = "http://127.0.0.1:9800"


def pce_is_running() -> bool:
    """Check if PCE Core server is reachable."""
    try:
        r = requests.get(f"{PCE_BASE}/api/v1/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def get_stats() -> dict:
    """Get current capture stats."""
    r = requests.get(f"{PCE_BASE}/api/v1/stats", timeout=5)
    r.raise_for_status()
    return r.json()


def get_recent_captures(last: int = 20, provider: str = None) -> list:
    """Get recent raw captures."""
    params = {"last": last}
    if provider:
        params["provider"] = provider
    r = requests.get(f"{PCE_BASE}/api/v1/captures", params=params, timeout=5)
    r.raise_for_status()
    return r.json()


def get_sessions(last: int = 20, provider: str = None) -> list:
    """Get recent sessions."""
    params = {"last": last}
    if provider:
        params["provider"] = provider
    r = requests.get(f"{PCE_BASE}/api/v1/sessions", params=params, timeout=5)
    r.raise_for_status()
    return r.json()


def get_session_messages(session_id: str) -> list:
    """Get messages for a session."""
    r = requests.get(f"{PCE_BASE}/api/v1/sessions/{session_id}/messages", timeout=5)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()


def reset_baseline():
    """Clear all data (dev mode) so tests start clean."""
    r = requests.post(f"{PCE_BASE}/api/v1/dev/reset", timeout=5)
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
            provider_new = new

            captures = []
            if provider:
                captures = get_recent_captures(last=200, provider=provider)
                provider_new = max(0, len(captures) - initial_provider_count)

            if provider_new >= min_new:
                if not captures:
                    captures = get_recent_captures(last=new + 5, provider=provider)
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
                captures = get_recent_captures(last=5, provider=provider)
                sessions = get_sessions(last=5, provider=provider)
            except Exception:
                captures, sessions = [], []

            return {
                "success": False,
                "new_count": 0,
                "provider_new_count": 0,
                "total": initial_count,
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
