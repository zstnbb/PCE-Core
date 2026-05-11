# SPDX-License-Identifier: Apache-2.0
"""P5.B.5 · MSIX-native cowork-region RECON for Claude Desktop.

Sibling to :mod:`tests.manual.recon_claude_desktop` which is **CDP-based**
and therefore **does NOT work on Windows MSIX** per ADR-018 §3.5
(``connect_over_cdp`` cannot reach the MSIX-sandboxed Chromium child
process). This script uses the three working axes from sub-runs 1-5 of
the chat sweep:

* **N axis** — tails ``~/.pce/data/pce.db raw_captures`` (populated by
  ``pce_proxy/`` mitmdump capturing ``claude.ai``); emits per-event
  records grouped by URL pattern.
* **L3g axis** — tails the Claude Desktop ``LocalCache\\Roaming\\Claude\\
  local-agent-mode-sessions\\`` directory; dumps the latest
  ``manifest.json`` on demand (closes §5.B.2 Q5).
* **UIA driver axis** — leaves UIA dumps to the standalone
  :mod:`tests.e2e_desktop_ui.scripts.dump_uia` script (modes
  ``open-cowork`` / ``open-skills`` / ``open-dispatch`` /
  ``open-scheduled`` / ``open-customize``); this RECON tool just
  records the click-through timing as markers.

Per the §5.B.2 acceptance, this RECON closes 6 open questions:

* **Q1** Skills picker UIA shape — closed by
  ``dump_uia open-skills`` plus ``mark skill-picker-open`` here.
* **Q2** async step semantics (SSE-per-step / single-stream / long-poll)
  — closed by N-axis event-timing analysis on a multi-step task.
* **Q3** Dispatch (Beta) window class — closed by ``dump_uia open-dispatch``
  plus ``mark dispatch-open``.
* **Q4** ``/skills/list-skills`` schema — closed by N-axis full-body
  capture (use ``--full-bodies`` flag here AND verify pce_proxy
  doesn't truncate; alternatively dump from the captured event).
* **Q5** ``local-agent-mode-sessions/<uuid>/manifest.json`` schema —
  closed by ``dump-agent-session`` REPL command.
* **Q6** scheduled-task lifecycle — closed by N-axis tail across
  scheduled-time + L3g manifest tail.

Usage::

    # Pre-flight (you do this manually):
    #   1. Claude Desktop is running, logged in, on Cowork tab.
    #   2. mitmdump :8080 is up and system proxy points at it.
    #   3. pce_persistence_watcher is running:
    #        python -m pce_persistence_watcher watch --poll-interval 5
    #
    # Then in this terminal:
    python -m tests.manual.recon_claude_desktop_cowork --duration 3600

    # During the run, drive Claude Desktop UI per the handoff §4 list,
    # marking each step:
    #   > mark cowork-tab
    #   > mark task-single
    #   > mark skill-picker-open
    #   > mark skill-xlsx
    #   > mark dispatch-open
    #   > mark scheduled-create
    #   > mark settings-toggle
    #   > mark idle-start
    #   > dump-agent-session
    #   > stop

After the run, ``tests/manual/recon_cowork_<ts>/`` contains:

* ``meta.json`` — run-level metadata
* ``events.jsonl`` — DB-tail records (one per new raw_captures row in
  the cowork-relevant URL set)
* ``markers.jsonl`` — REPL-issued markers + notes
* ``manifests/<uuid>.json`` — full-body L3g manifest dumps
* ``summary.json`` — per-marker / per-URL-pattern counts
* ``findings_skeleton.md`` — partial findings doc with auto-filled
  answers (you copy this to ``Docs/research/<date>-cowork-recon-
  findings.md`` and complete the human-judgement parts)

This script intentionally **does NOT post to PCE Core** and **does NOT
write to ``raw_captures``** — it only READS the DB. RECON traffic
stays in the user's actual Claude Desktop session, which goes through
the live ``pce_proxy`` capture path. That's the whole point: the RECON
is OBSERVING the production capture pipeline, not bypassing it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger("pce.recon.cowork")


# ---------------------------------------------------------------------------
# Cowork-relevant URL patterns. Anything not on this list is ignored by
# the DB tailer to keep events.jsonl scoped. The patterns are matched
# as case-insensitive substrings against ``raw_captures.path``.
# ---------------------------------------------------------------------------

COWORK_URL_PATTERNS: tuple[str, ...] = (
    # Cowork tab heartbeat / settings (C00, C13)
    "/cowork_settings",
    "/environments?included_worker_types=cowork",
    "/environments",  # broader fallback in case the query string is stripped
    "/skills/list-skills",
    "/referral/eligibility",
    # Task lifecycle (C01-C04, C12)
    "/chat_conversations/",
    "/completion",
    # Cowork file lifecycle (C05, C06, C09)
    "/wiggle/upload-file",
    "/wiggle/download-file",
    "/artifacts/",
    # MCP (C07, C16) — these go through pce_mcp_proxy not L1 proxy,
    # but if the user's setup forwards MCP through L1 too the DB will
    # still record them under host=localhost:<port>; harmless to include.
    "/mcp/",
    "tools/list",
    "tools/call",
)


# ---------------------------------------------------------------------------
# L3g — discover the local-agent-mode-sessions root for the running
# Claude Desktop install. We reuse pce_persistence_watcher.discovery's
# logic so the path resolution stays canonical.
# ---------------------------------------------------------------------------


def _resolve_agent_sessions_root() -> Optional[Path]:
    """Return the absolute path to ``local-agent-mode-sessions/`` for
    the active Claude Desktop install on this machine, or None if no
    install is discovered (in which case L3g dump features no-op
    gracefully).
    """
    try:
        from pce_persistence_watcher.discovery import discover  # type: ignore
    except Exception as exc:
        logger.warning("pce_persistence_watcher.discovery import failed: %s", exc)
        return None

    try:
        installs = discover()
    except Exception as exc:
        logger.warning("pce_persistence_watcher.discover() failed: %s", exc)
        return None

    for inst in installs:
        # discover() returns canonical app_id "claude-desktop" (hyphen)
        # per pce_persistence_watcher.discovery._DISCOVERY_ORDER.
        if inst.app_id != "claude-desktop":
            continue
        ag_root = inst.root("agent_sessions")
        if ag_root is not None and ag_root.exists():
            return ag_root
    return None


# ---------------------------------------------------------------------------
# Recon stats
# ---------------------------------------------------------------------------


@dataclass
class ReconStats:
    started_at: float = 0.0
    db_events: int = 0
    db_events_by_pattern: Counter = field(default_factory=Counter)
    markers: int = 0
    manifests_dumped: int = 0
    last_event_ts: float = 0.0


# ---------------------------------------------------------------------------
# Recon writer (thread-safe JSONL appender)
# ---------------------------------------------------------------------------


class ReconWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.events_path = out_dir / "events.jsonl"
        self.markers_path = out_dir / "markers.jsonl"
        self.manifests_dir = out_dir / "manifests"
        self._lock = threading.Lock()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(exist_ok=True)
        self._events_fp = self.events_path.open("a", encoding="utf-8")
        self._markers_fp = self.markers_path.open("a", encoding="utf-8")

    def write_event(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self._lock:
            self._events_fp.write(line + "\n")
            self._events_fp.flush()

    def write_marker(self, marker: dict) -> None:
        line = json.dumps(marker, ensure_ascii=False, default=str)
        with self._lock:
            self._markers_fp.write(line + "\n")
            self._markers_fp.flush()
            # Also mirror into events.jsonl so analyzer can split events
            # by surrounding markers without a join.
            self._events_fp.write(line + "\n")
            self._events_fp.flush()

    def write_manifest(self, uuid: str, body: str) -> Path:
        p = self.manifests_dir / f"{uuid}.json"
        p.write_text(body, encoding="utf-8", errors="replace")
        return p

    def close(self) -> None:
        with self._lock:
            try:
                self._events_fp.close()
                self._markers_fp.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# DB tailer — polls raw_captures for cowork-relevant rows
# ---------------------------------------------------------------------------


class DbTailer:
    """Polls ``~/.pce/data/pce.db raw_captures`` since baseline_ts and
    emits one event per new row whose path matches a cowork pattern.

    Runs on a background thread; stops on ``stop_event``.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        since_ts: float,
        writer: ReconWriter,
        stats: ReconStats,
        stop_event: threading.Event,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._db_path = db_path
        self._since_ts = since_ts
        self._writer = writer
        self._stats = stats
        self._stop_event = stop_event
        self._poll_interval_s = poll_interval_s
        # Cursor by created_at (REAL) — `id` column is TEXT (UUID hex)
        # post-schema-migration 0011/0012 and not int-castable.
        self._cursor_ts: float = since_ts
        self._stats_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="pce-cowork-recon-db", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 3.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        # Cursor is initialised to since_ts in __init__ — no DB seek
        # needed. We emit only rows with created_at > since_ts.
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                logger.exception("DB tailer poll failed")
            self._stop_event.wait(timeout=self._poll_interval_s)

    def _poll_once(self) -> None:
        con = sqlite3.connect(str(self._db_path))
        try:
            con.row_factory = sqlite3.Row
            cur = con.execute(
                "SELECT id, created_at, host, path, method, direction, "
                "       status_code, body_format, length(body_text_or_json) AS body_len, "
                "       pair_id, source, source_id "
                "FROM raw_captures "
                "WHERE created_at > ? "
                "ORDER BY created_at ASC",
                (self._cursor_ts,),
            )
            rows = cur.fetchall()
        finally:
            con.close()

        for r in rows:
            ts = r["created_at"]
            if ts is not None:
                ts_f = float(ts)
                if ts_f > self._cursor_ts:
                    self._cursor_ts = ts_f
            path = (r["path"] or "")
            host = (r["host"] or "")
            matched = _match_cowork_pattern(path, host)
            if matched is None:
                continue
            event = {
                "ts": time.time(),
                "kind": "raw_capture",
                "id": str(r["id"]),
                "created_at": float(r["created_at"]) if r["created_at"] is not None else None,
                "host": host,
                "path": path,
                "method": r["method"],
                "direction": r["direction"],
                "status_code": r["status_code"],
                "body_format": r["body_format"],
                "body_len": int(r["body_len"]) if r["body_len"] is not None else 0,
                "pair_id": r["pair_id"],
                "source": r["source"],
                "source_id": r["source_id"],
                "pattern": matched,
            }
            self._writer.write_event(event)
            with self._stats_lock:
                self._stats.db_events += 1
                self._stats.db_events_by_pattern[matched] += 1
                self._stats.last_event_ts = event["ts"]


def _match_cowork_pattern(path: str, host: str) -> Optional[str]:
    if not path and not host:
        return None
    p_lower = path.lower()
    for pat in COWORK_URL_PATTERNS:
        if pat.lower() in p_lower:
            return pat
    return None


# ---------------------------------------------------------------------------
# L3g manifest dumper — finds latest agent_sessions/<uuid>/manifest.json
# ---------------------------------------------------------------------------


def dump_latest_agent_session(
    ag_root: Optional[Path],
    writer: ReconWriter,
    stats: ReconStats,
) -> Optional[Path]:
    """Find the most recently-modified ``<uuid>/manifest.json`` under
    ``ag_root`` and copy its body to ``manifests/<uuid>.json`` in the
    recon output dir. Returns the written path, or None if not found.
    """
    if ag_root is None or not ag_root.exists():
        print("[recon] dump-agent-session: no agent_sessions root found "
              "(install discovery returned None or path missing). Skipping.")
        return None

    candidates: list[tuple[float, Path, str]] = []
    try:
        for child in ag_root.iterdir():
            if not child.is_dir():
                continue
            manifest = child / "manifest.json"
            if not manifest.exists():
                continue
            try:
                mtime = manifest.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, manifest, child.name))
    except OSError as exc:
        logger.warning("dump-agent-session: iterdir failed: %s", exc)
        return None

    if not candidates:
        print(f"[recon] dump-agent-session: no <uuid>/manifest.json under {ag_root}")
        return None

    candidates.sort(reverse=True)  # newest first
    mtime, manifest, uuid = candidates[0]
    try:
        body = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[recon] dump-agent-session: read failed: {exc}")
        return None

    written = writer.write_manifest(uuid, body)
    stats.manifests_dumped += 1
    age_s = max(0, time.time() - mtime)
    print(
        f"[recon] dump-agent-session: wrote {written} "
        f"({len(body)} bytes; manifest mtime was {age_s:.1f}s ago)"
    )
    return written


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------


HELP_TEXT = """
P5.B.5 cowork RECON — REPL commands

  mark <label>          tag upcoming traffic with this label.
                        Suggested labels (mirrors handoff §4):
                          cowork-tab            switch to Cowork tab
                          task-single           start a 1-step agent task
                          task-multistep        start a multi-step task (Q2)
                          task-cancel           click Stop mid-task
                          task-file-input       upload a file
                          task-code-output      task that writes a code artefact
                          task-mcp              task that calls a non-PCE MCP tool
                          skill-picker-open     type '/' in composer (Q1)
                          skill-xlsx            select /xlsx skill
                          skill-pdf             select /pdf skill
                          live-artifacts-open   open Live Artifacts pane
                          dispatch-open         open Dispatch (Beta) pane (Q3)
                          dispatch-launch       launch ≥2 concurrent tasks
                          scheduled-open        open Scheduled pane
                          scheduled-create      create a scheduled task (Q6)
                          project-cowork        launch cowork inside a project
                          settings-toggle       toggle a Customize setting
                          idle-start            begin 5-min idle window
                          mcpb-install          drag .mcpb into Settings → Extensions
  note <text>           add a free-form note to the timeline
  dump-agent-session    dump latest local-agent-mode-sessions/<uuid>/manifest.json
                        (closes Q5; run after each task completes)
  stats                 print current event counts grouped by URL pattern
  list-markers          list markers recorded so far
  patterns              print the cowork URL pattern list
  stop                  graceful shutdown
  help                  show this message
"""


def _run_repl(
    *,
    writer: ReconWriter,
    stats: ReconStats,
    stop_event: threading.Event,
    ag_root: Optional[Path],
) -> None:
    print(HELP_TEXT)
    sys.stdout.flush()
    markers: list[dict] = []

    while not stop_event.is_set():
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[recon] EOF / interrupt — initiating graceful stop")
            stop_event.set()
            break

        if not line:
            continue

        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if cmd == "mark":
            if not rest:
                print("[recon] usage: mark <label>")
                continue
            marker = {
                "ts": time.time(),
                "kind": "marker",
                "label": rest.strip(),
            }
            writer.write_marker(marker)
            markers.append(marker)
            stats.markers += 1
            print(f"[recon] marker recorded: {rest!r}")
        elif cmd == "note":
            if not rest:
                print("[recon] usage: note <text>")
                continue
            writer.write_marker({
                "ts": time.time(),
                "kind": "note",
                "text": rest.strip(),
            })
            print("[recon] note recorded")
        elif cmd in ("dump-agent-session", "dump-l3g", "dump"):
            dump_latest_agent_session(ag_root, writer, stats)
        elif cmd == "stats":
            print(json.dumps({
                "started_at": stats.started_at,
                "elapsed_s": round(time.time() - stats.started_at, 1),
                "db_events": stats.db_events,
                "db_events_by_pattern": dict(stats.db_events_by_pattern),
                "markers": stats.markers,
                "manifests_dumped": stats.manifests_dumped,
            }, indent=2))
        elif cmd == "list-markers":
            if not markers:
                print("[recon] (no markers yet)")
            else:
                for m in markers:
                    ts = time.strftime("%H:%M:%S", time.localtime(m["ts"]))
                    print(f"  {ts}  {m['label']}")
        elif cmd == "patterns":
            print("[recon] cowork URL patterns:")
            for p in COWORK_URL_PATTERNS:
                print(f"  - {p}")
        elif cmd in ("stop", "exit", "quit"):
            print("[recon] graceful stop")
            stop_event.set()
            break
        elif cmd in ("help", "?"):
            print(HELP_TEXT)
        else:
            print(f"[recon] unknown command: {cmd!r}. Type 'help'.")


# ---------------------------------------------------------------------------
# Findings skeleton — pre-fills auto-answerable parts
# ---------------------------------------------------------------------------


FINDINGS_SKELETON_TEMPLATE = """\
# Cowork-region RECON findings — {date_iso}

> **Scope**: closes the 6 open architectural questions in
> `Docs/stability/DESKTOP-PRODUCT-MATRIX.md` §5.B.2, gated by
> `Docs/handoff/HANDOFF-P1-CLAUDE-DESKTOP-COWORK-KICKOFF-2026-05-10.md`
> §4. Generated from `tests/manual/{out_dir_name}/` recon artefacts.

## Run metadata

- **Recon start**: `{started_at_iso}`
- **Recon end**: `{ended_at_iso}` ({elapsed_s:.0f} s)
- **DB events captured**: **{db_events}** across {db_pattern_count} URL patterns
- **Markers**: **{markers}**
- **Agent-session manifests dumped**: **{manifests_dumped}**

### Per-pattern event counts (auto-filled)

{pattern_table}

### Marker timeline (auto-filled, fill in human notes per row)

{marker_timeline}

---

## Q1 — Skills picker UIA shape

**Empirical answer**: TBD — fill from `_uia_dump_open-skills.txt`
(produced by `python -m tests.e2e_desktop_ui.scripts.dump_uia open-skills`).
Look for the popup root element type:

- If the picker root has `control_type="Pane"` or `"Custom"` and the
  Skills entries are descendants of the same Claude main window:
  **descendant** → `pick_skill()` reuses
  `_find_uia_by_name_substr` (sub-run 5 default).
- If the picker has a separate top-level `control_type="Window"`
  ancestor (different `class_name`):
  **separate Win32 popup** → `pick_skill()` needs
  `_find_uia_by_name_substr_all` cross-window mode (sub-run 4 added
  this for the model picker; reusable here at zero cost).

**Driver implication**: TBD.

**Evidence**: `_uia_dump_open-skills.txt` (gitignored).

---

## Q2 — Async step semantics (SSE-per-step / single-stream / long-poll)

**Empirical answer**: TBD — fill from event-timing analysis on the
`task-multistep` marker window in `events.jsonl`. Three signatures:

- **single SSE stream** — exactly 1 `/completion` request, 1 response
  with `body_format='sse'`, multi-step reasoning concatenated in the
  response body. (Most likely shape; matches chat-region D02.)
- **SSE per step** — N `/completion` requests within seconds of each
  other, each with its own `pair_id` and SSE response. `wait_for_cowork_step()`
  must wait for each new request, not just one body.
- **long-poll** — `/completion` returns immediately with a step-id,
  followed by polling `/sessions/<uuid>/steps?since=<step-id>` GETs
  every few seconds. Distinctive shape — different URL family.

**Driver implication**: TBD.

**Evidence**: `events.jsonl` between `mark task-multistep` and the next
non-task marker.

---

## Q3 — Dispatch (Beta) window class

**Empirical answer**: TBD — fill from `_uia_dump_open-dispatch.txt`.
Compare top-level windows:

- If clicking 'Dispatch' just changes the in-app pane (same Claude
  main window in foreground): **in-app sidebar pane**.
- If a new top-level Win32 window appears with a distinct `class_name`:
  **separate popup**; `open_dispatch()` needs to enumerate
  `Desktop.windows()` and switch to the new one.

**Driver implication**: TBD.

**Evidence**: `_uia_dump_open-dispatch.txt`.

---

## Q4 — `/skills/list-skills` schema

**Empirical answer**: TBD — fill from the largest `/skills/list-skills`
event body in `events.jsonl` (look for `body_len` ~4-5 KB; ADR-018
recorded 4927 B). Need to inspect the actual response body via:

```powershell
Get-Content tests/manual/{out_dir_name}/events.jsonl | Select-String 'list-skills'
# Then for the matching id, query the DB directly:
sqlite3 ~/.pce/data/pce.db ".mode json" "SELECT body_text_or_json FROM raw_captures WHERE id = <ID>"
```

Expected shape per ADR-018: a JSON array with entries containing
`id`, `name`, `kind`, `description` for at least 8 skills (xlsx, pdf,
pptx, docx, consolidate-memory, skill-creator, schedule, setup-cowork).

**Driver implication for `pick_skill(name)`**: TBD — what field do
we match `name` against? `name`? `id`?

**Evidence**: full body dump from `raw_captures.body_text_or_json`
where `id = <ID matched above>`.

---

## Q5 — `local-agent-mode-sessions/<uuid>/manifest.json` field schema

**Empirical answer**: TBD — fill from `manifests/<uuid>.json` in this
recon dir (created by `dump-agent-session` REPL command). Examine:

- Top-level keys (e.g. `id`, `created_at`, `task_text`, `status`,
  `steps`, `artifacts`).
- `steps[]` shape — does each step carry `tool_name`, `tool_input`,
  `tool_output`, `model`, latency?
- Foreign-key candidates that map back to the `claude.ai
  conversation_uuid` (so `local_persistence.py` can join L3g rows with
  the N-axis `messages` table).

**`local_persistence.py` v0 implication**: TBD — define the SELECT/INSERT
shape that lifts manifest fields into `sessions` + `messages`.

**Evidence**: `manifests/<uuid>.json` (gitignored under tests/manual/).

---

## Q6 — Scheduled task lifecycle

**Empirical answer**: TBD — fill from the time-window between
`mark scheduled-create` and the scheduled-time marker in `markers.jsonl`,
correlated with `events.jsonl` and `manifests/`. Two signatures:

- **Eager**: `POST /chat_conversations/...` fires immediately on schedule
  creation, conversation row exists from t=create.
- **Lazy**: only the schedule's metadata POST is captured at create-time
  (probably `/cowork_settings` or a dedicated endpoint); the
  `/chat_conversations/...` only fires at scheduled-time.

**C11 acceptance implication**: TBD. If lazy and `>24h` to verify, C11
SKIP is acceptable per §5.B; if eager, C11 should PASS in-sweep.

**Evidence**: `events.jsonl` + `manifests/` cross-reference around the
two markers.

---

## Driver helper signatures (after answering Q1-Q6)

Update `tests/e2e_desktop_ui/drivers/claude_desktop.py` with the 6
cowork helpers per §6 of the kickoff handoff. Sample post-RECON
signature decisions:

- `open_cowork_tab() -> None` — sidebar Button, `aid={TBD}`.
- `pick_skill(name: str) -> None` — `name ∈ {"xlsx", "pdf", "pptx",
  "docx", "consolidate-memory", "skill-creator", "schedule",
  "setup-cowork"}`; matched against the `{TBD}` field of
  `/skills/list-skills` response.
- `select_ask_mode(mode: str) -> None` — composer 'Ask' picker;
  modes: {TBD}.
- `view_live_artifacts() -> None` — sidebar entry, `aid={TBD}`.
- `open_dispatch() -> None` — {sidebar pane | top-level popup
  with class_name `{TBD}`}.
- `open_scheduled() -> None` — sidebar entry, `aid={TBD}`.
- `wait_for_cowork_step(timeout=120) -> WaitResult` — semantics from Q2.

---

## Sign-off

Once all 6 questions above are filled in with concrete empirical
answers, this doc closes the §5.B.2 gating block for P5.B.5
implementation. Next step: scaffold the 6 helpers per the answers,
then proceed to `local_persistence.py` v0.
"""


def _write_findings_skeleton(
    out_dir: Path,
    stats: ReconStats,
    started_at: float,
    ended_at: float,
    markers_path: Path,
) -> Path:
    pattern_lines = []
    if stats.db_events_by_pattern:
        for pat, n in stats.db_events_by_pattern.most_common():
            pattern_lines.append(f"| `{pat}` | {n} |")
    pattern_table = (
        "| URL pattern | events |\n|---|---|\n" + "\n".join(pattern_lines)
        if pattern_lines
        else "_(no cowork-pattern events captured)_"
    )

    marker_rows = []
    try:
        with markers_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts_local = time.strftime(
                    "%H:%M:%S", time.localtime(rec.get("ts", 0))
                )
                if rec.get("kind") == "marker":
                    marker_rows.append(
                        f"| `{ts_local}` | `mark {rec.get('label', '?')}` | _(notes)_ |"
                    )
                elif rec.get("kind") == "note":
                    marker_rows.append(
                        f"| `{ts_local}` | `note` | {rec.get('text', '?')} |"
                    )
    except FileNotFoundError:
        pass

    marker_timeline = (
        "| Time | Marker | Notes |\n|---|---|---|\n" + "\n".join(marker_rows)
        if marker_rows
        else "_(no markers recorded — re-run with REPL `mark` commands)_"
    )

    body = FINDINGS_SKELETON_TEMPLATE.format(
        date_iso=time.strftime("%Y-%m-%d", time.localtime(ended_at)),
        out_dir_name=out_dir.name,
        started_at_iso=time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(started_at)
        ),
        ended_at_iso=time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(ended_at)
        ),
        elapsed_s=ended_at - started_at,
        db_events=stats.db_events,
        db_pattern_count=len(stats.db_events_by_pattern),
        markers=stats.markers,
        manifests_dumped=stats.manifests_dumped,
        pattern_table=pattern_table,
        marker_timeline=marker_timeline,
    )

    out_path = out_dir / "findings_skeleton.md"
    out_path.write_text(body, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def _default_db_path() -> Path:
    return Path.home() / ".pce" / "data" / "pce.db"


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="P5.B.5 MSIX-native cowork-region RECON for Claude Desktop"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3600.0,
        help="Maximum run duration in seconds (default 3600 = 60 min)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="PCE DB path (default: ~/.pce/data/pce.db)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("tests/manual"),
        help="Where to write recon_cowork_<ts>/ artifacts",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="DB poll interval in seconds (default 1.0)",
    )
    parser.add_argument(
        "--print-progress-every",
        type=float,
        default=15.0,
        help="Print progress every N seconds (default 15)",
    )

    args = parser.parse_args(argv)

    db_path = args.db_path or _default_db_path()
    if not db_path.exists():
        print(
            f"[recon] error: DB not found at {db_path}. "
            f"Start pce_core (which creates it) first.",
            file=sys.stderr,
        )
        return 1

    ts = time.strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_root / f"recon_cowork_{ts}"

    ag_root = _resolve_agent_sessions_root()
    if ag_root is None:
        print(
            "[recon] warning: pce_persistence_watcher discovery returned no "
            "Claude Desktop install. dump-agent-session will no-op. Verify "
            "with: python -m pce_persistence_watcher discover",
            file=sys.stderr,
        )
    else:
        print(f"[recon] L3g agent_sessions root: {ag_root}")

    writer = ReconWriter(output_dir)
    stats = ReconStats()
    stop_event = threading.Event()
    started_at = time.time()
    stats.started_at = started_at

    meta = {
        "started_at": started_at,
        "duration_s": args.duration,
        "db_path": str(db_path),
        "agent_sessions_root": str(ag_root) if ag_root else None,
        "cowork_url_patterns": list(COWORK_URL_PATTERNS),
        "argv": sys.argv,
        "host_os": sys.platform,
        "python": sys.version,
    }
    (output_dir / "meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    tailer = DbTailer(
        db_path=db_path,
        since_ts=started_at,
        writer=writer,
        stats=stats,
        stop_event=stop_event,
        poll_interval_s=args.poll_interval,
    )
    tailer.start()
    print(f"[recon] writing to {output_dir.resolve()}")
    print(
        f"[recon] tailing {db_path} since ts={started_at:.1f}, "
        f"poll={args.poll_interval}s; {len(COWORK_URL_PATTERNS)} URL patterns"
    )

    repl_thread = threading.Thread(
        target=_run_repl,
        kwargs={
            "writer": writer,
            "stats": stats,
            "stop_event": stop_event,
            "ag_root": ag_root,
        },
        name="pce-cowork-recon-repl",
        daemon=True,
    )
    repl_thread.start()

    deadline = time.time() + args.duration
    last_progress = 0.0
    try:
        while not stop_event.is_set():
            if time.time() >= deadline:
                print(
                    f"[recon] duration {args.duration}s elapsed — stopping"
                )
                stop_event.set()
                break
            time.sleep(0.5)

            now = time.time()
            if now - last_progress >= args.print_progress_every:
                last_progress = now
                top3 = ", ".join(
                    f"{pat}={n}"
                    for pat, n in stats.db_events_by_pattern.most_common(3)
                )
                print(
                    f"[recon] db_events={stats.db_events} "
                    f"manifests={stats.manifests_dumped} "
                    f"markers={stats.markers} "
                    f"top: {top3 or '(none)'} "
                    f"elapsed={int(now - started_at)}s"
                )
    except KeyboardInterrupt:
        print("\n[recon] Ctrl+C — stopping")
        stop_event.set()
    finally:
        tailer.stop()
        ended_at = time.time()
        writer.close()

        summary = {
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_s": round(ended_at - started_at, 1),
            "db_events": stats.db_events,
            "db_events_by_pattern": dict(stats.db_events_by_pattern),
            "markers": stats.markers,
            "manifests_dumped": stats.manifests_dumped,
            "output_dir": str(output_dir),
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )

        skel_path = _write_findings_skeleton(
            out_dir=output_dir,
            stats=stats,
            started_at=started_at,
            ended_at=ended_at,
            markers_path=writer.markers_path,
        )

        print(f"[recon] final summary: {json.dumps(summary, indent=2)}")
        print(f"[recon] findings skeleton: {skel_path.resolve()}")
        print(
            "[recon] next step: copy findings_skeleton.md to "
            "Docs/research/<date>-cowork-recon-findings.md and answer "
            "Q1-Q6 from the dump files + this output dir."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
