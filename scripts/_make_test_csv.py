# SPDX-License-Identifier: Apache-2.0
"""Generate a tiny CSV with the C12 token embedded as a row value."""
from __future__ import annotations
import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default="PCE-C12-8264")
    ap.add_argument(
        "--out",
        default=os.path.join(os.path.expanduser("~"), "Downloads", "pce-c12-8264.csv"),
    )
    args = ap.parse_args()

    rows = [
        ["id", "name", "score", "note"],
        ["1", "alice", "42", "baseline"],
        ["2", "bob", "7", "outlier"],
        ["3", args.token, "99", "highest"],
        ["4", "carol", "55", "median"],
        ["5", "dave", "23", "low"],
    ]

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        for r in rows:
            f.write(",".join(r) + "\n")

    print(f"Wrote: {args.out}")
    print(f"Size: {os.path.getsize(args.out)} bytes")
    print("Rows:")
    for r in rows:
        print("  " + ",".join(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
