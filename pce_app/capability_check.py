# SPDX-License-Identifier: Apache-2.0
"""Self-check probes for the PCE Desktop Control Panel.

Each probe is a pure function: it takes no PCE-internal state, returns
a :class:`CheckResult`, and never raises. The Control Panel polls them
on demand; they are deliberately cheap so the full sweep runs in well
under a second.

A probe answers one question: *can PCE use capability X right now?* —
not *is X configured perfectly*. Optional / off-by-default features
(Phoenix, DuckDB) report as INFO when missing, so the panel does not
shout at users who never asked for them.
"""

from __future__ import annotations

import importlib.util
import logging
import platform
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger("pce.capability_check")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str         # "ok" | "warn" | "error" | "info"
    detail: str = ""
    hint: str = ""


def check_python_version() -> CheckResult:
    v = sys.version_info
    label = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 10):
        return CheckResult("Python", "ok", label)
    return CheckResult(
        "Python", "error", f"{label} (need ≥ 3.10)",
        hint="Install Python 3.10 or newer",
    )


def check_mitmproxy() -> CheckResult:
    if importlib.util.find_spec("mitmproxy") is None:
        return CheckResult(
            "mitmproxy", "error", "not installed",
            hint="pip install mitmproxy",
        )
    try:
        import mitmproxy  # noqa: F401
        version = getattr(mitmproxy, "__version__", "?")
        return CheckResult("mitmproxy", "ok", version)
    except Exception as exc:
        return CheckResult("mitmproxy", "error", f"import failed: {exc!r}")


def check_root_ca() -> CheckResult:
    """Detect whether mitmproxy's CA file has been generated."""
    cert = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    if cert.is_file():
        return CheckResult(
            "Root CA file", "ok",
            f"{cert} ({cert.stat().st_size} B)",
        )
    return CheckResult(
        "Root CA file", "warn",
        "not generated yet — run proxy once to produce it",
        hint="Start the Network Proxy then run the cert wizard",
    )


def check_core_api(host: str = "127.0.0.1", port: int = 9800) -> CheckResult:
    if _tcp_open(host, port, timeout=0.3):
        return CheckResult("Core API", "ok", f"{host}:{port}")
    return CheckResult(
        "Core API", "warn", f"{host}:{port} not reachable",
        hint="Click ▶ Start All to launch the core",
    )


def check_pce_database() -> CheckResult:
    try:
        from pce_core import config as _cfg
        db_path = Path(_cfg.DB_PATH)
    except Exception as exc:
        return CheckResult(
            "PCE database", "warn", f"path lookup failed: {exc!r}",
        )
    if db_path.is_file():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        return CheckResult(
            "PCE database", "ok",
            f"{db_path.name} ({size_mb:.2f} MB)",
        )
    return CheckResult(
        "PCE database", "info",
        "not created yet — will be created on first capture",
    )


def check_chrome_extension() -> CheckResult:
    repo_root = _repo_root()
    candidates = [
        repo_root / "pce_browser_extension_wxt" / ".output" / "chrome-mv3",
        repo_root / "pce_browser_extension_wxt" / ".output" / "firefox-mv3",
    ]
    found = [c for c in candidates if (c / "manifest.json").is_file()]
    if found:
        names = ", ".join(c.parent.name + "/" + c.name for c in found)
        return CheckResult(
            "Browser extension build", "ok", names,
            hint="Load it in chrome://extensions if you haven't",
        )
    return CheckResult(
        "Browser extension build", "warn",
        "no .output build found",
        hint="cd pce_browser_extension_wxt && pnpm build",
    )


def check_playwright() -> CheckResult:
    if importlib.util.find_spec("playwright") is None:
        return CheckResult(
            "playwright (CDP)", "info", "not installed",
            hint="pip install playwright && playwright install chromium",
        )
    return CheckResult("playwright (CDP)", "ok", "importable")


def check_ollama() -> CheckResult:
    if _tcp_open("127.0.0.1", 11434, timeout=0.25):
        return CheckResult("Ollama (:11434)", "ok", "listening")
    return CheckResult(
        "Ollama (:11434)", "info", "not running",
        hint="Optional — start Ollama if you use local models",
    )


def check_phoenix() -> CheckResult:
    if importlib.util.find_spec("phoenix") is None:
        return CheckResult(
            "Phoenix (arize-phoenix)", "info", "not installed",
            hint="pip install arize-phoenix  (optional observability UI)",
        )
    return CheckResult("Phoenix (arize-phoenix)", "ok", "importable")


def check_duckdb() -> CheckResult:
    if importlib.util.find_spec("duckdb") is None:
        return CheckResult(
            "DuckDB", "info", "not installed",
            hint="pip install duckdb  (optional analytics)",
        )
    return CheckResult("DuckDB", "ok", "importable")


def check_embeddings_backend() -> CheckResult:
    if importlib.util.find_spec("sentence_transformers") is not None:
        return CheckResult(
            "Semantic search backend", "ok", "sentence-transformers",
        )
    import os
    if os.environ.get("OPENAI_API_KEY"):
        return CheckResult(
            "Semantic search backend", "ok", "openai backend (key present)",
        )
    return CheckResult(
        "Semantic search backend", "info",
        "fallback hash backend (low quality)",
        hint="pip install sentence-transformers  for real embeddings",
    )


def check_node_pnpm() -> CheckResult:
    pnpm = shutil.which("pnpm")
    node = shutil.which("node")
    if pnpm and node:
        return CheckResult("node + pnpm", "ok", f"{node} + {pnpm}")
    missing = ", ".join([n for n, p in (("node", node), ("pnpm", pnpm)) if not p])
    return CheckResult(
        "node + pnpm", "info", f"missing: {missing}",
        hint="Install Node 18+ and pnpm if you plan to rebuild the extension",
    )


def check_os() -> CheckResult:
    return CheckResult(
        "Operating system", "ok",
        f"{platform.system()} {platform.release()} ({platform.machine()})",
    )


PROBES: tuple[Callable[[], CheckResult], ...] = (
    check_os,
    check_python_version,
    check_core_api,
    check_pce_database,
    check_mitmproxy,
    check_root_ca,
    check_chrome_extension,
    check_playwright,
    check_ollama,
    check_phoenix,
    check_duckdb,
    check_embeddings_backend,
    check_node_pnpm,
)


def run_all() -> list[CheckResult]:
    out: list[CheckResult] = []
    for probe in PROBES:
        try:
            out.append(probe())
        except Exception as exc:
            logger.exception("capability probe %s crashed", probe.__name__)
            out.append(CheckResult(
                probe.__name__, "error", f"probe crashed: {exc!r}",
            ))
    return out


def _tcp_open(host: str, port: int, *, timeout: float) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, int(port)))
            return True
        except (OSError, socket.timeout):
            return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


__all__ = ["CheckResult", "PROBES", "run_all"]
