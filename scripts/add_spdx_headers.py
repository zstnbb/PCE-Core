#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Add / check SPDX license headers on source files.

Modes:
    python scripts/add_spdx_headers.py              # dry-run (default, prints planned changes)
    python scripts/add_spdx_headers.py --apply      # actually write changes
    python scripts/add_spdx_headers.py --check      # CI mode: exit 1 if any file missing header

The script inserts a single-line SPDX tag at the top of supported source files.
If a shebang or coding-declaration is present, the tag is inserted after it.
Files that already contain `SPDX-License-Identifier` in the first 10 lines are left untouched.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Iterable


PY_HEADER = "# SPDX-License-Identifier: Apache-2.0\n"
JS_HEADER = "// SPDX-License-Identifier: Apache-2.0\n"

EXTENSIONS: dict[str, str] = {
    ".py": PY_HEADER,
    ".ts": JS_HEADER,
    ".tsx": JS_HEADER,
    ".js": JS_HEADER,
    ".jsx": JS_HEADER,
    ".mjs": JS_HEADER,
}

# Directories we never scan (either not source or generated output)
EXCLUDE_DIRS: set[str] = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".wxt",
    ".output",
    "dist",
    "build",
    ".pytest_cache",
    "htmlcov",
    "Docs",          # markdown / non-source
    ".idea",
    ".vscode",
}

CODING_DECL_RE = re.compile(r"^\s*#.*coding[:=]")


def has_spdx(content: str) -> bool:
    head = "\n".join(content.splitlines()[:10])
    return "SPDX-License-Identifier" in head


def insert_header(content: str, header: str) -> str:
    """Insert header at the top, after any shebang or coding declaration."""
    if not content:
        return header

    lines = content.splitlines(keepends=True)
    insert_at = 0

    # Skip shebang line
    if lines and lines[0].startswith("#!"):
        insert_at = 1

    # Skip coding declaration (applies mostly to Python)
    if insert_at < len(lines) and CODING_DECL_RE.match(lines[insert_at]):
        insert_at += 1

    return "".join(lines[:insert_at]) + header + "".join(lines[insert_at:])


def is_excluded(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in EXCLUDE_DIRS for part in rel.parts)


def iter_source_files(root: pathlib.Path) -> Iterable[tuple[pathlib.Path, str]]:
    for ext, header in EXTENSIONS.items():
        for path in root.rglob(f"*{ext}"):
            if not path.is_file():
                continue
            if is_excluded(path, root):
                continue
            yield path, header


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually modify files (default: dry-run).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI mode: exit 1 if any file is missing the header.",
    )
    parser.add_argument(
        "--root",
        default=".",
        type=pathlib.Path,
        help="Repository root to scan (default: current directory).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many missing file paths to print (default: 20).",
    )
    args = parser.parse_args()

    if args.apply and args.check:
        parser.error("--apply and --check are mutually exclusive.")

    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"Root does not exist or is not a directory: {root}")

    missing: list[pathlib.Path] = []
    modified: list[pathlib.Path] = []

    for path, header in iter_source_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            print(f"WARN: skipping {path} ({e})", file=sys.stderr)
            continue

        if not content.strip():
            continue  # empty file

        if has_spdx(content):
            continue  # already has header

        missing.append(path)

        if args.apply:
            new_content = insert_header(content, header)
            path.write_text(new_content, encoding="utf-8")
            modified.append(path)

    rel_paths = [p.relative_to(root) for p in missing[: args.limit]]

    if args.check:
        if missing:
            print(f"FAIL: {len(missing)} file(s) missing SPDX-License-Identifier:")
            for rp in rel_paths:
                print(f"  {rp}")
            if len(missing) > args.limit:
                print(f"  ... and {len(missing) - args.limit} more")
            print(
                "\nRun `python scripts/add_spdx_headers.py --apply` to fix, "
                "then commit the changes."
            )
            return 1
        print("OK: every source file carries SPDX-License-Identifier.")
        return 0

    if args.apply:
        print(f"Added SPDX header to {len(modified)} file(s).")
        for rp in [p.relative_to(root) for p in modified[: args.limit]]:
            print(f"  + {rp}")
        if len(modified) > args.limit:
            print(f"  ... and {len(modified) - args.limit} more")
        return 0

    # default: dry-run
    if not missing:
        print("OK: every source file already carries SPDX-License-Identifier.")
        return 0

    print(f"DRY RUN: {len(missing)} file(s) would receive an SPDX header:")
    for rp in rel_paths:
        print(f"  + {rp}")
    if len(missing) > args.limit:
        print(f"  ... and {len(missing) - args.limit} more")
    print("\nRe-run with `--apply` to actually modify the files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
