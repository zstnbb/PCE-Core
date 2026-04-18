#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Enforce the Open Core dependency-direction rule on the OSS `pce` repository.

The OSS edition (this repo) must NEVER import Pro modules. Pro modules
communicate with OSS via local HTTP (POST /api/v1/captures/v2), not in-process
calls. Violating this rule would make the OSS edition non-standalone and break
the Open Core promise (see ADR-010).

Forbidden import prefixes:
    - pce_agent_kernel
    - pce_agent_frida
    - pce_agent_electron
    - pce_agent_ax
    - pce_core.capture_supervisor

Usage:
    python scripts/check_import_direction.py              # scan whole repo
    python scripts/check_import_direction.py path1 path2  # scan specific paths

Exit codes:
    0 = clean
    1 = violations found
    2 = usage / runtime error
"""
from __future__ import annotations

import ast
import pathlib
import sys
from typing import Iterable, NamedTuple


FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "pce_agent_kernel",
    "pce_agent_frida",
    "pce_agent_electron",
    "pce_agent_ax",
    "pce_core.capture_supervisor",
)

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
    ".idea",
    ".vscode",
    "Docs",
}


class Violation(NamedTuple):
    path: pathlib.Path
    line: int
    module: str


def is_forbidden(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in FORBIDDEN_PREFIXES
    )


def scan_file(path: pathlib.Path) -> list[Violation]:
    violations: list[Violation] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        print(f"WARN: could not read {path} ({e})", file=sys.stderr)
        return violations

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"WARN: syntax error in {path}: {e}", file=sys.stderr)
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if is_forbidden(alias.name):
                    violations.append(Violation(path, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module and is_forbidden(node.module):
                violations.append(Violation(path, node.lineno, node.module))

    return violations


def is_excluded(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in EXCLUDE_DIRS for part in rel.parts)


def iter_python_files(root: pathlib.Path) -> Iterable[pathlib.Path]:
    for path in root.rglob("*.py"):
        if not path.is_file():
            continue
        if is_excluded(path, root):
            continue
        yield path


def main(argv: list[str]) -> int:
    targets: list[pathlib.Path]
    if len(argv) > 1:
        targets = [pathlib.Path(a).resolve() for a in argv[1:]]
        for t in targets:
            if not t.exists():
                print(f"ERROR: path does not exist: {t}", file=sys.stderr)
                return 2
    else:
        targets = [pathlib.Path.cwd().resolve()]

    all_violations: list[Violation] = []
    scanned = 0

    for target in targets:
        if target.is_file():
            if target.suffix == ".py":
                all_violations.extend(scan_file(target))
                scanned += 1
        else:
            for py_file in iter_python_files(target):
                all_violations.extend(scan_file(py_file))
                scanned += 1

    if not all_violations:
        print(f"OK: import-direction clean across {scanned} Python file(s).")
        return 0

    print(f"FAIL: {len(all_violations)} forbidden import(s) detected:\n")
    for v in all_violations:
        try:
            rel = v.path.relative_to(pathlib.Path.cwd())
        except ValueError:
            rel = v.path
        print(f"  {rel}:{v.line}  imports  {v.module}")

    print(
        "\nThe OSS `pce` repository must never import Pro modules "
        "(pce_agent_*, pce_core.capture_supervisor). "
        "Pro modules are in the separate `pce-pro` private repository and "
        "communicate with OSS via local HTTP. See ADR-010 and CONTRIBUTING.md."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
