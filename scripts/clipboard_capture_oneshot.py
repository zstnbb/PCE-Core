# SPDX-License-Identifier: Apache-2.0
"""One-shot clipboard read + AI detect + insert.

**Classification**: V-AUX (auxiliary evidence) per
``Docs/stability/REDUNDANCY-AUDIT-MATRIX.md`` section 1.1 amendment
adopted 2026-05-15. Clipboard captures are user-driven (each capture
needs the user to actively select + Ctrl+C), so they do NOT satisfy
the "passive / automatic / full-conversation" semantic that the >=3
V-GREEN invariant requires. Use this tool for:

- Supplementary signal alongside the passive legs (L1 / L3a / L3g
  / L3d CDP / L3f MCP) for human spot-checking
- Cross-leg monitoring (if L1 caught it but clipboard didn't fire,
  user didn't bother copying; if clipboard fired but L1 missed it,
  the passive leg has a gap)

Do NOT use this tool to claim V-GREEN status for a scenario's third
leg -- W4-T3 and W4-T5 in the redundancy sprint were re-pointed to
**L3d CDP launcher** as the actual third passive leg.

Bypasses the threaded ``ClipboardMonitor`` (which segfaults on Windows
from non-main threads in some envs) by reading the clipboard from the
main thread via a PowerShell subprocess.

Usage::

    python scripts/clipboard_capture_oneshot.py <tag>

where ``<tag>`` identifies the source surface (e.g. ``gemini``,
``gas``, ``grok``, ``windsurf``) and is stored in
``meta_json.subsystem`` for downstream filtering.

Exit codes:
- 0 capture inserted (as V-AUX, not V-GREEN)
- 1 clipboard read failed
- 2 clipboard empty / too short
- 3 not AI conversation (skipped)
"""
import json
import subprocess
import sys
from pathlib import Path

# Make pce_core importable when running this file directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5, encoding="utf-8",
        )
        text = (result.stdout or "").rstrip("\r\n")
    except Exception as e:
        print(f"clipboard read failed: {e}", file=sys.stderr)
        return 1

    if not text or len(text) < 20:
        print(f"clipboard empty or too short: {len(text) if text else 0} chars")
        return 2

    print(f"clipboard length: {len(text)} chars")
    print(f"preview: {text[:120]!r}")

    from pce_core.clipboard_monitor import detect_ai_conversation, parse_conversation
    from pce_core.db import SOURCE_CLIPBOARD_MONITOR, insert_capture, new_pair_id

    is_ai, reason, confidence = detect_ai_conversation(text)
    print(f"is_ai={is_ai} reason={reason!r} confidence={confidence:.2f}")

    if not is_ai:
        print("Not detected as AI conversation. Skipping capture.")
        return 3

    messages = parse_conversation(text)
    tag = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    body = json.dumps({
        "messages": messages,
        "total_messages": len(messages),
        "raw_text_length": len(text),
        "detection_reason": reason,
        "confidence": confidence,
        "raw_text_preview": text[:500],
    }, ensure_ascii=False)
    pair_id = new_pair_id()
    capture_id = insert_capture(
        direction="clipboard",
        pair_id=pair_id,
        host="clipboard",
        path=f"/{tag}",
        method="",
        provider="clipboard",
        body_text_or_json=body,
        body_format="json",
        source_id=SOURCE_CLIPBOARD_MONITOR,
        meta_json=json.dumps({
            "capture_source": "clipboard_oneshot",
            "confidence": confidence,
            "reason": reason,
            "message_count": len(messages),
            "subsystem": tag,
            "ai_signal_score": confidence,
            # P5.D.1 V-AUX classification (2026-05-15) — this row is
            # auxiliary evidence, NOT a leg toward the >=3 V-GREEN
            # invariant. See REDUNDANCY-AUDIT-MATRIX.md section 1.1.
            "evidence_tier": "V-AUX",
            "redundancy_leg": False,
        }, ensure_ascii=False),
    )
    print(f"capture inserted id={capture_id} pair_id={pair_id} "
          f"source_id={SOURCE_CLIPBOARD_MONITOR} tag={tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
