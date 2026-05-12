# SPDX-License-Identifier: Apache-2.0
"""Patch VS Code-family IDE ``settings.json`` to route through PCE mitmdump.

Cursor / Windsurf / VS Code all inherit VS Code's ``http.proxy`` /
``http.proxyStrictSSL`` / ``http.proxySupport`` settings, which control
the **extension host (Node main process) network**. The Chromium-renderer
``--proxy-server`` command-line flag only covers the renderer; the
extension host (Cursor agent, Codeium client, Copilot extension) uses
Node ``http(s).request`` and respects only the ``http.proxy`` setting.

Without this patch, IDE-class apps split their traffic:

  renderer        ──→ system proxy / --proxy-server ──→ mitmdump  ✅
  extension host  ──→ direct (Clash fake-IP)        ──→ ❌ invisible

…and the chat / agent / completion calls land in the second bucket.

Usage::

    from scripts.harvest.patch_ide_settings import patch_app, restore_app

    patch_app("cursor", "http://127.0.0.1:8080")
    # … run harvest …
    restore_app("cursor")

CLI::

    python scripts/harvest/patch_ide_settings.py --app cursor --proxy http://127.0.0.1:8080
    python scripts/harvest/patch_ide_settings.py --app cursor --restore
    python scripts/harvest/patch_ide_settings.py --app all --proxy http://127.0.0.1:8080
    python scripts/harvest/patch_ide_settings.py --status

JSONC (JSON-with-comments) is handled gracefully: line and block
comments are stripped before parsing, but written back as plain JSON.
A copy of the original file is preserved in
``<workdir>/_harvest_<app>_settings_backup.json`` so ``--restore``
recovers it bit-for-bit (including comments).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


logger = logging.getLogger("pce.harvest.patch_ide")


# ---------------------------------------------------------------------------
# App registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IDEPaths:
    """Where one IDE keeps its user settings on Windows."""

    name: str                 # short id ("cursor", "windsurf", "vscode")
    display_name: str         # for logs
    settings_path: Path       # absolute path to settings.json
    backup_filename: str      # stem written under workdir (gitignored)


def _windows_paths() -> dict[str, IDEPaths]:
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return {
        "cursor": IDEPaths(
            name="cursor",
            display_name="Cursor",
            settings_path=appdata / "Cursor" / "User" / "settings.json",
            backup_filename="_harvest_cursor_settings_backup.json",
        ),
        "windsurf": IDEPaths(
            name="windsurf",
            display_name="Windsurf",
            settings_path=appdata / "Windsurf" / "User" / "settings.json",
            backup_filename="_harvest_windsurf_settings_backup.json",
        ),
        "vscode": IDEPaths(
            name="vscode",
            display_name="VS Code",
            settings_path=appdata / "Code" / "User" / "settings.json",
            backup_filename="_harvest_vscode_settings_backup.json",
        ),
    }


def get_ide_paths(app_id: str) -> IDEPaths:
    paths = _windows_paths()
    if app_id not in paths:
        raise SystemExit(f"Unknown app {app_id!r}. Available: {sorted(paths)}")
    return paths[app_id]


# ---------------------------------------------------------------------------
# JSONC helpers (tolerant parsing)
# ---------------------------------------------------------------------------


_LINE_COMMENT_RE = re.compile(r"(?<!:)//[^\n]*")        # // ... but not http:// inside strings
_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _strip_jsonc(raw: str) -> str:
    """Best-effort strip of JSONC comments + trailing commas.

    Not a full JSONC parser — but handles the 99% case for VS Code-family
    settings files. The regexes are conservative: ``//`` inside string
    literals can still cause issues, but VS Code's own settings.json
    very rarely has ``//`` inside string values.
    """
    raw = _BLOCK_COMMENT_RE.sub("", raw)
    raw = _LINE_COMMENT_RE.sub("", raw)
    raw = _TRAILING_COMMA_RE.sub(r"\1", raw)
    return raw


def _load_settings_or_empty(path: Path) -> dict:
    """Read settings.json (JSONC-tolerant). Empty/missing → empty dict."""
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8-sig")  # tolerate BOM
    except Exception as exc:
        logger.warning("could not read %s: %s — treating as empty", path, exc)
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(_strip_jsonc(raw))
    except json.JSONDecodeError as exc:
        logger.warning(
            "could not parse %s as JSON(C): %s — treating as empty (original preserved in backup)",
            path, exc,
        )
        return {}


def _atomic_write(path: Path, content: str) -> None:
    """Write file atomically: write to .tmp then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp_harvest")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Patch / restore
# ---------------------------------------------------------------------------


PROXY_KEYS = ("http.proxy", "http.proxyStrictSSL", "http.proxySupport")


def patch_app(
    app_id: str,
    proxy_url: str,
    workdir: Optional[Path] = None,
    *,
    strict_ssl: bool = False,
    proxy_support: str = "on",
) -> dict:
    """Patch one IDE's settings.json to route extension host through ``proxy_url``.

    Args:
        app_id: ``cursor`` / ``windsurf`` / ``vscode``.
        proxy_url: e.g. ``http://127.0.0.1:8080``.
        workdir: where to drop the backup file. Defaults to CWD.
        strict_ssl: value for ``http.proxyStrictSSL`` (default False — we are
            doing MITM so the upstream cert won't validate).
        proxy_support: value for ``http.proxySupport`` (default ``on``,
            forces the setting even when env var HTTP_PROXY is unset).

    Returns:
        Dict describing what happened (paths, prior values, new values).

    Raises:
        SystemExit on unknown ``app_id``.
    """
    paths = get_ide_paths(app_id)
    workdir = workdir or Path.cwd()
    backup_path = workdir / paths.backup_filename

    # 1. Read current
    settings = _load_settings_or_empty(paths.settings_path)
    raw_original = (
        paths.settings_path.read_text(encoding="utf-8-sig")
        if paths.settings_path.is_file()
        else ""
    )

    # 2. Backup original (raw, with comments intact)
    backup_payload = {
        "app": app_id,
        "original_path": str(paths.settings_path),
        "existed": paths.settings_path.is_file(),
        "raw_content": raw_original,
    }
    backup_path.write_text(json.dumps(backup_payload, indent=2), encoding="utf-8")
    logger.info("backed up %s → %s", paths.settings_path, backup_path)

    # 3. Capture prior values for diff
    prior = {k: settings.get(k) for k in PROXY_KEYS}

    # 4. Merge in proxy block (preserve everything else)
    new_block = {
        "http.proxy": proxy_url,
        "http.proxyStrictSSL": bool(strict_ssl),
        "http.proxySupport": proxy_support,
    }
    settings.update(new_block)

    # 5. Write back atomically
    _atomic_write(paths.settings_path, json.dumps(settings, indent=2))
    logger.info("patched %s with %s", paths.settings_path, new_block)

    return {
        "app": app_id,
        "display_name": paths.display_name,
        "settings_path": str(paths.settings_path),
        "backup_path": str(backup_path),
        "prior": prior,
        "new": new_block,
    }


def restore_app(app_id: str, workdir: Optional[Path] = None) -> dict:
    """Restore one IDE's settings.json from the backup taken by ``patch_app``.

    Returns:
        Dict with paths + status.
    """
    paths = get_ide_paths(app_id)
    workdir = workdir or Path.cwd()
    backup_path = workdir / paths.backup_filename

    if not backup_path.is_file():
        logger.warning("no backup file found at %s — nothing to restore", backup_path)
        return {"app": app_id, "status": "no_backup", "backup_path": str(backup_path)}

    try:
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("backup %s is unreadable: %s", backup_path, exc)
        return {"app": app_id, "status": "backup_unreadable", "backup_path": str(backup_path)}

    if not payload.get("existed", False):
        # Settings file didn't exist before; delete the patched one.
        if paths.settings_path.is_file():
            paths.settings_path.unlink()
            logger.info("deleted patched settings %s (file did not exist before)", paths.settings_path)
        backup_path.unlink()
        return {"app": app_id, "status": "deleted_new_file", "settings_path": str(paths.settings_path)}

    raw = payload.get("raw_content", "")
    _atomic_write(paths.settings_path, raw)
    backup_path.unlink()
    logger.info("restored %s from %s (and removed backup)", paths.settings_path, backup_path)
    return {"app": app_id, "status": "restored", "settings_path": str(paths.settings_path)}


def status(workdir: Optional[Path] = None) -> dict:
    """Report current state of every supported IDE's settings."""
    workdir = workdir or Path.cwd()
    out: dict = {}
    for app_id, paths in _windows_paths().items():
        s = _load_settings_or_empty(paths.settings_path)
        backup_path = workdir / paths.backup_filename
        out[app_id] = {
            "settings_path": str(paths.settings_path),
            "settings_exists": paths.settings_path.is_file(),
            "current_proxy": s.get("http.proxy"),
            "current_proxy_support": s.get("http.proxySupport"),
            "current_strict_ssl": s.get("http.proxyStrictSSL"),
            "backup_exists": backup_path.is_file(),
            "backup_path": str(backup_path),
        }
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--app",
        choices=["cursor", "windsurf", "vscode", "all"],
        help="Target IDE. 'all' applies to every supported IDE.",
    )
    p.add_argument(
        "--proxy",
        default="http://127.0.0.1:8080",
        help="Proxy URL to install (default http://127.0.0.1:8080 = local mitmdump).",
    )
    p.add_argument(
        "--strict-ssl",
        action="store_true",
        help="Set http.proxyStrictSSL=true (default false — required when mitmdump is in path).",
    )
    p.add_argument(
        "--restore",
        action="store_true",
        help="Restore from backup instead of patching.",
    )
    p.add_argument(
        "--status",
        action="store_true",
        help="Show current state of every supported IDE's settings.json.",
    )
    p.add_argument(
        "--workdir",
        type=Path,
        help="Where to store the backup file (default CWD).",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args()

    if args.status:
        st = status(args.workdir)
        print(json.dumps(st, indent=2))
        return 0

    if not args.app:
        print("ERROR: --app is required (use --status to inspect without changing).", file=sys.stderr)
        return 2

    apps = ["cursor", "windsurf", "vscode"] if args.app == "all" else [args.app]
    results = []
    for app in apps:
        try:
            if args.restore:
                results.append(restore_app(app, args.workdir))
            else:
                results.append(patch_app(app, args.proxy, args.workdir, strict_ssl=args.strict_ssl))
        except SystemExit:
            raise
        except Exception as exc:
            logger.exception("operation failed for %s", app)
            results.append({"app": app, "status": "error", "error": str(exc)})

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
