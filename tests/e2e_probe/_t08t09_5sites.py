#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Ephemeral helper: extract T07/T08/T09 status across 5 target sites.

Walks ``tests/e2e_probe/reports/*/summary.json`` and emits a
recent-attempts matrix for the regenerate / branch-flip family on
ChatGPT, Claude, Gemini, GoogleAIStudio, Grok.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "tests" / "e2e_probe" / "reports"
SITES = ["chatgpt", "claude", "gemini", "googleaistudio", "grok"]
CASES = ["T07", "T08", "T09"]

# attempt key -> (timestamp, status, reason)
seen: dict[tuple[str, str], tuple[str, str, str]] = {}

for run_dir in sorted(ROOT.iterdir(), reverse=True):
    if not run_dir.is_dir():
        continue
    summary = run_dir / "summary.json"
    if not summary.exists():
        continue
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except Exception:
        continue
    results = data.get("results")
    if not isinstance(results, list):
        continue
    for r in results:
        site = (r.get("site_name") or "").lower()
        cid = (r.get("case_id") or "").upper()
        if site not in SITES or cid not in CASES:
            continue
        key = (site, cid)
        if key in seen:
            continue
        status = (r.get("status") or "").upper()
        details = r.get("details") or {}
        reason = (
            r.get("summary")
            or details.get("phase")
            or details.get("code")
            or ""
        )
        seen[key] = (run_dir.name, status, str(reason)[:90])

print(f"{'site':<16}{'case':<6}{'status':<8}{'most-recent-run':<18}reason")
print("-" * 110)
for site in SITES:
    for cid in CASES:
        key = (site, cid)
        if key in seen:
            ts, status, reason = seen[key]
            print(f"{site:<16}{cid:<6}{status:<8}{ts:<18}{reason}")
        else:
            print(f"{site:<16}{cid:<6}{'(none)':<8}{'-':<18}no run found")

# Histogram: how many distinct runs touched T07/T08/T09 per site overall
print()
print("=== run-count per (site,case) across all reports ===")
counts: dict[tuple[str, str], int] = {}
for run_dir in sorted(ROOT.iterdir()):
    if not run_dir.is_dir():
        continue
    summary = run_dir / "summary.json"
    if not summary.exists():
        continue
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except Exception:
        continue
    for r in data.get("results") or []:
        site = (r.get("site_name") or "").lower()
        cid = (r.get("case_id") or "").upper()
        if site in SITES and cid in CASES:
            counts[(site, cid)] = counts.get((site, cid), 0) + 1

for site in SITES:
    for cid in CASES:
        n = counts.get((site, cid), 0)
        print(f"{site:<16}{cid:<6}runs={n}")
