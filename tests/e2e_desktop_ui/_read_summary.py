# SPDX-License-Identifier: Apache-2.0
"""Read the most recent p1_cowork sweep summary.json."""
from __future__ import annotations
import json
from pathlib import Path

root = Path("tests/e2e_desktop_ui/reports/p1_cowork")
runs = sorted(root.glob("*_mode-live"))
if not runs:
    print("no runs found")
    raise SystemExit(1)

latest = runs[-1]
p = latest / "summary.json"
d = json.loads(p.read_text(encoding="utf-8"))
print(f"Run: {latest.name}")
counts = d.get("counts", {})
print(f"PASS={counts.get('pass', 0)} SKIP={counts.get('skip', 0)} FAIL={counts.get('fail', 0)}")
print(f"target={d.get('target')}, achieved={d.get('achieved')}, passes_gate={d.get('passes_acceptance')}")
print()
for c in d["cases"]:
    elapsed = round(c.get("elapsed_s", 0), 1)
    reason = (c.get("reason") or "")[:140]
    verdict = c.get("verdict") or c.get("outcome", "?")
    print(f"  {c['case']:5s} {verdict:5s} {elapsed:>5.1f}s  {reason}")
