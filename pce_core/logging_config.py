"""PCE Core – Structured logging configuration.

Provides:
- A JSON formatter that emits one-line JSON records for machine consumption
- A classic human-readable formatter as fallback
- ``configure_logging()`` that picks format from env (``PCE_LOG_JSON=1``)
- ``log_event(logger, event, **fields)`` for structured event logs

The JSON mode is intended for production / sidecar / CI use where logs
need to be parsed. Human mode is default for local development so
existing ``python -m pce_core.server`` sessions keep their familiar output.

No third-party dependencies are used – only the standard library.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

# Fields that the stdlib LogRecord already populates; anything else on the
# record's __dict__ is treated as structured context and serialised.
_LOGRECORD_BUILTIN_ATTRS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
    # our own convention — handled explicitly below
    "event", "pce_fields",
}


class JsonFormatter(logging.Formatter):
    """Format log records as one-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _iso_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Event name takes precedence over msg when present.
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event

        # Structured fields attached via extra=
        extra_fields = getattr(record, "pce_fields", None)
        if isinstance(extra_fields, dict):
            for k, v in extra_fields.items():
                if k not in payload:
                    payload[k] = _json_safe(v)

        # Any other extras passed via extra= appear directly on __dict__
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_BUILTIN_ATTRS:
                continue
            if key.startswith("_"):
                continue
            if key in payload:
                continue
            payload[key] = _json_safe(value)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = record.stack_info

        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            # Never let a log formatting failure crash the process.
            safe = {k: str(v) for k, v in payload.items()}
            return json.dumps(safe, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Classic PCE format, kept for local development friendliness."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        event = getattr(record, "event", None)
        extras = getattr(record, "pce_fields", None)
        if event or extras:
            tag = event or ""
            if isinstance(extras, dict) and extras:
                kv = " ".join(f"{k}={_short(v)}" for k, v in extras.items())
                return f"{base} [{tag} {kv}]" if tag else f"{base} [{kv}]"
            if tag:
                return f"{base} [{tag}]"
        return base


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CONFIGURED = False


def configure_logging(
    *,
    level: str | int | None = None,
    json_mode: bool | None = None,
    force: bool = False,
) -> None:
    """Install PCE's root logging handler.

    Idempotent unless ``force=True``.

    Priority for ``level``:  arg > env ``PCE_LOG_LEVEL`` > ``INFO``
    Priority for ``json_mode``: arg > env ``PCE_LOG_JSON`` > False

    Existing handlers on the root logger are replaced. This is intentional:
    sub-modules (addon, MCP) previously called ``logging.basicConfig`` which
    only installs handlers when there are none, so they silently no-op once
    the server has run first.  We want one consistent formatter everywhere.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    effective_level = _resolve_level(level)
    effective_json = _resolve_json(json_mode)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter() if effective_json else HumanFormatter())

    root = logging.getLogger()
    # Remove previously installed handlers from a prior basicConfig call, but
    # keep any user-installed handlers (e.g. tests that wired their own).
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(
            h.formatter, (JsonFormatter, HumanFormatter)
        ):
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(effective_level)

    _CONFIGURED = True

    # Emit a single bootstrap record so every run logs how it was configured.
    logging.getLogger("pce.logging").info(
        "logging configured",
        extra={"event": "logging.configured", "pce_fields": {
            "json_mode": effective_json,
            "level": logging.getLevelName(effective_level),
        }},
    )


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    msg: str | None = None,
    **fields: Any,
) -> None:
    """Emit a structured event log record.

    In JSON mode this produces a single JSON line with ``event`` + fields.
    In human mode the event name and fields are appended to a readable line.

    Example:
        log_event(logger, "capture.persisted", pair_id=pair[:8], source="proxy")
    """
    payload_msg = msg or event
    logger.log(
        level,
        payload_msg,
        extra={"event": event, "pce_fields": fields},
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_utc(epoch_s: float) -> str:
    """Return ISO-8601 UTC timestamp with millisecond precision."""
    t = time.gmtime(epoch_s)
    ms = int((epoch_s - int(epoch_s)) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", t) + f".{ms:03d}Z"


def _json_safe(value: Any) -> Any:
    """Coerce arbitrary values to JSON-serialisable forms."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    try:
        return str(value)
    except Exception:
        return "<unrepr>"


def _short(value: Any, limit: int = 120) -> str:
    try:
        s = str(value)
    except Exception:
        return "<unrepr>"
    return s if len(s) <= limit else s[:limit] + "…"


def _resolve_level(level: str | int | None) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str) and level:
        return logging.getLevelName(level.upper())  # type: ignore[return-value]
    env_level = os.environ.get("PCE_LOG_LEVEL", "").strip().upper()
    if env_level:
        resolved = logging.getLevelName(env_level)
        if isinstance(resolved, int):
            return resolved
    return logging.INFO


def _resolve_json(json_mode: bool | None) -> bool:
    if json_mode is not None:
        return bool(json_mode)
    raw = os.environ.get("PCE_LOG_JSON", "").strip().lower()
    return raw in ("1", "true", "yes", "on")
