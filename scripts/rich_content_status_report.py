"""Generate a current-state report for live-site rich-content capture.

This is a Phase 0 evidence tool. It does not prove support by itself; it
summarizes what the local DB, screenshots, and probe artifacts currently show
so future rich-content work has a truthful baseline.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pce_core.config import DB_PATH  # noqa: E402


REPORT_DIR = ROOT / "tests" / "e2e" / "reports"
SCREENSHOTS_DIR = ROOT / "tests" / "e2e" / "screenshots"
PROBE_DIR = ROOT / "tests" / "e2e"


SCREENSHOT_MARKERS = (
    "before_send",
    "after_response",
    "capture_failed",
    "session_failed",
    "send_fail",
    "no_input",
)


def _safe_json_loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    except sqlite3.Error:
        return 0
    return int(row["c"] if row else 0)


def collect_db_summary(db_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "counts": {},
        "providers": {},
        "storage_contract": {},
        "raw_captures": {},
        "recent_rich_samples": [],
    }
    if not db_path.exists():
        return summary

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        summary["counts"] = {
            "raw_captures": _count_table(conn, "raw_captures"),
            "sessions": _count_table(conn, "sessions"),
            "messages": _count_table(conn, "messages"),
        }
        rich_v1 = conn.execute(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE content_json IS NOT NULL
              AND json_valid(content_json)
              AND json_extract(content_json, '$.rich_content.schema') = 'pce.rich_content.v1'
            """
        ).fetchone()[0]
        legacy_only = conn.execute(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE content_json IS NOT NULL
              AND json_valid(content_json)
              AND json_extract(content_json, '$.attachments') IS NOT NULL
              AND json_extract(content_json, '$.rich_content') IS NULL
            """
        ).fetchone()[0]
        content_json = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content_json IS NOT NULL"
        ).fetchone()[0]
        summary["storage_contract"] = {
            "content_json_messages": int(content_json),
            "rich_content_v1_messages": int(rich_v1),
            "legacy_attachments_only_messages": int(legacy_only),
        }

        raw_rows = conn.execute(
            """
            SELECT COALESCE(provider, 'unknown') AS provider, direction, COUNT(*) AS c
            FROM raw_captures
            GROUP BY COALESCE(provider, 'unknown'), direction
            ORDER BY provider, direction
            """
        ).fetchall()
        raw_summary: dict[str, dict[str, int]] = defaultdict(dict)
        for row in raw_rows:
            raw_summary[row["provider"]][row["direction"]] = int(row["c"])
        summary["raw_captures"] = dict(raw_summary)

        rows = conn.execute(
            """
            SELECT
                COALESCE(s.provider, 'unknown') AS provider,
                s.id AS session_id,
                m.role,
                m.content_text,
                m.content_json
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            ORDER BY s.started_at DESC, m.ts ASC
            """
        ).fetchall()

        providers: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "sessions": set(),
                "messages": 0,
                "rich_messages": 0,
                "attachment_types": Counter(),
                "block_types": Counter(),
                "rich_content_v1_messages": 0,
                "legacy_attachments_only_messages": 0,
            }
        )
        for row in rows:
            provider = row["provider"]
            info = providers[provider]
            info["sessions"].add(row["session_id"])
            info["messages"] += 1

            data = _safe_json_loads(row["content_json"])
            attachments = data.get("attachments") if isinstance(data, dict) else None
            rich_content = data.get("rich_content") if isinstance(data, dict) else None
            blocks = rich_content.get("blocks") if isinstance(rich_content, dict) else None
            schema = rich_content.get("schema") if isinstance(rich_content, dict) else None
            has_rich = bool(attachments) or bool(blocks)
            if has_rich:
                info["rich_messages"] += 1
            if schema == "pce.rich_content.v1":
                info["rich_content_v1_messages"] += 1
            elif isinstance(attachments, list):
                info["legacy_attachments_only_messages"] += 1

            if isinstance(attachments, list):
                for att in attachments:
                    if isinstance(att, dict) and att.get("type"):
                        info["attachment_types"][str(att["type"])] += 1
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get("type"):
                        info["block_types"][str(block["type"])] += 1

        normalized: dict[str, Any] = {}
        for provider, info in sorted(providers.items()):
            normalized[provider] = {
                "sessions": len(info["sessions"]),
                "messages": info["messages"],
                "rich_messages": info["rich_messages"],
                "rich_content_v1_messages": info["rich_content_v1_messages"],
                "legacy_attachments_only_messages": info["legacy_attachments_only_messages"],
                "attachment_types": dict(info["attachment_types"].most_common()),
                "block_types": dict(info["block_types"].most_common()),
            }
        summary["providers"] = normalized

        samples = conn.execute(
            """
            SELECT
                COALESCE(s.provider, 'unknown') AS provider,
                s.id AS session_id,
                m.role,
                m.content_text,
                m.content_json,
                m.ts
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            WHERE m.content_json IS NOT NULL
            ORDER BY m.ts DESC
            LIMIT 12
            """
        ).fetchall()
        for row in samples:
            data = _safe_json_loads(row["content_json"])
            attachment_types = []
            for att in data.get("attachments") or []:
                if isinstance(att, dict) and att.get("type"):
                    attachment_types.append(att["type"])
            rich_content = data.get("rich_content") if isinstance(data, dict) else None
            blocks = rich_content.get("blocks") if isinstance(rich_content, dict) else None
            summary["recent_rich_samples"].append(
                {
                    "provider": row["provider"],
                    "session_id": row["session_id"],
                    "role": row["role"],
                    "ts": row["ts"],
                    "text_preview": (row["content_text"] or "").replace("\n", " ")[:160],
                    "attachment_types": attachment_types,
                    "rich_schema": rich_content.get("schema") if isinstance(rich_content, dict) else None,
                    "block_types": [
                        block["type"]
                        for block in blocks or []
                        if isinstance(block, dict) and block.get("type")
                    ],
                }
            )
    finally:
        conn.close()

    return summary


def collect_screenshot_summary(screenshots_dir: Path) -> dict[str, Any]:
    by_site: dict[str, Counter] = defaultdict(Counter)
    latest: dict[str, dict[str, str]] = defaultdict(dict)
    if not screenshots_dir.exists():
        return {"exists": False, "by_site": {}, "latest": {}}

    for path in screenshots_dir.glob("*.png"):
        name = path.stem
        site = name.split("_", 1)[0]
        for marker in SCREENSHOT_MARKERS:
            if f"_{marker}_" in name:
                by_site[site][marker] += 1
                current = latest[site].get(marker)
                if not current or path.stat().st_mtime > Path(current).stat().st_mtime:
                    latest[site][marker] = str(path)

    return {
        "exists": True,
        "by_site": {
            site: {marker: counts.get(marker, 0) for marker in SCREENSHOT_MARKERS}
            for site, counts in sorted(by_site.items())
        },
        "latest": {site: dict(paths) for site, paths in sorted(latest.items())},
    }


def collect_probe_artifacts(probe_dir: Path) -> list[dict[str, Any]]:
    artifacts = []
    patterns = ("*probe*.json", "*diag*.json", "login_status_results.json", "site_*_probe.json")
    seen: set[Path] = set()
    for pattern in patterns:
        for path in probe_dir.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            artifacts.append(
                {
                    "path": str(path),
                    "size": path.stat().st_size,
                    "mtime": path.stat().st_mtime,
                }
            )
    return sorted(artifacts, key=lambda item: item["path"])


def collect_report(db_path: Path) -> dict[str, Any]:
    return {
        "generated_at": time.time(),
        "root": str(ROOT),
        "db": collect_db_summary(db_path),
        "screenshots": collect_screenshot_summary(SCREENSHOTS_DIR),
        "probe_artifacts": collect_probe_artifacts(PROBE_DIR),
    }


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(out)


def render_markdown(report: dict[str, Any]) -> str:
    db = report["db"]
    lines = [
        "# PCE Rich Content Current-State Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report['generated_at']))}",
        f"Repo: `{report['root']}`",
        f"DB: `{db['db_path']}`",
        "",
        "## DB Counts",
        "",
    ]
    counts = db.get("counts", {})
    lines.append(
        _markdown_table(
            ["raw_captures", "sessions", "messages"],
            [[str(counts.get("raw_captures", 0)), str(counts.get("sessions", 0)), str(counts.get("messages", 0))]],
        )
    )
    storage_contract = db.get("storage_contract", {})
    lines.extend(
        [
            "",
            "## Storage Contract Summary",
            "",
            _markdown_table(
                ["content_json", "rich_content_v1", "legacy_attachments_only"],
                [
                    [
                        str(storage_contract.get("content_json_messages", 0)),
                        str(storage_contract.get("rich_content_v1_messages", 0)),
                        str(storage_contract.get("legacy_attachments_only_messages", 0)),
                    ]
                ],
            ),
        ]
    )

    provider_rows = []
    for provider, info in db.get("providers", {}).items():
        provider_rows.append(
            [
                provider,
                str(info.get("sessions", 0)),
                str(info.get("messages", 0)),
                str(info.get("rich_messages", 0)),
                str(info.get("rich_content_v1_messages", 0)),
                str(info.get("legacy_attachments_only_messages", 0)),
                ", ".join(f"{k}:{v}" for k, v in info.get("attachment_types", {}).items()) or "-",
                ", ".join(f"{k}:{v}" for k, v in info.get("block_types", {}).items()) or "-",
            ]
        )
    lines.extend(
        [
            "",
            "## Provider Rich Content Summary",
            "",
            _markdown_table(
                ["provider", "sessions", "messages", "rich_messages", "rich_v1", "legacy_only", "attachment_types", "block_types"],
                provider_rows or [["-", "0", "0", "0", "0", "0", "-", "-"]],
            ),
        ]
    )

    capture_rows = []
    for provider, directions in db.get("raw_captures", {}).items():
        capture_rows.append(
            [
                provider,
                str(directions.get("conversation", 0)),
                str(directions.get("network_intercept", 0)),
                str(directions.get("request", 0)),
                str(directions.get("response", 0)),
            ]
        )
    lines.extend(
        [
            "",
            "## Raw Capture Direction Summary",
            "",
            _markdown_table(
                ["provider", "conversation", "network_intercept", "request", "response"],
                capture_rows or [["-", "0", "0", "0", "0"]],
            ),
        ]
    )

    screenshot_rows = []
    for site, counts in report["screenshots"].get("by_site", {}).items():
        screenshot_rows.append([site] + [str(counts.get(marker, 0)) for marker in SCREENSHOT_MARKERS])
    lines.extend(
        [
            "",
            "## Live Screenshot Artifact Summary",
            "",
            _markdown_table(
                ["site", *SCREENSHOT_MARKERS],
                screenshot_rows or [["-", *["0" for _ in SCREENSHOT_MARKERS]]],
            ),
        ]
    )

    sample_rows = []
    for sample in db.get("recent_rich_samples", []):
        sample_rows.append(
            [
                sample["provider"],
                sample["role"],
                ", ".join(sample["attachment_types"]) or "-",
                sample.get("rich_schema") or "-",
                ", ".join(sample.get("block_types") or []) or "-",
                sample["text_preview"].replace("|", "\\|"),
            ]
        )
    lines.extend(
        [
            "",
            "## Recent Rich Message Samples",
            "",
            _markdown_table(
                ["provider", "role", "attachment_types", "rich_schema", "block_types", "text_preview"],
                sample_rows or [["-", "-", "-", "-", "-", "-"]],
            ),
        ]
    )

    lines.extend(["", "## Probe Artifacts", ""])
    for artifact in report.get("probe_artifacts", []):
        lines.append(f"- `{artifact['path']}` ({artifact['size']} bytes)")
    if not report.get("probe_artifacts"):
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Interpretation Guardrail",
            "",
            "This report is evidence inventory only. A site is stable only after capture, storage, Dashboard replay, and repeated live-run gates pass.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite DB path")
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR, help="Report output directory")
    parser.add_argument("--no-write", action="store_true", help="Print markdown only")
    args = parser.parse_args()

    report = collect_report(args.db)
    markdown = render_markdown(report)
    if args.no_write:
        print(markdown)
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = args.out_dir / f"rich_content_status_{stamp}.json"
    md_path = args.out_dir / f"rich_content_status_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
