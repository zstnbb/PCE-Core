# SPDX-License-Identifier: Apache-2.0
"""PCE Core – `pce diagnose` one-shot diagnostics collector.

Purpose:
    Gather everything a developer needs to triage a user-reported bug into
    a single, self-contained, **redacted** ``.zip`` file.

Usage::

    # CLI
    python -m pce_core.diagnose                  # write ~/.pce/diagnose/pce-diag-<ts>.zip
    python -m pce_core.diagnose -o out.zip       # write to custom path
    python -m pce_core.diagnose --no-logs        # skip log scraping
    python -m pce_core.diagnose --stdout         # stream zip bytes to stdout

    # From the server (Tauri / dashboard uses this)
    GET /api/v1/diagnose             → application/zip download

    # From Python
    from pce_core.diagnose import collect_diagnostics
    path = collect_diagnostics()

What's included (everything is redacted of secrets first):

- ``summary.json``      overview: version, OS, arch, timestamps
- ``config.json``       PCE config snapshot (API keys / tokens removed)
- ``env.json``          env vars (allowlist; everything else → "REDACTED")
- ``health.json``       ``get_detailed_health()`` snapshot (same as /api/v1/health)
- ``stats.json``        row counts + breakdowns
- ``migrations.json``   applied migrations + expected version
- ``schema.sql``        live DDL from ``sqlite_master``
- ``packages.txt``      ``pip freeze``-style list of installed pkgs
- ``pipeline_errors.jsonl``  last 200 pipeline errors
- ``logs/*.log``        recent log files from ~/.pce/logs (if PCE_LOG_DIR set)
- ``README.md``         how to read the bundle

Nothing in here contains capture content bodies — only schema / counts /
error messages and redacted config. The goal is reproducibility, not data
export. Users who want to share captures should use
``GET /api/v1/export?format=otlp`` instead.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import platform
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

from . import __version__
from . import config as _config

logger = logging.getLogger("pce.diagnose")

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# Any env var whose name contains one of these substrings is redacted.
# Lower-cased for case-insensitive match.
_SECRET_NAME_MARKERS = (
    "key", "token", "secret", "password", "passwd", "auth", "cookie",
    "session", "credential", "cert_pass", "private", "pwd", "signature",
)

# Env vars we always allow through verbatim (useful for debugging; none of
# these ever carry secrets).
_ALLOW_ENV_PREFIXES = (
    "PCE_",
    "OTEL_SERVICE_NAME",  # Service identity — safe.
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    # Runtime / platform — useful to see, never secret.
    "PATH_INFO", "PYTHON", "VIRTUAL_ENV", "CONDA_", "PWD", "USER", "HOME",
    "LANG", "LC_", "TZ", "SHELL", "TERM", "OS", "USERPROFILE", "APPDATA",
    "LOCALAPPDATA", "COMPUTERNAME", "PROCESSOR_ARCHITECTURE",
)

# Header names we never include in the diagnose bundle, even if config
# happens to expose them as defaults.
_REDACT_KEY_PATTERN = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|cookie|credential|bearer)",
    re.IGNORECASE,
)

# Soft cap on the number of bytes we pull per log file (tail from the end).
_LOG_TAIL_BYTES = 256 * 1024
_PIPELINE_ERRORS_LIMIT = 200


def _is_secret_env_name(name: str) -> bool:
    lo = name.lower()
    return any(m in lo for m in _SECRET_NAME_MARKERS)


def _is_allowed_env_name(name: str) -> bool:
    for p in _ALLOW_ENV_PREFIXES:
        if name == p or name.startswith(p):
            return True
    return False


def _redact_env(env: dict[str, str]) -> dict[str, str]:
    """Return an env dict with secrets replaced by ``REDACTED``."""
    out: dict[str, str] = {}
    for k, v in sorted(env.items()):
        if _is_secret_env_name(k):
            out[k] = "REDACTED"
            continue
        if _is_allowed_env_name(k):
            out[k] = v
        # Everything else is dropped entirely (opt-in instead of opt-out).
    return out


def _redact_dict(obj: Any) -> Any:
    """Recursively replace values for keys that look like secrets."""
    if isinstance(obj, dict):
        red: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _REDACT_KEY_PATTERN.search(k):
                red[k] = "REDACTED"
            else:
                red[k] = _redact_dict(v)
        return red
    if isinstance(obj, list):
        return [_redact_dict(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_summary() -> dict[str, Any]:
    """Static, fast-to-collect facts about the environment."""
    return {
        "pce_version": __version__,
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "argv": sys.argv,
        "pid": os.getpid(),
        "cwd": os.getcwd(),
    }


def collect_config() -> dict[str, Any]:
    """Snapshot of :mod:`pce_core.config` values that are safe to share."""
    # Only pick the module-level names the rest of the codebase treats as
    # canonical. Anything else (helpers, private) is ignored.
    keys = (
        "CAPTURE_MODE", "DATA_DIR", "DB_PATH",
        "PROXY_LISTEN_HOST", "PROXY_LISTEN_PORT",
        "INGEST_HOST", "INGEST_PORT",
        "RETENTION_DAYS", "RETENTION_MAX_ROWS", "RETENTION_INTERVAL_HOURS",
        "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_SERVICE_NAME",
    )
    out: dict[str, Any] = {}
    for key in keys:
        raw = getattr(_config, key, None)
        if raw is None:
            out[key] = None
        elif hasattr(raw, "value"):  # Enum
            out[key] = raw.value
        else:
            out[key] = str(raw) if isinstance(raw, Path) else raw

    # ALLOWED_HOSTS is a set; serialise as a sorted list.
    hosts = getattr(_config, "ALLOWED_HOSTS", None)
    if hosts is not None:
        out["ALLOWED_HOSTS"] = sorted(hosts)

    # Also include the set of local model ports (debugging hook discovery).
    ports = getattr(_config, "LOCAL_MODEL_PORTS", None)
    if ports is not None:
        out["LOCAL_MODEL_PORTS"] = sorted(ports)

    return _redact_dict(out)


def collect_env() -> dict[str, str]:
    """Redacted environment-variable snapshot."""
    return _redact_env(dict(os.environ))


def collect_health(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Detailed health snapshot; tolerates a locked or missing DB."""
    try:
        from .db import get_detailed_health
        return get_detailed_health(db_path=db_path)
    except Exception as exc:  # pragma: no cover – best-effort collector
        logger.debug("collect_health failed: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


def collect_stats(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Row-count summary (captures / sessions / storage)."""
    try:
        from .db import get_stats
        return get_stats(db_path=db_path)
    except Exception as exc:  # pragma: no cover
        logger.debug("collect_stats failed: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


def collect_migrations(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Schema version + full applied-migration history."""
    try:
        from .db import get_connection
        from .migrations import (
            EXPECTED_SCHEMA_VERSION,
            discover_migrations,
            get_current_version,
            get_migration_history,
        )
        conn = get_connection(db_path)
        try:
            current = get_current_version(conn)
            history = get_migration_history(conn)
        finally:
            conn.close()
        available = [
            {"version": m.version, "name": m.name}
            for m in discover_migrations()
        ]
        return {
            "expected_version": EXPECTED_SCHEMA_VERSION,
            "current_version": current,
            "up_to_date": current == EXPECTED_SCHEMA_VERSION,
            "applied": history,
            "available": available,
        }
    except Exception as exc:
        logger.debug("collect_migrations failed: %s", exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


def collect_schema(db_path: Optional[Path] = None) -> str:
    """Return the live DDL of every table / index / trigger."""
    try:
        from .db import get_connection
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE sql IS NOT NULL ORDER BY type, name"
            ).fetchall()
        finally:
            conn.close()
        parts: list[str] = []
        for row in rows:
            rtype, rname, sql = row[0], row[1], row[2]
            parts.append(f"-- {rtype}: {rname}\n{sql};\n")
        return "\n".join(parts) if parts else "-- empty database\n"
    except Exception as exc:
        return f"-- schema collection failed: {type(exc).__name__}: {exc}\n"


def collect_packages() -> str:
    """Emit a ``pip freeze``-equivalent without requiring pip."""
    try:
        try:
            from importlib import metadata as _md  # py3.8+
        except ImportError:  # pragma: no cover
            import importlib_metadata as _md  # type: ignore
        dists = sorted(
            ((d.metadata["Name"] or "?", d.version or "?") for d in _md.distributions()),
            key=lambda t: t[0].lower(),
        )
        return "\n".join(f"{n}=={v}" for n, v in dists) + "\n"
    except Exception as exc:
        return f"# package collection failed: {type(exc).__name__}: {exc}\n"


def collect_pipeline_errors(
    db_path: Optional[Path] = None,
    limit: int = _PIPELINE_ERRORS_LIMIT,
) -> list[dict[str, Any]]:
    """Last ``limit`` rows of the ``pipeline_errors`` table (may be empty)."""
    try:
        from .db import get_connection
        conn = get_connection(db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Table is optional (legacy DBs). Use a guarded query.
            try:
                rows = conn.execute(
                    "SELECT * FROM pipeline_errors "
                    "ORDER BY ts DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("collect_pipeline_errors failed: %s", exc)
        return []


def collect_logs(log_dirs: Optional[Iterable[Path]] = None) -> list[tuple[str, bytes]]:
    """Return ``(arcname, bytes)`` pairs for each recent log file found.

    We only read the tail of each log (see :data:`_LOG_TAIL_BYTES`) so the
    bundle doesn't blow up for long-running installs.
    """
    if log_dirs is None:
        candidates: list[Path] = []
        env_dir = os.environ.get("PCE_LOG_DIR", "").strip()
        if env_dir:
            candidates.append(Path(env_dir))
        candidates.append(_config.DATA_DIR / "logs")
        candidates.append(Path.home() / ".pce" / "logs")
        log_dirs = candidates

    seen: set[Path] = set()
    out: list[tuple[str, bytes]] = []
    for d in log_dirs:
        try:
            p = Path(d).expanduser().resolve()
        except Exception:
            continue
        if not p.exists() or not p.is_dir() or p in seen:
            continue
        seen.add(p)
        # Walk only one level deep — logs/ is expected to be flat.
        for entry in sorted(p.iterdir(), key=lambda e: e.name):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in (".log", ".txt", ".jsonl"):
                continue
            try:
                size = entry.stat().st_size
                with entry.open("rb") as fh:
                    if size > _LOG_TAIL_BYTES:
                        fh.seek(size - _LOG_TAIL_BYTES)
                        # Drop the partial leading line so the tail parses cleanly.
                        data = fh.read()
                        nl = data.find(b"\n")
                        if nl >= 0:
                            data = data[nl + 1:]
                    else:
                        data = fh.read()
                out.append((f"logs/{entry.name}", data))
            except Exception as exc:
                out.append((
                    f"logs/{entry.name}.error",
                    f"read failed: {type(exc).__name__}: {exc}".encode("utf-8"),
                ))
    return out


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------

_README_TEMPLATE = """# PCE Diagnose Bundle

Generated: {generated_at_iso}
PCE version: {pce_version}
Platform: {system} {release} ({machine})
Python: {python_version} ({implementation})

## Contents

- `summary.json`          runtime / platform facts
- `config.json`           PCE configuration (redacted)
- `env.json`              environment variables (allowlisted + redacted)
- `health.json`           detailed health snapshot (same as /api/v1/health)
- `stats.json`            row-count summary
- `migrations.json`       current schema version + applied migrations
- `schema.sql`            live DDL from the SQLite database
- `packages.txt`          installed Python packages
- `pipeline_errors.jsonl` last {pipeline_errors_limit} pipeline errors
- `logs/`                 tail of each recent PCE log file

## Privacy

This bundle was redacted before zipping:
- environment variables with names containing key/token/secret/password/auth
  are replaced with `REDACTED`
- config values whose key name looks like a secret are likewise replaced
- only allowlisted env var prefixes (PCE_*, OTEL_*, …) are kept at all
- capture payload bodies are **not** included. Use
  `GET /api/v1/export?format=otlp` if you want to share conversation data.

Open the bundle with any unzip tool and inspect before forwarding.
"""


def _write_json(zf: zipfile.ZipFile, arcname: str, data: Any) -> None:
    payload = json.dumps(data, indent=2, sort_keys=False, default=str, ensure_ascii=False)
    zf.writestr(arcname, payload)


def _write_jsonl(zf: zipfile.ZipFile, arcname: str, rows: Iterable[dict[str, Any]]) -> None:
    buf = io.StringIO()
    for row in rows:
        buf.write(json.dumps(row, default=str, ensure_ascii=False))
        buf.write("\n")
    zf.writestr(arcname, buf.getvalue())


def build_bundle(
    buf: io.IOBase,
    *,
    include_logs: bool = True,
    db_path: Optional[Path] = None,
    log_dirs: Optional[Iterable[Path]] = None,
) -> dict[str, Any]:
    """Write a diagnose zip into ``buf``. Returns the ``summary.json`` dict."""
    summary = collect_summary()
    cfg = collect_config()
    env = collect_env()
    health = collect_health(db_path=db_path)
    stats = collect_stats(db_path=db_path)
    migrations = collect_migrations(db_path=db_path)
    schema = collect_schema(db_path=db_path)
    packages = collect_packages()
    errors = collect_pipeline_errors(db_path=db_path)

    readme = _README_TEMPLATE.format(
        generated_at_iso=summary["generated_at_iso"],
        pce_version=summary["pce_version"],
        system=summary["platform"]["system"],
        release=summary["platform"]["release"],
        machine=summary["platform"]["machine"],
        python_version=summary["python"]["version"],
        implementation=summary["python"]["implementation"],
        pipeline_errors_limit=_PIPELINE_ERRORS_LIMIT,
    )

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", readme)
        _write_json(zf, "summary.json", summary)
        _write_json(zf, "config.json", cfg)
        _write_json(zf, "env.json", env)
        _write_json(zf, "health.json", health)
        _write_json(zf, "stats.json", stats)
        _write_json(zf, "migrations.json", migrations)
        zf.writestr("schema.sql", schema)
        zf.writestr("packages.txt", packages)
        _write_jsonl(zf, "pipeline_errors.jsonl", errors)
        if include_logs:
            for arcname, data in collect_logs(log_dirs=log_dirs):
                zf.writestr(arcname, data)

    # Teach the caller what we produced so the tray / UI can show a toast.
    return {
        "pce_version": summary["pce_version"],
        "generated_at": summary["generated_at"],
        "generated_at_iso": summary["generated_at_iso"],
        "pipeline_errors_included": len(errors),
        "migrations_current": migrations.get("current_version"),
        "include_logs": include_logs,
    }


def collect_diagnostics(
    output_path: Optional[Path] = None,
    *,
    include_logs: bool = True,
    db_path: Optional[Path] = None,
    log_dirs: Optional[Iterable[Path]] = None,
) -> Path:
    """Build a diagnose bundle on disk. Returns the output path.

    Default location is ``{DATA_DIR}/diagnose/pce-diag-<epoch>.zip``.
    """
    if output_path is None:
        out_dir = _config.DATA_DIR / "diagnose"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        output_path = out_dir / f"pce-diag-{ts}.zip"
    else:
        output_path = Path(output_path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("wb") as fh:
        build_bundle(fh, include_logs=include_logs, db_path=db_path, log_dirs=log_dirs)
    logger.info(
        "diagnose bundle written",
        extra={"event": "diagnose.written", "pce_fields": {
            "path": str(output_path),
            "size_bytes": output_path.stat().st_size,
        }},
    )
    return output_path


def collect_diagnostics_bytes(
    *,
    include_logs: bool = True,
    db_path: Optional[Path] = None,
    log_dirs: Optional[Iterable[Path]] = None,
) -> bytes:
    """In-memory variant used by the HTTP endpoint."""
    buf = io.BytesIO()
    build_bundle(buf, include_logs=include_logs, db_path=db_path, log_dirs=log_dirs)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pce_core.diagnose",
        description="Collect a redacted PCE diagnostics bundle (.zip).",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file path. Default: ~/.pce/data/diagnose/pce-diag-<ts>.zip",
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="Skip collecting log files.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write the zip to stdout instead of a file (for piping).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Alternative SQLite DB path (default: PCE_DATA_DIR/pce.db).",
    )
    args = parser.parse_args(argv)

    if args.stdout:
        data = collect_diagnostics_bytes(
            include_logs=not args.no_logs,
            db_path=args.db,
        )
        # Emit bytes on the underlying buffer to avoid text/encoding mangling.
        sys.stdout.buffer.write(data)  # type: ignore[attr-defined]
        sys.stdout.flush()
        return 0

    path = collect_diagnostics(
        output_path=args.output,
        include_logs=not args.no_logs,
        db_path=args.db,
    )
    size_kb = path.stat().st_size / 1024.0
    print(f"Wrote diagnose bundle → {path} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
