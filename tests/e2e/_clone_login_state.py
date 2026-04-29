# SPDX-License-Identifier: Apache-2.0
"""Clone Cloudflare clearance + login state from your everyday Chrome
profile into the managed ``~/.pce/chrome_profile`` user-data dir so
``_open_login_tabs.py`` can skip the Claude / Grok Cloudflare
challenge and inherit existing logged-in sessions.

Why this exists
---------------
Sites like Claude (``claude.ai``) and Grok (``grok.com``) sit behind a
Cloudflare Turnstile / JS-challenge gate. A fresh, never-used Chrome
profile rarely passes the challenge cleanly -- stealth patches help,
but the persistent ``cf_clearance`` cookie (earned by surviving the
challenge once, bound to fingerprint + IP) is still the most reliable
signal Cloudflare uses. Your everyday Chrome profile already has that
cookie plus a real login session for both sites; reusing those in the
managed test profile is the path of least resistance.

How it works
------------
Chrome encrypts cookie values with AES-256-GCM. The 32-byte key sits
in ``<user_data>/Local State`` JSON under ``os_crypt.encrypted_key``,
itself wrapped by Windows DPAPI bound to the current Windows user.
Because both the source profile and the destination managed profile
run as the same Windows user, copying ``Local State`` AND the
profile's ``Cookies`` SQLite is enough -- the destination Chrome can
re-decrypt the blobs with no re-encryption needed. We do NOT have to
implement DPAPI unwrap + AES-GCM decrypt + AES-GCM encrypt manually.

The Cookies SQLite is cloned via SQLite's online backup API so the
source Chrome can stay open during the clone (the snapshot is
point-in-time consistent). The ``Local State`` JSON file is small
and practically never partially-written, so a plain file copy is
fine.

Safety
------
- The destination's existing ``Local State`` + ``Cookies`` are moved
  to ``<dst>/.pce-revert/<timestamp>/`` so you can roll back.
- Any chrome.exe processes pinned to the destination profile are
  terminated first (reusing ``_reset_managed_profile`` logic).
- Source Chrome can stay running for ``--scope minimal``. For
  ``--scope full`` (which copies leveldb dirs via plain file copy)
  you should close the source profile's Chrome windows first.
- Without ``--apply``, runs as a dry-run.

Anchor + overlay model
----------------------
Real users keep different sites logged in to different Chrome
profiles -- e.g. ``Default`` for ChatGPT/Gemini, ``Profile 1`` for
Grok, ``Profile 4`` for Claude. To consolidate them into one test
browser this helper supports a two-phase clone:

1. **Anchor clone** (whole ``Cookies`` SQLite + ``Local State``) from
   one profile that holds the bulk of your logins. ``--source-profile``
   selects the anchor (``auto`` = ``Local State.profile.last_used``).
2. **Overlays** -- repeatable ``--overlay <site>=<profile>`` flags
   merge just the rows whose ``host_key`` matches that site's known
   domains, INSERT-OR-REPLACE into the anchor clone. All source
   profiles share one ``Local State`` master key, so cross-profile
   cookie rows decrypt correctly in the destination.

Usage
-----
    python -m tests.e2e._clone_login_state              # dry-run, prints plan
    python -m tests.e2e._clone_login_state --apply      # anchor-only clone
    python -m tests.e2e._clone_login_state --apply \\
        --source-profile Default \\
        --overlay claude=LOL \\
        --overlay grok=Hurricane                          # the typical case
    python -m tests.e2e._clone_login_state --apply \\
        --source-profile "Profile 1" --scope full

Flags
-----
    --source-user-data <path>   Override default %LOCALAPPDATA%/Google/Chrome/User Data
    --source-profile <name>     Anchor profile dir or display name; 'auto' = last_used
    --overlay <site>=<profile>  Merge a single site's cookies from another profile;
                                repeatable. <profile> accepts dir name OR display
                                name (e.g. 'Profile 4' or 'LOL'). <site> must be
                                a key of SITE_HOST_PATTERNS below.
    --dst-user-data <path>      Override default ~/.pce/chrome_profile
    --scope minimal|full        minimal: Local State + Cookies (default; safe while source Chrome is open)
                                full:    + leveldb / Login Data / IndexedDB / Service Worker DB
                                         (REQUIRES source Chrome closed)
    --keep-extensions           Don't strip cloned profile's installed extensions
                                (default: strip them so they don't fight the PCE extension)
    --apply                     Actually perform the clone. Without this flag, dry-run only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Make absolute imports work whether invoked as
# ``python tests/e2e/_clone_login_state.py`` or as a module.
if __package__ in (None, ""):
    _here = Path(__file__).resolve()
    sys.path.insert(0, str(_here.parent.parent.parent))
    __package__ = "tests.e2e"

from tests.e2e._reset_managed_profile import (  # noqa: E402
    _find_open_login_tabs_pids,
    _find_pce_chrome_pids,
    _kill_pids,
    _remove_lockfiles,
)

DEFAULT_SRC_USER_DATA = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    / "Google" / "Chrome" / "User Data"
)
DEFAULT_DST_USER_DATA = Path.home() / ".pce" / "chrome_profile"
# ``_open_login_tabs.py`` launches Chrome without ``--profile-directory``,
# so the dest profile is always the implicit ``Default``.
DST_PROFILE_DIR_NAME = "Default"

# SCOPE_FULL extras: paths under <profile> that hold session-state
# beyond cookies. Plain file copy -- requires source Chrome closed for
# leveldb consistency. Service Worker/CacheStorage is intentionally
# omitted (just response cache; bloats the copy).
COPY_PLAN_FULL_EXTRAS = [
    "Local Storage/leveldb",
    "Session Storage",
    "IndexedDB",
    "Login Data",
    "Login Data-journal",
    "Login Data For Account",
    "Login Data For Account-journal",
    "Web Data",
    "Web Data-journal",
    "Service Worker/Database",
    "Service Worker/ScriptCache",
]

# Per-site host_key patterns used by ``--overlay``. The dest cookies
# DB is queried with: host_key = pattern OR host_key = '.'+pattern OR
# host_key LIKE '%.'+pattern, so subdomain + bare-domain + dot-prefix
# variants all match. Keep this in sync with SITES in
# ``_open_login_tabs.py``.
SITE_HOST_PATTERNS: dict[str, list[str]] = {
    "chatgpt":        ["chatgpt.com", "openai.com", "oaiusercontent.com"],
    "claude":         ["claude.ai", "anthropic.com"],
    "gemini":         ["gemini.google.com", "bard.google.com"],
    "googleaistudio": ["aistudio.google.com", "makersuite.google.com"],
    "perplexity":     ["perplexity.ai"],
    "copilot":        ["copilot.microsoft.com", "bing.com"],
    "grok":           ["grok.com", "x.ai", "x.com"],
    "deepseek":       ["chat.deepseek.com", "deepseek.com"],
    "huggingface":    ["huggingface.co", "hf.co"],
    "poe":            ["poe.com"],
    "kimi":           ["kimi.com", "moonshot.cn"],
    "zhipu":          ["chatglm.cn", "zhipuai.cn", "bigmodel.cn"],
    "mistral":        ["mistral.ai"],
    "manus":          ["manus.im"],
    "m365-copilot":   ["m365.cloud.microsoft", "office.com", "office365.com"],
    "notion":         ["notion.so", "notion.com"],
    "gmail":          ["mail.google.com", "accounts.google.com"],
    "figma":          ["figma.com"],
}


def _chrome_using_source(src_user_data: Path) -> list[int]:
    """Return PIDs of chrome.exe processes that hold the source User
    Data root (browser process + renderers + helpers all reference it
    on their cmdline). On Windows these processes hold an exclusive
    file-share lock on the Cookies SQLite; SQLite cannot open the
    file until they release it -- not even with ``mode=ro``,
    ``immutable=1`` or ``nolock=1``, because the lock is at the
    Windows file-system layer, not SQLite's byte-range layer.

    The *only* clean fix on a non-admin Windows session is to quit the
    source Chrome, so the helper aborts with clear instructions when
    this returns non-empty.
    """
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return []
    needle = str(src_user_data).lower()
    pids: list[int] = []
    for proc in psutil.process_iter(attrs=["name", "cmdline"]):
        info = proc.info
        if (info.get("name") or "").lower() != "chrome.exe":
            continue
        cmdline = info.get("cmdline") or []
        if any(needle in (c or "").lower() for c in cmdline):
            pids.append(proc.pid)
    return pids


def _read_local_state(local_state_path: Path) -> dict:
    return json.loads(local_state_path.read_text(encoding="utf-8"))


def _list_source_profiles(src_user_data: Path) -> list[tuple[str, str]]:
    """Return [(profile_dir, display_name), ...] from ``Local State``."""
    ls = src_user_data / "Local State"
    if not ls.is_file():
        return []
    try:
        cache = _read_local_state(ls).get("profile", {}).get("info_cache", {})
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for prof_dir, meta in cache.items():
        if (src_user_data / prof_dir).is_dir():
            out.append((prof_dir, meta.get("name") or prof_dir))
    return out


def _resolve_source_profile(
    src_user_data: Path, requested: str | None,
) -> tuple[str, str]:
    """Return (profile_dir, display_name)."""
    profiles = _list_source_profiles(src_user_data)
    if not profiles:
        raise SystemExit(
            f"No usable profiles found under {src_user_data}. "
            "Is Google Chrome installed?"
        )
    if requested in (None, "", "auto"):
        try:
            last_used = (
                _read_local_state(src_user_data / "Local State")
                .get("profile", {})
                .get("last_used", "")
            )
        except Exception:
            last_used = ""
        for prof_dir, name in profiles:
            if prof_dir == last_used:
                return (prof_dir, name)
        return profiles[0]
    # Explicit name -- match either dir token or display name.
    for prof_dir, name in profiles:
        if requested == prof_dir or requested == name:
            return (prof_dir, name)
    raise SystemExit(
        f"Source profile {requested!r} not found. Available:\n"
        + "\n".join(f"  {d!r}  ({n})" for d, n in profiles)
    )


def _backup_existing_dst(dst_user_data: Path) -> Path | None:
    """Move existing dest ``Local State`` + ``Default/Cookies*`` to a
    timestamped backup folder so the user can revert.
    """
    targets = [
        dst_user_data / "Local State",
        dst_user_data / DST_PROFILE_DIR_NAME / "Cookies",
        dst_user_data / DST_PROFILE_DIR_NAME / "Cookies-journal",
        dst_user_data / DST_PROFILE_DIR_NAME / "Network" / "Cookies",
        dst_user_data / DST_PROFILE_DIR_NAME / "Network" / "Cookies-journal",
    ]
    targets = [p for p in targets if p.exists()]
    if not targets:
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = dst_user_data / ".pce-revert" / stamp
    for src in targets:
        rel = src.relative_to(dst_user_data)
        dst = backup_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return backup_root


def _sqlite_online_backup(src: Path, dst: Path) -> None:
    """Use SQLite's online backup API to clone src -> dst safely even
    if src is currently open by Chrome.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        # backup() onto an existing DB merges schemas, which we don't
        # want -- we want a faithful replica of source. Remove first.
        try:
            dst.unlink()
        except OSError:
            pass
    # ``Path.as_uri()`` URL-encodes spaces ("User Data" -> "User%20Data")
    # and produces ``file:///C:/...`` which SQLite's URI parser
    # accepts. Concatenating the raw Windows path produces an invalid
    # URI ("file:C:\Users\ZST\AppData\Local\Google\Chrome\User Data\..."
    # fails with "unable to open database file").
    src_conn = sqlite3.connect(
        f"{src.as_uri()}?mode=ro", uri=True, timeout=10.0,
    )
    dst_conn = sqlite3.connect(str(dst), timeout=10.0)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()


def _resolve_cookies_path(profile_dir: Path) -> tuple[Path | None, bool]:
    """Return (cookies_sqlite_path, is_under_network_subdir).

    Chrome 96+ migrated cookies to ``<profile>/Network/Cookies``;
    older versions kept ``<profile>/Cookies``. Both can co-exist
    transiently during a Chrome upgrade.
    """
    for candidate, in_net in (
        (profile_dir / "Network" / "Cookies", True),
        (profile_dir / "Cookies", False),
    ):
        if candidate.is_file():
            return candidate, in_net
    return None, False


def _overlay_cookies(
    src_cookies_db: Path,
    dst_cookies_db: Path,
    host_patterns: list[str],
    label: str,
) -> int:
    """Merge rows from ``src_cookies_db`` into ``dst_cookies_db`` whose
    ``host_key`` matches any of ``host_patterns``. Returns the number
    of rows merged. Schema must match between src and dst (we copied
    dst from another profile of the same Chrome install -- they
    always do).
    """
    # ``as_uri()`` URL-encodes spaces in the path; see comment in
    # ``_sqlite_online_backup`` for why concatenating raw paths fails.
    src = sqlite3.connect(
        f"{src_cookies_db.as_uri()}?mode=ro", uri=True, timeout=10.0,
    )
    dst = sqlite3.connect(str(dst_cookies_db), timeout=10.0)
    try:
        src_cols = [r[1] for r in src.execute("PRAGMA table_info(cookies)")]
        dst_cols = [r[1] for r in dst.execute("PRAGMA table_info(cookies)")]
        if not src_cols or not dst_cols:
            raise SystemExit(
                f"  {label}: 'cookies' table missing in src or dst."
            )
        if src_cols != dst_cols:
            # Take the intersection in src order so an INSERT works on
            # both sides (rare cross-version drift).
            common = [c for c in src_cols if c in dst_cols]
            print(
                f"  {label}: schema drift -- using {len(common)}/{len(src_cols)}"
                f" common columns: {common}"
            )
            cols = common
        else:
            cols = src_cols

        # Build WHERE clause covering bare-domain, dot-prefix and
        # subdomain forms for every pattern.
        clauses = []
        params: list[str] = []
        for d in host_patterns:
            clauses.append(
                "(host_key = ? OR host_key = '.' || ? OR "
                "host_key LIKE '%.' || ?)"
            )
            params.extend([d, d, d])
        sel = (
            f"SELECT {', '.join(cols)} FROM cookies WHERE "
            + " OR ".join(clauses)
        )
        rows = src.execute(sel, params).fetchall()
        if not rows:
            print(f"  {label}: no cookies matched in source profile.")
            return 0
        placeholders = ", ".join("?" * len(cols))
        ins = (
            f"INSERT OR REPLACE INTO cookies ({', '.join(cols)}) "
            f"VALUES ({placeholders})"
        )
        dst.executemany(ins, rows)
        dst.commit()
        print(
            f"  {label}: merged {len(rows)} cookie row(s) "
            f"(hosts: {sorted({r[cols.index('host_key')] for r in rows})})"
        )
        return len(rows)
    finally:
        src.close()
        dst.close()


def _copy_tree(src: Path, dst: Path) -> int:
    """Plain recursive copy. Returns number of files copied. Suitable
    only for targets that are not being mutated concurrently
    (leveldb dirs in --scope full).
    """
    if not src.exists():
        return 0
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return 1
    count = 0
    for path in src.rglob("*"):
        if path.is_file():
            rel = path.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(path), str(target))
                count += 1
            except (PermissionError, OSError) as exc:
                print(f"  WARN: skipped {rel}: {exc}", file=sys.stderr)
    return count


def _strip_extensions_in_dst(dst_profile_dir: Path) -> None:
    """Remove cloned profile's installed extensions so they don't
    fight the PCE extension we ``--load-extension`` into the test
    browser (e.g. uBlock blocking captured network calls).
    """
    for sub in (
        "Extensions", "Extension Settings", "Extension State",
        "Extension Rules", "Extension Scripts",
    ):
        target = dst_profile_dir / sub
        if target.exists():
            shutil.rmtree(str(target), ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Clone Chrome login + Cloudflare clearance state from a "
                    "real profile into the PCE managed test profile.",
    )
    p.add_argument(
        "--source-user-data", type=Path, default=DEFAULT_SRC_USER_DATA,
        help=f"Source User Data root (default: {DEFAULT_SRC_USER_DATA})",
    )
    p.add_argument(
        "--source-profile", default="auto",
        help='Anchor profile dir name (e.g. "Default", "Profile 1") or "auto" (last_used)',
    )
    p.add_argument(
        "--overlay", action="append", default=[],
        metavar="SITE=PROFILE",
        help="Merge cookies for a single site from another profile. "
             "Repeatable. Example: --overlay claude=LOL --overlay grok=Hurricane. "
             "PROFILE accepts dir name OR display name. "
             f"Valid SITE keys: {', '.join(sorted(SITE_HOST_PATTERNS))}",
    )
    p.add_argument(
        "--dst-user-data", type=Path, default=DEFAULT_DST_USER_DATA,
        help=f"Destination managed profile root (default: {DEFAULT_DST_USER_DATA})",
    )
    p.add_argument(
        "--scope", choices=["minimal", "full"], default="minimal",
        help="minimal: Local State + Cookies. full: + leveldb / Login Data / IndexedDB",
    )
    p.add_argument(
        "--keep-extensions", action="store_true",
        help="Don't strip cloned profile's installed extensions (default: strip them)",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="Actually perform the clone. Without this flag, runs in dry-run.",
    )
    args = p.parse_args(argv)

    src_ud: Path = args.source_user_data
    dst_ud: Path = args.dst_user_data

    if not src_ud.is_dir():
        print(f"ERROR: source User Data dir not found: {src_ud}", file=sys.stderr)
        return 2
    if not (src_ud / "Local State").is_file():
        print(f"ERROR: missing 'Local State' under {src_ud}", file=sys.stderr)
        return 2

    src_profile_dir, src_profile_name = _resolve_source_profile(
        src_ud, args.source_profile,
    )
    src_profile = src_ud / src_profile_dir

    # Parse and resolve overlays (site=profile pairs).
    overlays: list[tuple[str, list[str], str, str, Path]] = []
    # Each entry: (site, host_patterns, profile_dir, profile_name, profile_path)
    for spec in args.overlay:
        if "=" not in spec:
            print(
                f"ERROR: --overlay must be SITE=PROFILE, got {spec!r}",
                file=sys.stderr,
            )
            return 2
        site, prof = spec.split("=", 1)
        site = site.strip().lower()
        prof = prof.strip()
        if site not in SITE_HOST_PATTERNS:
            print(
                f"ERROR: unknown overlay site {site!r}. Valid: "
                f"{sorted(SITE_HOST_PATTERNS)}",
                file=sys.stderr,
            )
            return 2
        ov_dir, ov_name = _resolve_source_profile(src_ud, prof)
        overlays.append((
            site, SITE_HOST_PATTERNS[site], ov_dir, ov_name, src_ud / ov_dir,
        ))

    print(f"Source User Data : {src_ud}")
    print(f"Anchor profile   : {src_profile_dir!r}  ({src_profile_name})")
    if overlays:
        print("Overlays         :")
        for site, _patterns, ov_dir, ov_name, _ in overlays:
            print(f"                   {site:<14} <- {ov_dir!r}  ({ov_name})")
    print(f"Dest   User Data : {dst_ud}")
    print(f"Dest   profile   : {DST_PROFILE_DIR_NAME!r}")
    print(f"Scope            : {args.scope}")

    # Pre-flight: source Chrome must NOT be running. Windows holds an
    # exclusive file-share lock on the Cookies SQLite while Chrome is
    # alive; SQLite open fails with "unable to open database file" no
    # matter what URI flags we pass. We refuse to proceed instead of
    # leaving dest in a half-cloned state.
    src_chrome_pids = _chrome_using_source(src_ud)
    if src_chrome_pids:
        print(
            f"\nERROR: your everyday Chrome is running ({len(src_chrome_pids)}"
            f" chrome.exe processes referencing the source User Data root)."
            f"\n       Sample PIDs: {src_chrome_pids[:8]}"
            "\n\nWindows locks the Cookies SQLite exclusively while Chrome is"
            " alive, so the clone cannot read it. Do this:"
            "\n  1. In Chrome: File menu > 'Exit'  (Ctrl+Shift+Q on most builds)"
            "\n     -- this saves your session; tabs restore on next launch."
            "\n  2. Wait until Task Manager shows 0 Google Chrome processes."
            "\n  3. Re-run this command. The clone takes ~3 seconds; after"
            "\n     the helper says 'Done.', you can re-launch your daily"
            "\n     Chrome and your tabs will restore.",
            file=sys.stderr,
        )
        return 4

    src_cookies, cookies_in_network = _resolve_cookies_path(src_profile)
    if src_cookies is None:
        print(
            f"ERROR: no Cookies SQLite under {src_profile} "
            "(checked Network/Cookies and Cookies). Has this anchor "
            "profile ever been used in Chrome?",
            file=sys.stderr,
        )
        return 2
    print(f"Anchor Cookies   : {src_cookies}")

    # Validate every overlay profile has a usable Cookies DB.
    overlay_dbs: dict[str, Path] = {}
    for site, _patterns, ov_dir, _ov_name, ov_path in overlays:
        ov_cookies, _ = _resolve_cookies_path(ov_path)
        if ov_cookies is None:
            print(
                f"ERROR: overlay profile {ov_dir!r} has no Cookies SQLite "
                f"under {ov_path}. Skip the overlay or pick a different profile.",
                file=sys.stderr,
            )
            return 2
        overlay_dbs[site] = ov_cookies

    # Build operation list.
    ops: list[tuple[str, Path, Path]] = []

    # 1. Local State (plain file copy).
    ops.append((
        "copy-file",
        src_ud / "Local State",
        dst_ud / "Local State",
    ))

    # 2. Cookies SQLite (online backup -> mirror source layout).
    if cookies_in_network:
        dst_cookies_target = dst_ud / DST_PROFILE_DIR_NAME / "Network" / "Cookies"
    else:
        dst_cookies_target = dst_ud / DST_PROFILE_DIR_NAME / "Cookies"
    ops.append(("sqlite-backup", src_cookies, dst_cookies_target))

    # 3. Full-scope extras.
    if args.scope == "full":
        for rel in COPY_PLAN_FULL_EXTRAS:
            src_p = src_profile / rel
            dst_p = dst_ud / DST_PROFILE_DIR_NAME / rel
            if src_p.exists():
                ops.append(("copy-tree", src_p, dst_p))

    # Print plan.
    print("\nPlanned operations:")
    for kind, s, d in ops:
        size = ""
        if s.is_file():
            size = f" ({s.stat().st_size // 1024} KB)"
        elif s.is_dir():
            try:
                bytes_total = sum(
                    p.stat().st_size for p in s.rglob("*") if p.is_file()
                )
                size = f" ({bytes_total // (1024 * 1024)} MB)"
            except Exception:
                pass
        print(f"  [{kind:>14}]{size}  {s}")
        print(f"  {'':>16}    -> {d}")
    for site, patterns, _ov_dir, _ov_name, _ in overlays:
        print(
            f"  [{'overlay-merge':>14}]  {overlay_dbs[site]}\n"
            f"  {'':>16}    -> {dst_cookies_target}  (host_key: {patterns})"
        )

    if not args.apply:
        print("\nDry-run complete. Re-run with --apply to perform the clone.")
        return 0

    if args.scope == "full":
        print(
            "\nNOTE: --scope full copies leveldb dirs via plain file copy. "
            "Make sure your everyday Chrome (source profile) is CLOSED "
            "before continuing -- otherwise the snapshot may be "
            "internally inconsistent."
        )
        for s in (3, 2, 1):
            print(f"  proceeding in {s} ...", end="\r", flush=True)
            time.sleep(1.0)
        print(" " * 40)

    # Stop dest Chrome so we can safely write into the dest profile.
    print("\nStopping destination managed-profile Chrome / babysitters ...")
    babysitters = _find_open_login_tabs_pids()
    if babysitters:
        print(f"  killing {len(babysitters)} _open_login_tabs babysitter(s): {babysitters}")
        _kill_pids(babysitters)
    pids = _find_pce_chrome_pids()
    if pids:
        print(f"  killing {len(pids)} chrome.exe on dest profile: {pids}")
        _kill_pids(pids)
    _remove_lockfiles()
    # Brief pause so OS releases file handles before we copy.
    time.sleep(1.0)

    # Backup existing dest state for revert.
    backup_root = _backup_existing_dst(dst_ud)
    if backup_root is not None:
        print(f"  backed up existing dest state -> {backup_root}")

    # Apply anchor ops first so the dest cookies DB exists before
    # overlays merge into it.
    print("\nApplying anchor ops ...")
    for kind, s, d in ops:
        d.parent.mkdir(parents=True, exist_ok=True)
        if kind == "copy-file":
            shutil.copy2(str(s), str(d))
            print(f"  copied:   {s.name}")
        elif kind == "sqlite-backup":
            _sqlite_online_backup(s, d)
            print(f"  cloned:   {s.name} (sqlite online backup)")
        elif kind == "copy-tree":
            count = _copy_tree(s, d)
            print(f"  copied:   {s.name}/  ({count} files)")
        else:
            print(f"  SKIP unknown op: {kind}")

    # Apply overlays in declared order.
    if overlays:
        print("\nApplying overlays ...")
        for site, patterns, ov_dir, _ov_name, _ in overlays:
            _overlay_cookies(
                overlay_dbs[site], dst_cookies_target, patterns,
                label=f"{site:<10} from {ov_dir!r}",
            )

    # Defensive: even at scope=minimal we may have inherited extension
    # dirs from a previous run; strip them unless caller insists.
    if not args.keep_extensions:
        _strip_extensions_in_dst(dst_ud / DST_PROFILE_DIR_NAME)

    print(
        "\nDone. Re-launch the login helper to pick up the cloned state:\n"
        "    python -m tests.e2e._open_login_tabs\n"
    )
    if backup_root is not None:
        print(
            "If something goes wrong, revert with PowerShell:\n"
            f"    Get-ChildItem '{backup_root}' -Recurse -File | "
            f"ForEach-Object {{ "
            f"$rel = $_.FullName.Substring('{backup_root}'.Length + 1); "
            f"$dst = Join-Path '{dst_ud}' $rel; "
            f"New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null; "
            f"Move-Item -Force $_.FullName $dst }}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
