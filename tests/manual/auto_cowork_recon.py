# SPDX-License-Identifier: Apache-2.0
"""P5.B.5 · fully-automated cowork-region RECON for Claude Desktop.

Sibling to :mod:`tests.manual.recon_claude_desktop_cowork` which is the
**REPL-driven** version. This module is the **scripted** version: the
operator only needs to ensure pre-flight is up (Claude Desktop logged in
on Cowork surface, mitmdump on :8080, pce_persistence_watcher running,
pce_core daemon up) and run::

    python -m tests.manual.auto_cowork_recon

After ~12 minutes wall clock, the script writes
``tests/manual/recon_cowork_auto_<ts>/`` containing the same artefact
shape as the REPL recon (events.jsonl, markers.jsonl, manifests/,
summary.json, findings_skeleton.md) plus per-step UIA dumps and a
``step_results.json`` summarising what each step empirically observed.

The 9 sequential steps and the open question each closes:

* ``baseline``         — full UIA dump of current surface (top-level shape).
* ``q5_manifest``      — copy the latest local-agent-mode-sessions
                         manifest as a baseline before any new task fires
                         (closes Q5 schema).
* ``open_cowork``      — heuristic click on a Cowork / Agents sidebar
                         entry (records whether the surface is even
                         reachable by name and what the post-click UIA
                         looks like).
* ``q1_skill_picker``  — focus composer, send a ``/`` keystroke, dump
                         the resulting popup / inline widget (closes
                         Q1 — descendant vs cross-window).
* ``q2_multistep``     — send a multi-step task prompt; count the number
                         of ``/completion`` request pairs that fire
                         within 4 min; record their relative timing.
                         Single-stream vs SSE-per-step vs long-poll
                         signature falls straight out of the delta
                         count + body_format mix (closes Q2).
* ``q7_artifact``      — send a Live Artifact prompt (SVG bar chart),
                         dump UIA + L3g manifest after completion to
                         answer where artifact bytes live (closes Q7).
* ``q6_scheduled``     — send a scheduled-task prompt; dump UIA + L3g
                         manifest. Empirically distinguishes the eager
                         vs lazy lifecycle by whether a conversation
                         row appears at create time (closes Q6 eager
                         lean, pre-scheduled-time).
* ``q8_folder_picker`` — send a folder-access prompt; watch for a new
                         top-level window to appear and dump its
                         descendants. The window class_name distinguishes
                         native Win32 picker (``#32770``) from in-app
                         Chromium dialog (closes Q8).
* ``q3_dispatch``      — heuristic click on a Dispatch (Beta) sidebar
                         entry; dump post-click UIA. Distinguishes
                         in-app pane vs top-level popup (closes Q3).

Each step is wrapped in try/except so a single failure does not abort
the run. Failures land in ``step_results.json`` with their traceback,
and a corresponding ``step-error-<name>`` marker is recorded in the
events timeline.

The script does NOT post to PCE Core and does NOT write to
``raw_captures`` — it only READS the DB (mirrors the REPL recon).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


logger = logging.getLogger("pce.recon.cowork.auto")


# ---------------------------------------------------------------------------
# Reuse infrastructure from the REPL recon module
# ---------------------------------------------------------------------------

from tests.manual.recon_claude_desktop_cowork import (  # noqa: E402
    COWORK_URL_PATTERNS,
    DbTailer,
    ReconStats,
    ReconWriter,
    _default_db_path,
    _resolve_agent_sessions_root,
    _write_findings_skeleton,
)
from tests.e2e_desktop_ui.drivers.claude_desktop import ClaudeDesktopDriver  # noqa: E402
from tests.e2e_desktop_ui.utils import (  # noqa: E402
    baseline_ts,
    configure_utf8_stdout,
    count_completions,
    latest_completion_pair_id,
    wait_completion_response,
)


# ---------------------------------------------------------------------------
# Context object passed to every step
# ---------------------------------------------------------------------------


@dataclass
class StepCtx:
    driver: ClaudeDesktopDriver
    writer: ReconWriter
    stats: ReconStats
    ag_root: Optional[Path]
    out_dir: Path
    step_results: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers (in-script — not added to driver.py until P5.B.5.2)
# ---------------------------------------------------------------------------


def _mark(ctx: StepCtx, label: str, extra: Optional[dict] = None) -> None:
    rec: dict = {"ts": time.time(), "kind": "marker", "label": label}
    if extra is not None:
        rec["extra"] = extra
    ctx.writer.write_marker(rec)
    ctx.stats.markers += 1
    print(f"[auto] mark {label}")
    sys.stdout.flush()


def _note(ctx: StepCtx, text: str) -> None:
    ctx.writer.write_marker(
        {"ts": time.time(), "kind": "note", "text": text}
    )


def _dump_uia(
    ctx: StepCtx,
    label: str,
    keywords: Optional[Iterable[str]] = None,
) -> int:
    """Dump the Claude main window's UIA tree (optionally keyword-filtered)
    to ``_uia_<label>.txt`` and return the row count.
    """
    try:
        rows = ctx.driver.dump_tree(
            keywords=list(keywords) if keywords else None,
        )
    except Exception as exc:
        logger.warning("_dump_uia %s: dump_tree failed: %s", label, exc)
        rows = []
    out_path = ctx.out_dir / f"_uia_{label}.txt"
    lines = []
    for r in rows:
        try:
            lines.append("\t".join(str(c) for c in r))
        except Exception:
            continue
    out_path.write_text("\n".join(lines), encoding="utf-8", errors="replace")
    return len(rows)


def _try_click_uia(
    ctx: StepCtx,
    name_substrings: Iterable[str],
    *,
    control_types: Optional[Iterable[str]] = None,
    timeout: float = 2.0,
) -> tuple[bool, Optional[dict]]:
    """Heuristically locate a UIA element by name substring and click it.

    Returns ``(clicked, info_dict)``. ``info_dict`` always carries
    ``element`` (whether one was found) and either ``err`` (on failure)
    or ``hit`` (name/aid/control_type of the element clicked).
    """
    try:
        el = ctx.driver._find_uia_by_name_substr(  # noqa: SLF001 — RECON
            name_substrings,
            control_types=control_types,
            timeout=timeout,
        )
    except Exception as exc:
        return False, {"element": False, "err": f"find raised: {exc}"}
    if el is None:
        return False, {"element": False, "err": "no element matched"}

    info: dict = {"element": True}
    try:
        info["hit"] = {
            "name": (getattr(el, "window_text", lambda: "")() or "")[:200],
            "aid": getattr(el.element_info, "automation_id", "") or "",
            "ct": getattr(el.element_info, "control_type", "") or "",
            "class": getattr(el.element_info, "class_name", "") or "",
        }
    except Exception:
        info["hit"] = {"name": "?", "aid": "?", "ct": "?", "class": "?"}

    try:
        el.click_input()
        info["clicked"] = True
        return True, info
    except Exception as exc:
        info["clicked"] = False
        info["err"] = f"click raised: {exc}"
        return False, info


def _snap_manifest(ctx: StepCtx, label: str) -> Optional[dict]:
    """Snapshot the most-recently-modified ``<uuid>/manifest.json`` under
    ``ag_root`` to ``manifests/<label>__<uuid>.json``. Returns a small
    metadata dict (path, uuid, size, mtime age in seconds), or None if
    no manifest is found.
    """
    if ctx.ag_root is None or not ctx.ag_root.exists():
        return None
    candidates: list[tuple[float, Path, str]] = []
    try:
        for child in ctx.ag_root.iterdir():
            if not child.is_dir():
                continue
            man = child / "manifest.json"
            if not man.exists():
                continue
            try:
                candidates.append((man.stat().st_mtime, man, child.name))
            except OSError:
                continue
    except OSError as exc:
        logger.warning("_snap_manifest %s: iterdir failed: %s", label, exc)
        return None
    if not candidates:
        return None
    candidates.sort(reverse=True)
    mtime, manifest, uuid = candidates[0]
    try:
        body = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("_snap_manifest %s: read failed: %s", label, exc)
        return None
    dest = ctx.writer.manifests_dir / f"{label}__{uuid}.json"
    dest.write_text(body, encoding="utf-8", errors="replace")
    ctx.stats.manifests_dumped += 1
    return {
        "path": str(dest),
        "uuid": uuid,
        "bytes": len(body),
        "mtime_age_s": round(time.time() - mtime, 1),
    }


def _enum_top_windows() -> list[dict]:
    """Return a list of dicts describing each currently-visible top-level
    desktop window (used as the diff baseline for native-dialog detection).
    """
    out: list[dict] = []
    try:
        from pywinauto import Desktop  # type: ignore
        try:
            wins = Desktop(backend="uia").windows()
        except Exception:
            wins = []
        for w in wins:
            try:
                out.append({
                    "hwnd": getattr(w, "handle", None),
                    "title": (getattr(w, "window_text", lambda: "")() or "")[:200],
                    "class": getattr(w.element_info, "class_name", "") or "",
                })
            except Exception:
                continue
    except Exception:
        pass
    return out


def _poll_new_window(prev_hwnds: set, *, timeout: float) -> Optional[Any]:
    """Poll the desktop for a new top-level window not in ``prev_hwnds``.
    Returns the wrapper of the first such window discovered, or None.
    """
    try:
        from pywinauto import Desktop  # type: ignore
    except Exception:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for w in Desktop(backend="uia").windows():
                try:
                    h = getattr(w, "handle", None)
                except Exception:
                    continue
                if h is None or h in prev_hwnds:
                    continue
                # Skip empty / invisible shells
                try:
                    title = w.window_text() or ""
                except Exception:
                    title = ""
                try:
                    cls = w.element_info.class_name or ""
                except Exception:
                    cls = ""
                if not title and not cls:
                    continue
                return w
        except Exception:
            pass
        time.sleep(0.4)
    return None


def _dump_window_descendants(
    window: Any,
    out_path: Path,
    *,
    max_rows: int = 250,
) -> int:
    """Dump descendants of an arbitrary window (used for the native
    folder-picker tree). Returns the number of rows written.
    """
    rows: list[dict] = []
    try:
        descendants = window.descendants()
    except Exception:
        descendants = []
    for el in descendants:
        try:
            rows.append({
                "ct": (getattr(el.element_info, "control_type", "") or ""),
                "name": (getattr(el.element_info, "name", "") or "")[:200],
                "aid": (getattr(el.element_info, "automation_id", "") or ""),
                "class": (getattr(el.element_info, "class_name", "") or ""),
            })
        except Exception:
            continue
        if len(rows) >= max_rows:
            break
    try:
        header = {
            "window_title": (getattr(window, "window_text", lambda: "")() or "")[:200],
            "window_class": getattr(window.element_info, "class_name", "") or "",
            "window_hwnd": getattr(window, "handle", None),
        }
    except Exception:
        header = {}
    out_path.write_text(
        json.dumps({"window": header, "descendants": rows}, indent=2, default=str),
        encoding="utf-8",
        errors="replace",
    )
    return len(rows)


def _send_keys(seq: str, *, pause: float = 0.05) -> None:
    """Wrapper for pywinauto.keyboard.send_keys with a small post-pause."""
    try:
        from pywinauto.keyboard import send_keys  # type: ignore
    except Exception:
        return
    send_keys(seq, pause=pause)
    time.sleep(max(0.0, pause))


# ---------------------------------------------------------------------------
# Steps — each returns a JSON-serialisable dict describing what it saw
# ---------------------------------------------------------------------------


def step_baseline(ctx: StepCtx) -> dict:
    ctx.driver.focus()
    _mark(ctx, "auto-start")
    n_full = _dump_uia(ctx, "baseline-full", keywords=None)
    n_keyed = _dump_uia(
        ctx,
        "baseline-keyed",
        keywords=[
            "cowork", "skill", "dispatch", "scheduled", "artifact", "task",
            "agent", "live", "customize",
        ],
    )
    windows = _enum_top_windows()
    return {
        "uia_full_rows": n_full,
        "uia_keyed_rows": n_keyed,
        "top_windows": len(windows),
        "claude_windows": [
            w for w in windows
            if "claude" in (w.get("title") or "").lower()
        ],
    }


def step_q5_manifest(ctx: StepCtx) -> dict:
    _mark(ctx, "q5-manifest-baseline")
    snap = _snap_manifest(ctx, "baseline")
    if snap is None:
        _note(ctx, "q5: no agent_sessions manifest found at baseline")
    return {"baseline_manifest": snap}


def step_open_cowork(ctx: StepCtx) -> dict:
    _mark(ctx, "open-cowork-try")
    clicked, info = _try_click_uia(
        ctx,
        ["Cowork", "Agents", "Agent Mode", "Agent mode"],
        control_types=[
            "Button", "ListItem", "TabItem", "Custom", "TreeItem",
            "Hyperlink", "Text", "MenuItem",
        ],
        timeout=2.5,
    )
    if clicked:
        time.sleep(2.0)
        _dump_uia(
            ctx,
            "cowork-tab",
            keywords=[
                "cowork", "skill", "dispatch", "scheduled", "artifact",
                "task", "agent", "live", "customize", "send",
            ],
        )
    _mark(ctx, "open-cowork-done", extra={"clicked": clicked, **(info or {})})
    return {"clicked": clicked, **(info or {})}


def step_q1_skill_picker(ctx: StepCtx) -> dict:
    _mark(ctx, "q1-skill-picker-start")
    try:
        ctx.driver.focus()
        ctx.driver.click_composer()
    except Exception as exc:
        _mark(ctx, "q1-error", extra={"err": str(exc)})
        return {"error": str(exc), "stage": "focus"}
    # Capture top-level window count before pressing "/"
    prev_windows = {w["hwnd"] for w in _enum_top_windows() if w.get("hwnd")}
    _send_keys("/", pause=0.1)
    time.sleep(1.8)  # let the picker render
    new_win = _poll_new_window(prev_windows, timeout=1.0)
    n_keyed = _dump_uia(
        ctx,
        "q1-skill-picker",
        keywords=[
            "xlsx", "pdf", "pptx", "docx", "skill", "consolidate",
            "schedule", "setup-cowork", "skill-creator", "memory",
        ],
    )
    n_full = _dump_uia(ctx, "q1-skill-picker-full", keywords=None)
    new_win_info: Optional[dict] = None
    if new_win is not None:
        try:
            picker_path = ctx.out_dir / "_uia_q1-skill-picker-newwindow.json"
            n_rows = _dump_window_descendants(new_win, picker_path)
            new_win_info = {
                "title": (new_win.window_text() or "")[:200],
                "class": new_win.element_info.class_name or "",
                "hwnd": getattr(new_win, "handle", None),
                "descendant_rows": n_rows,
                "dump_path": str(picker_path),
            }
        except Exception as exc:
            new_win_info = {"err": str(exc)}
    # Dismiss picker
    _send_keys("{ESC}", pause=0.1)
    time.sleep(0.4)
    _mark(
        ctx, "q1-skill-picker-end",
        extra={"keyed_rows": n_keyed, "full_rows": n_full, "new_window": new_win_info},
    )
    return {
        "keyed_rows": n_keyed,
        "full_rows": n_full,
        "new_window": new_win_info,
        "scope": "descendant" if new_win is None else "cross-window",
    }


def step_q2_multistep(ctx: StepCtx) -> dict:
    _mark(ctx, "q2-multistep-start")
    base_ts = baseline_ts()
    n0 = count_completions(base_ts - 1.0)
    pid_send: Optional[str] = None
    try:
        pid_send = ctx.driver.send_message(
            "Please complete this multi-step task for me, doing each step "
            "with a separate reasoning round: (step 1) name 3 file types "
            "commonly found in a typical Downloads folder, then (step 2) "
            "for each, write a 2-line markdown summary of why it's there. "
            "Use any tools you'd normally use.",
            wait_done=True,
            wait_timeout=180.0,
        )
    except Exception as exc:
        _mark(ctx, "q2-error", extra={"err": str(exc), "stage": "send"})
        return {"error": str(exc), "stage": "send"}

    _mark(ctx, "q2-first-pair", extra={"pair_id": pid_send})

    # Drain any further /completion fires for 60s — multi-step would
    # surface them here.
    drain_start = time.time()
    drain_pairs: list[str] = []
    last_pid_seen: Optional[str] = pid_send
    while time.time() - drain_start < 60.0:
        new_pid = latest_completion_pair_id(base_ts - 1.0)
        if new_pid and new_pid != last_pid_seen:
            drain_pairs.append(new_pid)
            last_pid_seen = new_pid
            _mark(ctx, "q2-additional-pair", extra={"pair_id": new_pid})
        time.sleep(2.0)

    final_n = count_completions(base_ts - 1.0)
    delta = final_n - n0

    n_keyed = _dump_uia(
        ctx, "q2-end",
        keywords=[
            "thinking", "step", "tool", "result", "code", "skill",
        ],
    )
    snap = _snap_manifest(ctx, "q2-multistep")

    # Signature classification
    if delta <= 0:
        sig = "no-completion-observed"
    elif delta == 1:
        sig = "single-stream"
    else:
        sig = "sse-per-step-or-multi"

    _mark(
        ctx, "q2-multistep-end",
        extra={
            "n_completions_delta": delta,
            "first_pair_id": pid_send,
            "additional_pairs": drain_pairs,
            "signature": sig,
            "manifest": snap,
        },
    )
    return {
        "n_completions_delta": delta,
        "first_pair_id": pid_send,
        "additional_pairs": drain_pairs,
        "signature": sig,
        "uia_keyed_rows": n_keyed,
        "manifest": snap,
    }


def step_q7_artifact(ctx: StepCtx) -> dict:
    _mark(ctx, "q7-artifact-start")
    base_ts = baseline_ts()
    n0 = count_completions(base_ts - 1.0)
    try:
        pid = ctx.driver.send_message(
            "Please create a Live Artifact for me: an SVG 200x200 bar "
            "chart with three bars labeled A, B, C with values 10, 20, 30. "
            "Use the artifacts / visualize tooling so I can see it render. "
            "After it's rendered, tell me where you stored it.",
            wait_done=True,
            wait_timeout=150.0,
        )
    except Exception as exc:
        _mark(ctx, "q7-error", extra={"err": str(exc), "stage": "send"})
        return {"error": str(exc), "stage": "send"}
    time.sleep(20.0)  # let any pane render
    n_keyed = _dump_uia(
        ctx, "q7-end",
        keywords=[
            "artifact", "svg", "chart", "view", "preview", "live",
            "render", "download",
        ],
    )
    snap = _snap_manifest(ctx, "q7-artifact")
    delta = count_completions(base_ts - 1.0) - n0
    _mark(
        ctx, "q7-artifact-end",
        extra={"pair_id": pid, "n_completions_delta": delta, "manifest": snap},
    )
    return {
        "pair_id": pid,
        "n_completions_delta": delta,
        "uia_keyed_rows": n_keyed,
        "manifest": snap,
    }


def step_q6_scheduled(ctx: StepCtx) -> dict:
    _mark(ctx, "q6-scheduled-start")
    base_ts = baseline_ts()
    n0 = count_completions(base_ts - 1.0)
    try:
        pid = ctx.driver.send_message(
            "Please schedule a recurring task: every weekday at 9 AM, send "
            "me a one-sentence friendly greeting. Use the scheduled-tasks "
            "tool. Once it's scheduled, tell me its task id and the cron "
            "/ recurrence expression you used.",
            wait_done=True,
            wait_timeout=150.0,
        )
    except Exception as exc:
        _mark(ctx, "q6-error", extra={"err": str(exc), "stage": "send"})
        return {"error": str(exc), "stage": "send"}
    time.sleep(10.0)
    n_keyed = _dump_uia(
        ctx, "q6-end",
        keywords=[
            "scheduled", "schedule", "cron", "task", "weekday", "recurring",
            "9 AM", "9:00",
        ],
    )
    snap = _snap_manifest(ctx, "q6-scheduled")
    delta = count_completions(base_ts - 1.0) - n0
    _mark(
        ctx, "q6-scheduled-end",
        extra={"pair_id": pid, "n_completions_delta": delta, "manifest": snap},
    )
    return {
        "pair_id": pid,
        "n_completions_delta": delta,
        "uia_keyed_rows": n_keyed,
        "manifest": snap,
        "lifecycle_signal": (
            "eager" if (snap and snap.get("mtime_age_s", 9999) < 60)
            else "lazy-or-unverified"
        ),
    }


def step_q8_folder_picker(ctx: StepCtx) -> dict:
    _mark(ctx, "q8-folder-picker-start")
    prev_windows = {w["hwnd"] for w in _enum_top_windows() if w.get("hwnd")}
    base_ts = baseline_ts()
    try:
        pid = ctx.driver.send_message(
            "Please request access to my Downloads folder so you can "
            "read files from it. Use the request_cowork_directory tool "
            "(or whatever folder-access tool is available) and trigger "
            "the system folder picker if needed.",
            wait_done=False,
            wait_timeout=60.0,
        )
    except Exception as exc:
        _mark(ctx, "q8-error", extra={"err": str(exc), "stage": "send"})
        return {"error": str(exc), "stage": "send"}
    new_win = _poll_new_window(prev_windows, timeout=30.0)
    window_info: Optional[dict] = None
    if new_win is not None:
        try:
            picker_path = ctx.out_dir / "_uia_q8-folder-picker.json"
            n_rows = _dump_window_descendants(new_win, picker_path)
            window_info = {
                "title": (new_win.window_text() or "")[:200],
                "class": new_win.element_info.class_name or "",
                "hwnd": getattr(new_win, "handle", None),
                "descendant_rows": n_rows,
                "dump_path": str(picker_path),
            }
            # Classify
            cls = (window_info["class"] or "").lower()
            if cls == "#32770":
                window_info["category"] = "win32-common-dialog"
            elif "shell" in cls or "browse" in cls:
                window_info["category"] = "shell-folder-picker"
            elif cls.startswith("chrome"):
                window_info["category"] = "chromium-in-app-dialog"
            else:
                window_info["category"] = "unknown"
        except Exception as exc:
            window_info = {"err": str(exc)}
        finally:
            _send_keys("{ESC}", pause=0.2)
            time.sleep(0.6)
            # Some pickers eat the first ESC; send a second just in case
            _send_keys("{ESC}", pause=0.1)
            time.sleep(0.3)
    # Allow the completion to settle if it's still in-flight
    if pid:
        wait_completion_response(pid, timeout=90.0)
    _mark(
        ctx, "q8-folder-picker-end",
        extra={"window": window_info, "pair_id": pid},
    )
    return {"window": window_info, "pair_id": pid}


def step_q3_dispatch(ctx: StepCtx) -> dict:
    _mark(ctx, "q3-dispatch-start")
    try:
        ctx.driver.focus()
    except Exception:
        pass
    prev_windows = {w["hwnd"] for w in _enum_top_windows() if w.get("hwnd")}
    clicked, info = _try_click_uia(
        ctx, ["Dispatch"],
        control_types=[
            "Button", "ListItem", "TabItem", "Custom", "TreeItem",
            "Hyperlink", "Text", "MenuItem",
        ],
        timeout=2.5,
    )
    new_win_info: Optional[dict] = None
    if clicked:
        time.sleep(2.0)
        new_win = _poll_new_window(prev_windows, timeout=2.0)
        if new_win is not None:
            try:
                new_win_info = {
                    "title": (new_win.window_text() or "")[:200],
                    "class": new_win.element_info.class_name or "",
                    "hwnd": getattr(new_win, "handle", None),
                }
            except Exception:
                new_win_info = None
        _dump_uia(
            ctx, "q3-dispatch",
            keywords=["dispatch", "queue", "concurrent", "task"],
        )
    _mark(
        ctx, "q3-dispatch-end",
        extra={"clicked": clicked, "new_window": new_win_info, **(info or {})},
    )
    return {
        "clicked": clicked,
        "info": info,
        "new_window": new_win_info,
        "shape": (
            "separate-popup" if new_win_info
            else ("in-app-pane" if clicked else "not-found")
        ),
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


STEPS: list[tuple[str, Any]] = [
    ("baseline", step_baseline),
    ("q5_manifest", step_q5_manifest),
    ("open_cowork", step_open_cowork),
    ("q1_skill_picker", step_q1_skill_picker),
    ("q2_multistep", step_q2_multistep),
    ("q7_artifact", step_q7_artifact),
    ("q6_scheduled", step_q6_scheduled),
    ("q8_folder_picker", step_q8_folder_picker),
    ("q3_dispatch", step_q3_dispatch),
]


def main(argv: Optional[list[str]] = None) -> int:
    configure_utf8_stdout()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="P5.B.5 fully-automated cowork-region RECON",
    )
    parser.add_argument(
        "--steps",
        default="",
        help=(
            "Comma-separated step names to run (default: all). "
            "Use --list-steps to see available step names."
        ),
    )
    parser.add_argument(
        "--list-steps", action="store_true",
        help="List available step names and exit.",
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="PCE DB path (default: ~/.pce/data/pce.db)",
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("tests/manual"),
        help="Where to write recon_cowork_auto_<ts>/",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=0.5,
        help="DB tailer poll interval in seconds (default 0.5)",
    )
    args = parser.parse_args(argv)

    if args.list_steps:
        for name, fn in STEPS:
            doc = (fn.__doc__ or "").strip().splitlines()[0:1]
            head = doc[0] if doc else ""
            print(f"  {name:20s}  {head}")
        return 0

    db_path = args.db_path or _default_db_path()
    if not db_path.exists():
        print(
            f"[auto] error: DB not found at {db_path}. "
            f"Start pce_core (which creates it) first.",
            file=sys.stderr,
        )
        return 1

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = args.output_root / f"recon_cowork_auto_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    ag_root = _resolve_agent_sessions_root()
    if ag_root is None:
        print(
            "[auto] warning: pce_persistence_watcher discovery returned no "
            "Claude Desktop install. L3g snapshots will no-op. Verify with: "
            "python -m pce_persistence_watcher discover",
            file=sys.stderr,
        )
    else:
        print(f"[auto] L3g agent_sessions root: {ag_root}")

    writer = ReconWriter(out_dir)
    stats = ReconStats()
    stop_event = threading.Event()
    started_at = time.time()
    stats.started_at = started_at

    selected_names: set[str]
    if args.steps:
        selected_names = {s.strip() for s in args.steps.split(",") if s.strip()}
        unknown = selected_names - {n for n, _ in STEPS}
        if unknown:
            print(f"[auto] error: unknown step(s): {sorted(unknown)}", file=sys.stderr)
            return 1
    else:
        selected_names = {n for n, _ in STEPS}

    meta = {
        "mode": "automated",
        "started_at": started_at,
        "db_path": str(db_path),
        "agent_sessions_root": str(ag_root) if ag_root else None,
        "cowork_url_patterns": list(COWORK_URL_PATTERNS),
        "selected_steps": sorted(selected_names),
        "argv": sys.argv,
        "host_os": sys.platform,
        "python": sys.version,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8",
    )

    tailer = DbTailer(
        db_path=db_path, since_ts=started_at, writer=writer, stats=stats,
        stop_event=stop_event, poll_interval_s=args.poll_interval,
    )
    tailer.start()
    print(f"[auto] writing to {out_dir.resolve()}")
    print(
        f"[auto] tailing {db_path} since ts={started_at:.1f}, "
        f"poll={args.poll_interval}s; {len(COWORK_URL_PATTERNS)} URL patterns"
    )

    # Construct driver — this lazily resolves the Claude window on first
    # focus() call; if Claude isn't running we'll get a clean error in
    # the first step rather than a confusing traceback at module load.
    driver = ClaudeDesktopDriver()
    ctx = StepCtx(
        driver=driver,
        writer=writer,
        stats=stats,
        ag_root=ag_root,
        out_dir=out_dir,
    )

    try:
        for name, fn in STEPS:
            if name not in selected_names:
                continue
            print(f"\n[auto] === STEP: {name} ===")
            sys.stdout.flush()
            t0 = time.time()
            try:
                result = fn(ctx)
                elapsed_s = round(time.time() - t0, 1)
                ctx.step_results[name] = {
                    "ok": True, "elapsed_s": elapsed_s, "result": result,
                }
                print(f"[auto] step {name} OK ({elapsed_s}s)")
            except Exception as exc:
                tb = traceback.format_exc()
                elapsed_s = round(time.time() - t0, 1)
                ctx.step_results[name] = {
                    "ok": False, "elapsed_s": elapsed_s,
                    "error": str(exc), "tb": tb,
                }
                _mark(ctx, f"step-error-{name}", extra={"err": str(exc)})
                print(f"[auto] step {name} FAILED: {exc}", file=sys.stderr)
                print(tb, file=sys.stderr)
            sys.stdout.flush()
    finally:
        stop_event.set()
        tailer.stop()
        ended_at = time.time()
        writer.close()

        (out_dir / "step_results.json").write_text(
            json.dumps(ctx.step_results, indent=2, default=str),
            encoding="utf-8",
        )

        summary = {
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_s": round(ended_at - started_at, 1),
            "db_events": stats.db_events,
            "db_events_by_pattern": dict(stats.db_events_by_pattern),
            "markers": stats.markers,
            "manifests_dumped": stats.manifests_dumped,
            "output_dir": str(out_dir),
            "steps_run": list(ctx.step_results.keys()),
            "steps_ok": [n for n, r in ctx.step_results.items() if r.get("ok")],
            "steps_failed": [n for n, r in ctx.step_results.items() if not r.get("ok")],
        }
        (out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8",
        )

        skel_path = _write_findings_skeleton(
            out_dir=out_dir, stats=stats, started_at=started_at,
            ended_at=ended_at, markers_path=writer.markers_path,
        )

        print(f"\n[auto] === DONE ===")
        print(f"[auto] elapsed: {summary['elapsed_s']}s")
        print(
            f"[auto] db_events: {summary['db_events']} "
            f"({len(summary['db_events_by_pattern'])} patterns)"
        )
        print(f"[auto] markers: {summary['markers']}")
        print(f"[auto] manifests dumped: {summary['manifests_dumped']}")
        print(f"[auto] steps ok: {summary['steps_ok']}")
        if summary["steps_failed"]:
            print(f"[auto] steps FAILED: {summary['steps_failed']}")
        print(f"[auto] output: {out_dir.resolve()}")
        print(f"[auto] findings skeleton: {skel_path.resolve()}")
        print(
            "[auto] next: review step_results.json + UIA dumps, then fold "
            "into Docs/research/2026-05-XX-cowork-recon-findings.md."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
