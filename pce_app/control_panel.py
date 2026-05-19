# SPDX-License-Identifier: Apache-2.0
"""PCE Desktop Control Panel — independent PySide6 window.

A real desktop-style app, not a stacked console: sidebar navigation,
five pages, status bar, live activity feed. Every host mutation is
funnelled through :mod:`pce_app.system_state_guard` so closing the
panel — by any means — restores the system to its prior state.

Pages
-----
- **Overview**   — at-a-glance rollup, quick actions, live activity feed
- **Services**   — per-process start/stop with status dots
- **Capture Lanes** — lane × target health matrix
- **Network**    — VPN auto-chain detection + restart proxy
- **Health**     — capability self-check probes

Pythonw note: the panel runs under ``pythonw.exe`` from the desktop
shortcut, so ``sys.stdout`` is unavailable. All diagnostics go to
``~/.pce/logs/control_panel.log`` via :func:`_setup_file_logging`.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import math
import struct
import sys
import time
import urllib.error
import urllib.request
import wave
import webbrowser
from collections import deque
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    Qt, QObject, QSize, QThread, QTimer, Signal,
)
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpacerItem,
    QStackedWidget, QStatusBar, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .capability_check import CheckResult, run_all as run_capability_checks
from .service_manager import ServiceManager, ServiceStatus
from .system_state_guard import get_guard

logger = logging.getLogger("pce.control_panel")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CORE_HOST = "127.0.0.1"
CORE_PORT = 9800
DASHBOARD_URL = f"http://{CORE_HOST}:{CORE_PORT}/"
MATRIX_URL = f"http://{CORE_HOST}:{CORE_PORT}/api/v1/health/matrix"
STATS_URL = f"http://{CORE_HOST}:{CORE_PORT}/api/v1/stats"

LANE_HEALTH_POLL_MS = 30_000
NETWORK_ENV_POLL_MS = 60_000
CAPTURE_POLL_MS = 2_500

LANE_ORDER = ("browser", "desktop", "cli", "mcp")

# Palette — single source for the QSS and dynamic widget tints.
ACCENT      = "#7c83ff"
ACCENT_DARK = "#6366f1"
SURFACE     = "#ffffff"
BG          = "#f3f4f6"
SIDEBAR_BG  = "#1e293b"
SIDEBAR_BG2 = "#0f172a"
SIDEBAR_FG  = "#cbd5e1"
SIDEBAR_FG2 = "#ffffff"
BORDER      = "#e5e7eb"
INK         = "#1f2937"
INK_DIM     = "#6b7280"
INK_FAINT   = "#9ca3af"

COLOR_HEX = {
    "green":  "#22c55e",
    "yellow": "#eab308",
    "red":    "#ef4444",
    "grey":   "#94a3b8",
}
STATUS_HEX = {
    "ok":    "#22c55e",
    "warn":  "#eab308",
    "error": "#ef4444",
    "info":  "#94a3b8",
}
SERVICE_COLOR = {
    ServiceStatus.RUNNING:  "#22c55e",
    ServiceStatus.STARTING: "#eab308",
    ServiceStatus.ERROR:    "#ef4444",
    ServiceStatus.STOPPED:  "#94a3b8",
}

ASSETS_DIR = Path.home() / ".pce" / "assets"
LOGS_DIR   = Path.home() / ".pce" / "logs"
LOG_PATH   = LOGS_DIR / "control_panel.log"
DING_PATH  = ASSETS_DIR / "ding.wav"


# ---------------------------------------------------------------------------
# File logging — pythonw.exe has no stdout, so route to a rotating file.
# ---------------------------------------------------------------------------

def _setup_file_logging() -> None:
    """Send INFO+ to ``~/.pce/logs/control_panel.log`` (rotating, 1 MB×3)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    ))
    root = logging.getLogger()
    # Only add once — repeated init() calls would multiply handlers.
    if not any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "baseFilename", None) == str(LOG_PATH)
        for h in root.handlers
    ):
        root.addHandler(handler)
    root.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Sound — generated WAV + layered Win/Mac/Linux fallback
# ---------------------------------------------------------------------------

def _ensure_ding_wav() -> Path:
    """Synthesise a short two-tone bell into ``DING_PATH`` (idempotent).

    Done in code so we don't have to ship a binary asset. ~22 kB WAV,
    250 ms, two sine tones (E6 + A5) with linear decay envelope —
    pleasant + obviously audible.
    """
    if DING_PATH.is_file() and DING_PATH.stat().st_size > 4_000:
        return DING_PATH
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    framerate = 22050
    duration_s = 0.28
    n = int(framerate * duration_s)
    f1, f2 = 1318.5, 880.0  # E6, A5
    samples = []
    for i in range(n):
        t = i / framerate
        env = max(0.0, 1.0 - (t / duration_s))
        val = 0.45 * env * (math.sin(2 * math.pi * f1 * t)
                            + 0.55 * math.sin(2 * math.pi * f2 * t))
        # Clamp + quantize to 16-bit signed.
        v = max(-1.0, min(1.0, val))
        samples.append(int(v * 32767))
    with wave.open(str(DING_PATH), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(struct.pack(f"<{n}h", *samples))
    logger.info("ding wav generated: %s (%d bytes)",
                DING_PATH, DING_PATH.stat().st_size)
    return DING_PATH


def play_ding() -> bool:
    """Best-effort short bell. Logs WHICH backend fired so we can debug.

    Returns True if a sound API call succeeded (the speaker still has
    to be unmuted at the OS level for the user to actually hear it).
    """
    if sys.platform.startswith("win"):
        # 1) Generated WAV — most reliable, bypasses system event sounds.
        try:
            wav = _ensure_ding_wav()
            import winsound
            winsound.PlaySound(
                str(wav),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            logger.debug("ding: PlaySound(WAV)")
            return True
        except Exception as exc:
            logger.warning("ding WAV path failed: %r", exc)
        # 2) Direct tone fallback — works even if PlaySound is misbehaving.
        try:
            import winsound
            winsound.Beep(880, 180)
            logger.debug("ding: Beep tone")
            return True
        except Exception as exc:
            logger.warning("ding Beep failed: %r", exc)
        # 3) Last resort: SystemAsterisk alias.
        try:
            import winsound
            winsound.PlaySound(
                "SystemAsterisk",
                winsound.SND_ALIAS | winsound.SND_ASYNC,
            )
            logger.debug("ding: SND_ALIAS Asterisk")
            return True
        except Exception as exc:
            logger.warning("ding alias failed: %r", exc)
        return False

    if sys.platform == "darwin":
        import subprocess
        for sound in ("Tink", "Glass", "Pop", "Funk"):
            path = Path(f"/System/Library/Sounds/{sound}.aiff")
            if path.is_file():
                subprocess.Popen(
                    ["afplay", str(path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                logger.debug("ding: afplay %s", sound)
                return True
        return False

    # Linux
    import shutil, subprocess
    for prog, arg in (
        ("paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"),
        ("aplay", "/usr/share/sounds/alsa/Front_Center.wav"),
    ):
        if shutil.which(prog):
            subprocess.Popen(
                [prog, arg],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logger.debug("ding: %s", prog)
            return True
    try:
        sys.stdout.write("\a"); sys.stdout.flush()
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# QSS stylesheet
# ---------------------------------------------------------------------------

def _build_stylesheet() -> str:
    return f"""
    QMainWindow, QWidget#central {{ background: {BG}; }}
    QLabel {{ color: {INK}; }}

    /* Sidebar */
    QWidget#sidebar {{
        background: {SIDEBAR_BG};
        border-right: 1px solid {SIDEBAR_BG2};
    }}
    QLabel#brand {{
        color: {SIDEBAR_FG2};
        font-size: 16px;
        font-weight: 600;
        padding: 18px 18px 8px 18px;
    }}
    QLabel#brandSub {{
        color: {SIDEBAR_FG};
        font-size: 11px;
        padding: 0 18px 14px 18px;
    }}
    QListWidget#nav {{
        background: transparent;
        border: none;
        color: {SIDEBAR_FG};
        font-size: 13px;
        outline: none;
        padding: 4px 0;
    }}
    QListWidget#nav::item {{
        padding: 11px 18px;
        border-left: 3px solid transparent;
    }}
    QListWidget#nav::item:selected {{
        background: {SIDEBAR_BG2};
        border-left: 3px solid {ACCENT};
        color: {SIDEBAR_FG2};
        font-weight: 600;
    }}
    QListWidget#nav::item:hover:!selected {{
        background: rgba(255,255,255,0.05);
    }}

    /* Cards */
    QFrame.Card {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 8px;
    }}
    QLabel.CardTitle {{
        font-size: 13px;
        font-weight: 600;
        color: {INK};
        padding: 12px 16px 0 16px;
    }}
    QLabel.CardSubtitle {{
        font-size: 11px;
        color: {INK_DIM};
        padding: 0 16px 10px 16px;
    }}
    QFrame.CardSeparator {{
        background: {BORDER};
        max-height: 1px;
        min-height: 1px;
        border: none;
        margin: 0 12px;
    }}
    QLabel.KpiNumber {{
        font-size: 28px;
        font-weight: 700;
        color: {INK};
    }}
    QLabel.KpiLabel {{
        font-size: 11px;
        color: {INK_DIM};
    }}

    /* Buttons */
    QPushButton {{
        background: {SURFACE};
        border: 1px solid #d1d5db;
        border-radius: 6px;
        padding: 7px 14px;
        color: {INK};
        font-size: 13px;
    }}
    QPushButton:hover {{
        background: #f9fafb;
        border-color: #9ca3af;
    }}
    QPushButton:pressed {{
        background: #f3f4f6;
    }}
    QPushButton:disabled {{
        color: {INK_FAINT};
        background: #f9fafb;
    }}
    QPushButton#primary {{
        background: {ACCENT};
        border: 1px solid {ACCENT};
        color: white;
        font-weight: 600;
        padding: 9px 18px;
    }}
    QPushButton#primary:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
    QPushButton#danger {{
        background: #ef4444;
        border: 1px solid #ef4444;
        color: white;
        font-weight: 600;
        padding: 9px 18px;
    }}
    QPushButton#danger:hover {{ background: #dc2626; border-color: #dc2626; }}

    /* Tables */
    QTableWidget {{
        background: {SURFACE};
        alternate-background-color: #f9fafb;
        border: none;
        gridline-color: #f3f4f6;
        font-size: 13px;
    }}
    QTableWidget::item {{
        padding: 4px 6px;
    }}
    QHeaderView::section {{
        background: #f9fafb;
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: 8px;
        font-weight: 600;
        color: {INK};
    }}

    /* Status bar */
    QStatusBar {{
        background: {SURFACE};
        border-top: 1px solid {BORDER};
        color: {INK_DIM};
        font-size: 12px;
    }}
    QStatusBar::item {{ border: none; }}

    /* Checkboxes */
    QCheckBox {{
        color: {INK};
        spacing: 8px;
        font-size: 13px;
    }}

    /* Scroll areas — kill the visible frame */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    """


# ---------------------------------------------------------------------------
# Background fetchers
# ---------------------------------------------------------------------------

class _Fetcher(QObject):
    """One-shot HTTP fetch on a worker QThread; emits the parsed payload."""

    finished = Signal(object)

    def __init__(self, url: str, timeout_s: float = 3.0):
        super().__init__()
        self._url = url
        self._timeout = timeout_s

    def run(self) -> None:
        try:
            with urllib.request.urlopen(self._url, timeout=self._timeout) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
            self.finished.emit(payload)
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.finished.emit(exc)


class _NetworkEnvFetcher(QObject):
    finished = Signal(object)

    def run(self) -> None:
        try:
            from pce_core.network_env import detect
            self.finished.emit(detect())
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(exc)


# ---------------------------------------------------------------------------
# Capture notifier — polls /api/v1/stats, dings on delta, logs verbosely
# ---------------------------------------------------------------------------

class CaptureNotifier(QObject):
    """Watch ``/api/v1/stats`` and emit signals on capture deltas.

    Emits ``captured(delta, providers, total)`` whenever
    ``total_captures`` increases between two polls. The Control Panel
    also surfaces the raw poll result via ``ticked(total, ok)`` so the
    Overview status bar can show "captures: 62,985 · last poll OK".
    """

    captured = Signal(int, dict, int)   # (delta, providers, total_now)
    ticked   = Signal(int, bool)        # (total, ok)
    poll_error = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._last_total: Optional[int] = None
        self._last_breakdown: dict = {}
        self.muted: bool = False
        self._timer = QTimer(self)
        self._timer.setInterval(CAPTURE_POLL_MS)
        self._timer.timeout.connect(self._tick)
        self._consec_errors = 0
        self._dings_played = 0

    # -- public --

    def start(self) -> None:
        self._timer.start()
        logger.info("CaptureNotifier started, polling %s every %d ms",
                    STATS_URL, CAPTURE_POLL_MS)
        QTimer.singleShot(150, self._tick)

    def stop(self) -> None:
        self._timer.stop()
        logger.info("CaptureNotifier stopped (dings_played=%d)", self._dings_played)

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        logger.info("CaptureNotifier muted=%s", self.muted)

    @property
    def last_total(self) -> Optional[int]:
        return self._last_total

    @property
    def dings_played(self) -> int:
        return self._dings_played

    # -- internal --

    def _tick(self) -> None:
        try:
            with urllib.request.urlopen(STATS_URL, timeout=1.5) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._consec_errors += 1
            self.poll_error.emit(f"{type(exc).__name__}: {exc}")
            # First 3 errors logged at INFO so we see the symptom; after
            # that fall to DEBUG so a long-down core doesn't flood the log.
            level = logging.INFO if self._consec_errors <= 3 else logging.DEBUG
            logger.log(level, "stats poll failed (#%d): %s",
                       self._consec_errors, exc)
            self.ticked.emit(self._last_total or 0, False)
            return

        self._consec_errors = 0
        total = int(payload.get("total_captures", 0))
        by_provider = dict(payload.get("by_provider", {}) or {})
        self.ticked.emit(total, True)

        if self._last_total is None:
            self._last_total = total
            self._last_breakdown = by_provider
            logger.info("CaptureNotifier baseline: total=%d", total)
            return

        delta = total - self._last_total
        if delta > 0:
            delta_breakdown = {
                k: v - int(self._last_breakdown.get(k, 0))
                for k, v in by_provider.items()
                if v > int(self._last_breakdown.get(k, 0))
            }
            logger.info("capture delta +%d: %s", delta, delta_breakdown)
            if not self.muted:
                if play_ding():
                    self._dings_played += 1
            self.captured.emit(delta, delta_breakdown, total)

        self._last_total = total
        self._last_breakdown = by_provider


# ---------------------------------------------------------------------------
# Visual primitives
# ---------------------------------------------------------------------------

def render_status_dot(color_hex: str, size: int = 14) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#1f2937"), 0.5))
    p.setBrush(QBrush(QColor(color_hex)))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return pm


def render_app_icon(color_hex: str = ACCENT, size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(color_hex)))
    p.drawRoundedRect(0, 0, size, size, size // 8, size // 8)
    f = QFont(); f.setBold(True); f.setPixelSize(int(size * 0.6))
    p.setFont(f)
    p.setPen(QPen(QColor("white")))
    p.drawText(pm.rect(), Qt.AlignCenter, "P")
    p.end()
    return QIcon(pm)


class Card(QFrame):
    """White rounded container with an optional title + subtitle header.

    Usage::

        card = Card("Services", subtitle="3 of 5 running")
        card.body_layout().addWidget(my_inner_widget)
    """

    def __init__(self, title: str = "", subtitle: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        # Property-based class selector so the QSS .Card rule applies.
        self.setProperty("class", "Card")
        self.setFrameShape(QFrame.NoFrame)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if title:
            t = QLabel(title)
            t.setProperty("class", "CardTitle")
            outer.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setProperty("class", "CardSubtitle")
            self._subtitle = s
            outer.addWidget(s)
        else:
            self._subtitle = None
        if title:
            sep = QFrame()
            sep.setProperty("class", "CardSeparator")
            outer.addWidget(sep)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(16, 14, 16, 14)
        self._body.setSpacing(10)
        outer.addLayout(self._body)

    def body_layout(self) -> QVBoxLayout:
        return self._body

    def set_subtitle(self, text: str) -> None:
        if self._subtitle is None:
            return
        self._subtitle.setText(text)


class Kpi(QWidget):
    """Big number + tiny label, used in the Overview header strip."""

    def __init__(self, label: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self._num = QLabel("—")
        self._num.setProperty("class", "KpiNumber")
        self._lbl = QLabel(label)
        self._lbl.setProperty("class", "KpiLabel")
        v.addWidget(self._num)
        v.addWidget(self._lbl)

    def set_value(self, text: str, color_hex: Optional[str] = None) -> None:
        self._num.setText(text)
        if color_hex:
            self._num.setStyleSheet(f"color: {color_hex};")


class LiveActivityList(QWidget):
    """Streaming log of capture events. Last 30 only — purely a feedback
    surface, NOT a persistent log (that lives in SQLite + the dashboard).
    """

    MAX_ROWS = 30

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rows: deque[QWidget] = deque(maxlen=self.MAX_ROWS)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._inner = QWidget()
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(8, 6, 8, 6)
        self._inner_layout.setSpacing(2)
        self._inner_layout.addStretch()
        scroll.setWidget(self._inner)

        v.addWidget(scroll)

        self._empty_label = QLabel(
            "  Waiting for captures… send a message in ChatGPT / Claude / "
            "Cursor — it should appear here within ~3 seconds."
        )
        self._empty_label.setStyleSheet(f"color: {INK_DIM}; padding: 12px;")
        self._empty_label.setWordWrap(True)
        self._inner_layout.insertWidget(0, self._empty_label)
        self._scroll = scroll

    def add_capture(self, delta: int, providers: dict, total: int) -> None:
        if self._empty_label is not None:
            self._empty_label.deleteLater()
            self._empty_label = None

        ts = time.strftime("%H:%M:%S")
        top = ", ".join(
            f"{name} +{n}" for name, n in list(providers.items())[:3]
        ) if providers else f"+{delta}"
        row = self._build_row(ts, total, delta, top)
        # Insert at index 0 so newest is at top.
        # The layout has a trailing stretch — index 0 is fine.
        self._inner_layout.insertWidget(0, row)
        self._rows.append(row)

        # Trim
        if len(self._rows) >= self.MAX_ROWS:
            # The deque autoshifts; remove any extras from the layout.
            for i in range(self._inner_layout.count() - 1):  # skip stretch
                item = self._inner_layout.itemAt(i)
                w = item.widget() if item else None
                if w is None:
                    continue
                if w not in self._rows:
                    self._inner_layout.removeWidget(w)
                    w.deleteLater()

        # Pulse: briefly green-tint, then back to neutral.
        row.setStyleSheet(
            f"background: #ecfdf5; border-left: 3px solid #22c55e; "
            "padding-left: 6px; border-radius: 4px;"
        )
        QTimer.singleShot(900, lambda: row.setStyleSheet(""))

    def add_error(self, text: str) -> None:
        ts = time.strftime("%H:%M:%S")
        row = self._build_row(ts, 0, 0, f"⚠ {text}", error=True)
        self._inner_layout.insertWidget(0, row)
        self._rows.append(row)

    @staticmethod
    def _build_row(ts: str, total: int, delta: int, detail: str,
                   *, error: bool = False) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(10)

        when = QLabel(ts)
        when.setStyleSheet(f"color: {INK_FAINT}; font-family: Consolas, monospace;")
        when.setMinimumWidth(70)
        h.addWidget(when)

        dot = QLabel()
        dot.setPixmap(render_status_dot(
            STATUS_HEX["error"] if error else STATUS_HEX["ok"]
        ))
        h.addWidget(dot)

        if delta > 0:
            d = QLabel(f"+{delta}")
            d.setStyleSheet("color: #15803d; font-weight: 600;")
            d.setMinimumWidth(40)
            h.addWidget(d)
        else:
            sp = QLabel("")
            sp.setMinimumWidth(40)
            h.addWidget(sp)

        body = QLabel(detail)
        body.setStyleSheet(
            f"color: {INK if not error else '#b91c1c'};"
        )
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        h.addWidget(body, stretch=1)

        if total > 0:
            tot = QLabel(f"total {total:,}")
            tot.setStyleSheet(f"color: {INK_DIM}; font-family: Consolas, monospace;")
            h.addWidget(tot)

        return row

    def clear(self) -> None:
        for r in list(self._rows):
            self._inner_layout.removeWidget(r)
            r.deleteLater()
        self._rows.clear()


# ---------------------------------------------------------------------------
# Page: Overview
# ---------------------------------------------------------------------------

class OverviewPage(QWidget):
    """Landing page — rollup, KPIs, quick actions, live activity."""

    test_ding_requested = Signal()
    start_capturing_requested = Signal()
    stop_capturing_requested = Signal()
    mute_toggled = Signal(bool)

    def __init__(self, manager: ServiceManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._manager = manager

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # -- Hero card: rollup + KPIs --
        hero = Card("PCE Status", subtitle="—")
        self._hero = hero

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(28)
        self._k_status   = Kpi("Overall")
        self._k_captures = Kpi("Total captures")
        self._k_lanes    = Kpi("Lane health")
        self._k_running  = Kpi("Services up")
        for k in (self._k_status, self._k_captures, self._k_lanes, self._k_running):
            kpi_row.addWidget(k)
        kpi_row.addStretch()
        hero.body_layout().addLayout(kpi_row)
        root.addWidget(hero)

        # -- Quick actions --
        actions = Card(
            "Quick Actions",
            subtitle="Start capturing turns on mitmproxy AND takes over your "
                     "system-proxy slot. Stop reverts it.",
        )
        ar = QHBoxLayout()
        ar.setSpacing(8)
        self._btn_start = QPushButton("▶  Start Capturing")
        self._btn_start.setObjectName("primary")
        self._btn_start.clicked.connect(self.start_capturing_requested.emit)
        self._btn_stop = QPushButton("■  Stop Capturing")
        self._btn_stop.clicked.connect(self.stop_capturing_requested.emit)
        self._btn_test = QPushButton("🔊  Test Ding")
        self._btn_test.setToolTip(
            "Play the capture sound right now to confirm your speakers "
            "are unmuted. Independent of any actual capture."
        )
        self._btn_test.clicked.connect(self.test_ding_requested.emit)
        self._btn_dashboard = QPushButton("Open Dashboard")
        self._btn_dashboard.clicked.connect(lambda: webbrowser.open(DASHBOARD_URL))
        for b in (self._btn_start, self._btn_stop, self._btn_test, self._btn_dashboard):
            ar.addWidget(b)
        ar.addStretch()
        actions.body_layout().addLayout(ar)

        mute_row = QHBoxLayout()
        self._mute = QCheckBox("🔔  Ding when a new capture arrives")
        self._mute.setChecked(True)
        self._mute.setToolTip(
            "When ON, a short bell plays every time the capture count "
            "increases. Sound file: ~/.pce/assets/ding.wav"
        )
        self._mute.stateChanged.connect(
            lambda s: self.mute_toggled.emit(not bool(s))
        )
        mute_row.addWidget(self._mute)
        mute_row.addStretch()
        self._sound_status = QLabel("")
        self._sound_status.setStyleSheet(f"color: {INK_DIM}; font-size: 11px;")
        mute_row.addWidget(self._sound_status)
        actions.body_layout().addLayout(mute_row)

        root.addWidget(actions)

        # -- Live activity --
        live = Card(
            "Live Activity",
            subtitle="Last 30 ingest events. Use this to confirm captures "
                     "are arriving even when sound is muted.",
        )
        live.body_layout().setContentsMargins(0, 0, 0, 0)
        self.activity = LiveActivityList()
        self.activity.setMinimumHeight(220)
        live.body_layout().addWidget(self.activity)
        root.addWidget(live, stretch=1)

    # -- public --

    def apply_rollup(self, color: str) -> None:
        label = {"green": "All Systems Go", "yellow": "Warnings",
                 "red": "Action Needed", "grey": "Idle"}.get(color, "—")
        hex_ = COLOR_HEX.get(color, INK_DIM)
        self._k_status.set_value(label, hex_)
        self._hero.set_subtitle(
            "Real-time rollup of services, lane health, and capture flow."
        )

    def apply_capture_count(self, total: int) -> None:
        self._k_captures.set_value(f"{total:,}")

    def apply_lane_health(self, green: int, total: int) -> None:
        if total == 0:
            self._k_lanes.set_value("—", INK_DIM)
        else:
            self._k_lanes.set_value(f"{green}/{total}",
                                    COLOR_HEX["green"] if green == total
                                    else COLOR_HEX["yellow"])

    def apply_services_status(self, status: dict) -> None:
        up = sum(1 for v in status.values() if v.get("status") == "running")
        total = len(status)
        self._k_running.set_value(
            f"{up}/{total}",
            COLOR_HEX["green"] if up >= 1 else COLOR_HEX["grey"],
        )

    def report_ding(self, played: bool) -> None:
        ts = time.strftime("%H:%M:%S")
        if played:
            self._sound_status.setText(f"last ding {ts}  ✓")
            self._sound_status.setStyleSheet("color: #15803d; font-size: 11px;")
        else:
            self._sound_status.setText(f"last ding {ts}  (sound failed — see log)")
            self._sound_status.setStyleSheet("color: #b91c1c; font-size: 11px;")


# ---------------------------------------------------------------------------
# Page: Services
# ---------------------------------------------------------------------------

class ServiceRow(QWidget):
    toggle_clicked = Signal(str)

    def __init__(self, key: str, label: str, hint: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._key = key

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(12)

        self._dot = QLabel()
        self._dot.setPixmap(render_status_dot(SERVICE_COLOR[ServiceStatus.STOPPED], 16))
        h.addWidget(self._dot)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self._name = QLabel(label)
        f = self._name.font(); f.setBold(True); self._name.setFont(f)
        col.addWidget(self._name)
        self._sub = QLabel(hint or "—")
        self._sub.setStyleSheet(f"color: {INK_DIM}; font-size: 11px;")
        col.addWidget(self._sub)
        h.addLayout(col, stretch=1)

        self._meta = QLabel("stopped")
        self._meta.setStyleSheet(f"color: {INK_DIM}; font-family: Consolas, monospace;")
        self._meta.setMinimumWidth(240)
        h.addWidget(self._meta)

        self._toggle = QPushButton("Start")
        self._toggle.setFixedWidth(90)
        self._toggle.clicked.connect(lambda: self.toggle_clicked.emit(self._key))
        h.addWidget(self._toggle)

    def apply_status(self, info: dict) -> None:
        st = ServiceStatus(info.get("status", "stopped"))
        self._dot.setPixmap(render_status_dot(SERVICE_COLOR[st], 16))
        port = info.get("port") or 0
        pid = info.get("pid")
        err = info.get("error")
        if st == ServiceStatus.RUNNING:
            self._meta.setText(f":{port}    pid {pid}")
            self._meta.setStyleSheet(f"color: {INK}; font-family: Consolas, monospace;")
            self._toggle.setText("Stop")
        elif st == ServiceStatus.STARTING:
            self._meta.setText("starting…")
            self._meta.setStyleSheet("color: #b45309; font-family: Consolas, monospace;")
            self._toggle.setText("Stop")
        elif st == ServiceStatus.ERROR:
            self._meta.setText(f"error: {err or '?'}")
            self._meta.setStyleSheet("color: #b91c1c; font-family: Consolas, monospace;")
            self._toggle.setText("Start")
        else:
            self._meta.setText(f":{port}    stopped" if port else "stopped")
            self._meta.setStyleSheet(f"color: {INK_DIM}; font-family: Consolas, monospace;")
            self._toggle.setText("Start")


class ServicesPage(QWidget):
    bulk_start = Signal()
    bulk_stop = Signal()
    bulk_restart = Signal()
    toggle = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # Toolbar
        bar = Card("Bulk Actions",
                   subtitle="Start All also turns on the system proxy. "
                            "Stop All restores it.")
        br = QHBoxLayout()
        b_start = QPushButton("▶  Start All")
        b_start.setObjectName("primary")
        b_start.clicked.connect(self.bulk_start.emit)
        b_stop = QPushButton("■  Stop All")
        b_stop.clicked.connect(self.bulk_stop.emit)
        b_restart = QPushButton("↻  Restart All")
        b_restart.clicked.connect(self.bulk_restart.emit)
        for b in (b_start, b_stop, b_restart):
            br.addWidget(b)
        br.addStretch()
        bar.body_layout().addLayout(br)
        root.addWidget(bar)

        # Service rows
        svc = Card("Services",
                   subtitle="Each row reflects one PCE subprocess.")
        self._rows: dict[str, ServiceRow] = {}
        for key, label, hint in (
            ("core", "Core API Server",
             "FastAPI + SQLite — the brain. Always start this first."),
            ("proxy", "Network Proxy",
             "mitmproxy — captures TLS traffic from browsers/apps."),
            ("local_hook", "Local Model Hook",
             "Reverse-proxy in front of Ollama for local-model capture."),
            ("multi_hook", "Multi-Hook (auto)",
             "Auto-discover and front local model servers on common ports."),
            ("clipboard", "Clipboard Monitor",
             "Capture clipboard-paste prompts (experimental)."),
        ):
            row = ServiceRow(key, label, hint)
            row.toggle_clicked.connect(self.toggle.emit)
            svc.body_layout().addWidget(row)
            self._rows[key] = row
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet(f"background: {BORDER}; border: none; max-height: 1px;")
            svc.body_layout().addWidget(sep)
        # Drop the trailing separator visually by stretching at the end.
        root.addWidget(svc)
        root.addStretch()

    def apply_status(self, status: dict) -> None:
        for key, row in self._rows.items():
            row.apply_status(status.get(key, {}))


# ---------------------------------------------------------------------------
# Page: Capture Lanes (health matrix)
# ---------------------------------------------------------------------------

class LanesPage(QWidget):
    rollup_changed = Signal(str)
    counts_changed = Signal(int, int)  # (green, total)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        card = Card(
            "Capture Lane Health",
            subtitle="Rows = lanes (browser / desktop / cli / mcp). "
                     "Columns = target products. ● = healthy, hover for details.",
        )
        bar = QHBoxLayout()
        b_refresh = QPushButton("↻  Refresh now")
        b_refresh.clicked.connect(self.refresh_now)
        bar.addWidget(b_refresh)
        self._stamp = QLabel("never polled")
        self._stamp.setStyleSheet(f"color: {INK_DIM};")
        bar.addWidget(self._stamp)
        bar.addStretch()
        card.body_layout().addLayout(bar)

        self._table = QTableWidget(len(LANE_ORDER), 1)
        self._table.setVerticalHeaderLabels(list(LANE_ORDER))
        self._table.setHorizontalHeaderLabels(["(waiting for data)"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self._table.verticalHeader().setDefaultSectionSize(34)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(220)
        card.body_layout().addWidget(self._table)

        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color: {INK_DIM};")
        card.body_layout().addWidget(self._summary)

        root.addWidget(card)
        root.addStretch()

        self._rollup = "grey"
        self._thread: Optional[QThread] = None
        self._worker: Optional[_Fetcher] = None
        self._poll = QTimer(self)
        self._poll.setInterval(LANE_HEALTH_POLL_MS)
        self._poll.timeout.connect(self.refresh_now)
        self._poll.start()
        QTimer.singleShot(800, self.refresh_now)

    def refresh_now(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._stamp.setText("refreshing…")
        self._thread = QThread(self)
        self._worker = _Fetcher(MATRIX_URL)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_fetched)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _cleanup(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater(); self._worker = None
        if self._thread is not None:
            self._thread.deleteLater(); self._thread = None

    def _on_fetched(self, result) -> None:
        if isinstance(result, Exception):
            self._stamp.setText(f"unreachable: {type(result).__name__}")
            self._summary.setText(
                "Core server isn't answering — start it via Services."
            )
            self._set_rollup("grey")
            return
        if not isinstance(result, dict) or "lanes" not in result:
            self._stamp.setText("bad response shape")
            return
        self._render(result)
        self._stamp.setText(
            f"updated {time.strftime('%H:%M:%S')}    "
            f"window={result.get('window_hours', '?')}h"
        )

    def _render(self, payload: dict) -> None:
        lanes_payload = payload.get("lanes") or {}
        all_targets: set[str] = set()
        for ln in LANE_ORDER:
            targets = (lanes_payload.get(ln) or {}).get("targets") or {}
            all_targets.update(targets.keys())
        columns = sorted(all_targets)

        if not columns:
            self._table.setColumnCount(1)
            self._table.setHorizontalHeaderLabels(["(no beacons yet)"])
            for r in range(len(LANE_ORDER)):
                self._table.setItem(r, 0, QTableWidgetItem(""))
            self._set_rollup("grey")
            self._summary.setText(
                "No health beacons in window — use any AI tool to emit one."
            )
            self.counts_changed.emit(0, 0)
            return

        self._table.setColumnCount(len(columns))
        self._table.setHorizontalHeaderLabels(columns)

        worst = "grey"
        green = 0; total = 0
        for r, lane in enumerate(LANE_ORDER):
            lane_data = lanes_payload.get(lane) or {}
            targets = lane_data.get("targets") or {}
            for c, target in enumerate(columns):
                entry = targets.get(target)
                if entry is None:
                    cell = QTableWidgetItem("·")
                    cell.setForeground(QBrush(QColor("#e5e7eb")))
                    cell.setTextAlignment(Qt.AlignCenter)
                    self._table.setItem(r, c, cell)
                    continue
                color = entry.get("color") or "grey"
                cell = QTableWidgetItem("●")
                cell.setForeground(QBrush(QColor(COLOR_HEX.get(color, "#94a3b8"))))
                cell.setTextAlignment(Qt.AlignCenter)
                f = cell.font(); f.setPointSize(16); cell.setFont(f)
                fails = entry.get("fail_count_24h", 0)
                rate = entry.get("pass_rate_24h", 0.0)
                tier = entry.get("tier") or "—"
                cell.setToolTip(
                    f"{lane} × {target}\ncolor: {color}\ntier: {tier}\n"
                    f"fails (24h): {fails}\npass_rate: {rate:.0%}"
                )
                self._table.setItem(r, c, cell)
                worst = _max_severity(worst, color)
                total += 1
                if color == "green":
                    green += 1

        self._set_rollup(worst)
        self._summary.setText(
            f"{green} of {total} (lane × target) cells GREEN — rollup is "
            f"{self._rollup.upper()}"
        )
        self.counts_changed.emit(green, total)

    def _set_rollup(self, color: str) -> None:
        if color != self._rollup:
            self._rollup = color
            self.rollup_changed.emit(color)


_SEVERITY = ("green", "grey", "yellow", "red")


def _max_severity(a: str, b: str) -> str:
    ai = _SEVERITY.index(a) if a in _SEVERITY else 0
    bi = _SEVERITY.index(b) if b in _SEVERITY else 0
    return _SEVERITY[max(ai, bi)]


# ---------------------------------------------------------------------------
# Page: Network (VPN adaptation)
# ---------------------------------------------------------------------------

class NetworkPage(QWidget):
    chain_changed = Signal(bool)
    restart_proxy_requested = Signal()

    def __init__(self, manager: ServiceManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._manager = manager

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        card = Card(
            "VPN / Upstream-Proxy Adaptation",
            subtitle="When a local proxy (Clash, V2Ray, Mihomo, …) is "
                     "detected, PCE auto-chains mitmproxy through it so "
                     "your VPN keeps working alongside capture.",
        )

        head_row = QHBoxLayout()
        self._auto = QCheckBox("Auto-chain mitmproxy through detected VPN")
        self._auto.setChecked(self._manager.auto_chain_proxy)
        self._auto.stateChanged.connect(self._on_toggle)
        head_row.addWidget(self._auto)
        head_row.addStretch()
        b_redetect = QPushButton("↻  Re-detect")
        b_redetect.clicked.connect(self.refresh_now)
        head_row.addWidget(b_redetect)
        b_restart = QPushButton("↻  Restart Proxy")
        b_restart.setToolTip("Restart mitmproxy with the current chain setting.")
        b_restart.clicked.connect(self.restart_proxy_requested.emit)
        head_row.addWidget(b_restart)
        card.body_layout().addLayout(head_row)

        self._headline = QLabel("not yet detected")
        f = self._headline.font(); f.setBold(True); self._headline.setFont(f)
        card.body_layout().addWidget(self._headline)

        self._aux = QLabel("")
        self._aux.setStyleSheet(f"color: {INK_DIM};")
        card.body_layout().addWidget(self._aux)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Port", "Vendor", "Protocol", "Confidence"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setMaximumHeight(180)
        card.body_layout().addWidget(self._table)

        self._stamp = QLabel("never polled")
        self._stamp.setStyleSheet(f"color: {INK_DIM};")
        card.body_layout().addWidget(self._stamp)

        root.addWidget(card)
        root.addStretch()

        self._thread: Optional[QThread] = None
        self._worker: Optional[_NetworkEnvFetcher] = None
        self._poll = QTimer(self)
        self._poll.setInterval(NETWORK_ENV_POLL_MS)
        self._poll.timeout.connect(self.refresh_now)
        self._poll.start()
        QTimer.singleShot(500, self.refresh_now)

    def refresh_now(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._stamp.setText("re-detecting…")
        self._thread = QThread(self)
        self._worker = _NetworkEnvFetcher()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_detected)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _cleanup(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater(); self._worker = None
        if self._thread is not None:
            self._thread.deleteLater(); self._thread = None

    def _on_toggle(self, state) -> None:
        on = bool(state)
        self._manager.auto_chain_proxy = on
        self.chain_changed.emit(on)
        logger.info("auto_chain_proxy = %s", on)

    def _on_detected(self, result) -> None:
        if isinstance(result, Exception):
            self._headline.setText("detection failed")
            self._aux.setText(f"{type(result).__name__}: {result}")
            self._aux.setStyleSheet("color: #b91c1c;")
            self._stamp.setText(f"last attempt {time.strftime('%H:%M:%S')}")
            return

        env = result
        best = env.best_upstream
        action = env.recommended_action
        if action == "chain_upstream" and best is not None:
            self._headline.setText(f"✅  Will chain upstream → {best.display}")
            self._headline.setStyleSheet("color: #15803d; font-weight: 600;")
        elif action == "warn_conflict":
            self._headline.setText(
                f"⚠  Enterprise TLS CA detected "
                f"({', '.join(env.foreign_root_cas[:2])}) — running un-chained."
            )
            self._headline.setStyleSheet("color: #b45309; font-weight: 600;")
        elif env.has_tun:
            self._headline.setText(
                f"ℹ  TUN VPN active ({', '.join(env.tun_interfaces[:2])}) — "
                f"no chaining needed."
            )
            self._headline.setStyleSheet("color: #1e40af; font-weight: 600;")
        else:
            self._headline.setText(
                "No upstream proxy detected — mitmproxy runs directly."
            )
            self._headline.setStyleSheet(f"color: {INK}; font-weight: 600;")

        aux: list[str] = []
        if env.tun_interfaces:
            aux.append(f"TUN: {', '.join(env.tun_interfaces[:3])}")
        if env.foreign_root_cas:
            aux.append(f"foreign CA: {', '.join(env.foreign_root_cas)}")
        self._aux.setText("    ".join(aux))
        self._aux.setStyleSheet(
            "color: #b45309;" if env.foreign_root_cas else f"color: {INK_DIM};"
        )

        self._table.setRowCount(len(env.upstream_candidates))
        for r, c in enumerate(env.upstream_candidates):
            self._table.setItem(r, 0, QTableWidgetItem(str(c.port)))
            self._table.setItem(r, 1, QTableWidgetItem(c.likely_vendor or "—"))
            self._table.setItem(r, 2, QTableWidgetItem(c.kind))
            conf = QTableWidgetItem(f"{c.confidence:.0%}   ({c.probe_status})")
            color_hex = (
                "#15803d" if c.is_usable else
                "#b45309" if c.probe_status == "wrong_protocol" else
                "#b91c1c"
            )
            conf.setForeground(QBrush(QColor(color_hex)))
            self._table.setItem(r, 3, conf)

        self._stamp.setText(f"updated {time.strftime('%H:%M:%S')}")


# ---------------------------------------------------------------------------
# Page: Health (capability self-check)
# ---------------------------------------------------------------------------

class HealthPage(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        card = Card(
            "Capability Self-Check",
            subtitle="Each row is one host capability PCE may rely on. "
                     "INFO rows are optional features.",
        )
        bar = QHBoxLayout()
        b_refresh = QPushButton("↻  Re-check")
        b_refresh.clicked.connect(self.refresh_now)
        bar.addWidget(b_refresh)
        self._summary = QLabel("not yet run")
        self._summary.setStyleSheet(f"color: {INK_DIM};")
        bar.addWidget(self._summary)
        bar.addStretch()
        card.body_layout().addLayout(bar)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget()
        self._inner = QVBoxLayout(inner)
        self._inner.setContentsMargins(0, 0, 0, 0)
        self._inner.setSpacing(2)
        self._inner.addStretch()
        scroll.setWidget(inner)
        card.body_layout().addWidget(scroll)
        root.addWidget(card, stretch=1)

        QTimer.singleShot(300, self.refresh_now)

    def refresh_now(self) -> None:
        while self._inner.count() > 1:
            item = self._inner.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        results = run_capability_checks()
        for r in results:
            self._inner.insertWidget(self._inner.count() - 1, _build_capability_row(r))
        ok = sum(1 for r in results if r.status == "ok")
        warn = sum(1 for r in results if r.status == "warn")
        err = sum(1 for r in results if r.status == "error")
        self._summary.setText(
            f"{ok} ok    {warn} warn    {err} error    (total {len(results)})"
        )


def _build_capability_row(result: CheckResult) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(6, 4, 6, 4)
    h.setSpacing(10)

    dot = QLabel()
    dot.setPixmap(render_status_dot(STATUS_HEX.get(result.status, "#94a3b8"), 12))
    h.addWidget(dot)

    name = QLabel(result.name)
    name.setMinimumWidth(190)
    f = name.font(); f.setBold(True); name.setFont(f)
    h.addWidget(name)

    detail = QLabel(result.detail)
    detail.setStyleSheet(f"color: {INK};")
    detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
    h.addWidget(detail)
    h.addStretch()

    if result.hint:
        hint = QLabel(result.hint)
        hint.setStyleSheet(f"color: {INK_DIM}; font-style: italic; font-size: 12px;")
        h.addWidget(hint)

    row.setToolTip(result.hint or result.detail or result.name)
    return row


# ---------------------------------------------------------------------------
# Sidebar nav
# ---------------------------------------------------------------------------

class Sidebar(QWidget):
    """Dark sidebar with the brand + page nav."""

    page_changed = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(220)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        brand = QLabel("PCE")
        brand.setObjectName("brand")
        v.addWidget(brand)
        sub = QLabel("Personal Capture Engine")
        sub.setObjectName("brandSub")
        v.addWidget(sub)

        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._nav.setFrameShape(QListWidget.NoFrame)
        self._nav.setIconSize(QSize(18, 18))
        self._nav.currentRowChanged.connect(self.page_changed)
        for label in (
            "  Overview",
            "  Services",
            "  Capture Lanes",
            "  Network",
            "  Health",
        ):
            it = QListWidgetItem(label)
            self._nav.addItem(it)
        self._nav.setCurrentRow(0)
        v.addWidget(self._nav, stretch=1)

        # Footer link area
        footer = QVBoxLayout()
        footer.setContentsMargins(14, 8, 14, 14)
        footer.setSpacing(4)
        self._foot_log = QLabel(f"log: ~/.pce/logs/control_panel.log")
        self._foot_log.setStyleSheet(f"color: {INK_FAINT}; font-size: 10px;")
        footer.addWidget(self._foot_log)
        v.addLayout(footer)


# ---------------------------------------------------------------------------
# System tray
# ---------------------------------------------------------------------------

class PCETrayIcon(QSystemTrayIcon):
    show_panel_requested = Signal()
    quit_requested = Signal()
    start_all_requested = Signal()
    stop_all_requested = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.setIcon(render_app_icon(ACCENT))
        self.setToolTip("PCE Control Panel")

        menu = QMenu()
        act_show = QAction("Open Control Panel", menu)
        act_show.triggered.connect(self.show_panel_requested)
        menu.addAction(act_show)
        menu.addSeparator()
        act_dashboard = QAction("Open Dashboard (browser)", menu)
        act_dashboard.triggered.connect(lambda: webbrowser.open(DASHBOARD_URL))
        menu.addAction(act_dashboard)
        menu.addSeparator()
        act_start = QAction("Start Capturing", menu)
        act_start.triggered.connect(self.start_all_requested)
        menu.addAction(act_start)
        act_stop = QAction("Stop Capturing", menu)
        act_stop.triggered.connect(self.stop_all_requested)
        menu.addAction(act_stop)
        menu.addSeparator()
        act_quit = QAction("Quit PCE (restores system state)", menu)
        act_quit.triggered.connect(self.quit_requested)
        menu.addAction(act_quit)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def set_rollup_color(self, color: str) -> None:
        hex_ = COLOR_HEX.get(color, ACCENT) if color != "grey" else ACCENT
        self.setIcon(render_app_icon(hex_))
        self.setToolTip(f"PCE — lane health: {color}")

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_panel_requested.emit()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ControlPanel(QMainWindow):
    """Sidebar nav + stacked pages + status bar + tray."""

    def __init__(self, manager: ServiceManager):
        super().__init__()
        self._manager = manager
        self._force_quit = False

        # File logging FIRST — pythonw.exe has no stdout, so without
        # this nothing is debuggable when the user reports a problem.
        _setup_file_logging()

        # Crash recovery + signal handlers MUST happen before anything
        # that could mutate host state.
        self._guard = get_guard()
        try:
            if self._guard.recover_from_crash():
                logger.info("system state restored from stale snapshot")
        except Exception as exc:  # noqa: BLE001
            logger.warning("crash recovery failed: %r", exc)
        try:
            self._guard.install_signal_handlers()
        except Exception:
            logger.debug("install_signal_handlers failed", exc_info=True)

        # Window chrome
        self.setWindowTitle("PCE Control Panel")
        self.setWindowIcon(render_app_icon(ACCENT))
        self.resize(1180, 800)
        self.setMinimumSize(960, 640)
        self.setStyleSheet(_build_stylesheet())

        # Central: sidebar | pages
        central = QWidget(); central.setObjectName("central")
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar()
        outer.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.overview = OverviewPage(manager)
        self.services_page = ServicesPage()
        self.lanes_page = LanesPage()
        self.network_page = NetworkPage(manager)
        self.health_page = HealthPage()
        self.pages.addWidget(self.overview)
        self.pages.addWidget(self.services_page)
        self.pages.addWidget(self.lanes_page)
        self.pages.addWidget(self.network_page)
        self.pages.addWidget(self.health_page)
        outer.addWidget(self.pages, stretch=1)

        self.sidebar.page_changed.connect(self.pages.setCurrentIndex)

        # Status bar
        self._build_status_bar()

        # Wire signals
        self._wire()

        # Tray
        self.tray = PCETrayIcon(parent=self)
        self.tray.show_panel_requested.connect(self._show_window)
        self.tray.quit_requested.connect(self._real_quit)
        self.tray.start_all_requested.connect(self._start_all)
        self.tray.stop_all_requested.connect(manager.stop_all)
        self.lanes_page.rollup_changed.connect(self.tray.set_rollup_color)
        self.tray.show()

        # Service polling
        self._svc_poll = QTimer(self)
        self._svc_poll.setInterval(1000)
        self._svc_poll.timeout.connect(self._refresh_services_status)
        self._svc_poll.start()
        self._refresh_services_status()
        self._manager.on_change(lambda: QTimer.singleShot(0, self._refresh_services_status))

        # Capture notifier
        self.notifier = CaptureNotifier(parent=self)
        self.notifier.captured.connect(self._on_capture)
        self.notifier.ticked.connect(self._on_capture_tick)
        self.notifier.poll_error.connect(self._on_capture_error)
        QTimer.singleShot(2500, self.notifier.start)

        # Welcome toast — confirms tray icon is alive
        QTimer.singleShot(800, lambda: self.tray.showMessage(
            "PCE Control Panel ready",
            "Click the tray icon any time to reopen.",
            QSystemTrayIcon.Information,
            2500,
        ))

    # -- status bar --

    def _build_status_bar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._sb_services = QLabel("services: ?")
        self._sb_lanes = QLabel("lanes: —")
        self._sb_sound = QLabel("🔔 sound ON")
        self._sb_log = QLabel("")
        self._sb_log.setStyleSheet(f"color: {INK_DIM};")

        sb.addWidget(self._sb_services)
        sb.addWidget(_sep())
        sb.addWidget(self._sb_lanes)
        sb.addWidget(_sep())
        sb.addWidget(self._sb_sound)
        sb.addPermanentWidget(self._sb_log)

    # -- wiring --

    def _wire(self) -> None:
        # Overview
        self.overview.test_ding_requested.connect(self._test_ding)
        self.overview.start_capturing_requested.connect(self._start_all)
        self.overview.stop_capturing_requested.connect(self._manager.stop_all)
        self.overview.mute_toggled.connect(self._set_muted)

        # Services
        self.services_page.bulk_start.connect(self._start_all)
        self.services_page.bulk_stop.connect(self._manager.stop_all)
        self.services_page.bulk_restart.connect(self._restart_all)
        self.services_page.toggle.connect(self._toggle_service)

        # Lanes
        self.lanes_page.rollup_changed.connect(self.overview.apply_rollup)
        self.lanes_page.counts_changed.connect(self.overview.apply_lane_health)
        self.lanes_page.counts_changed.connect(self._update_lanes_status)
        self.lanes_page.rollup_changed.connect(self._update_lanes_rollup_status)

        # Network
        self.network_page.restart_proxy_requested.connect(self._restart_proxy)

    # -- service actions --

    def _start_all(self) -> None:
        self._manager.start_core()
        if self._manager.proxy_available():
            self._manager.start_proxy()
        self._manager.start_local_hook()

    def _restart_all(self) -> None:
        self._manager.stop_all()
        QTimer.singleShot(500, self._start_all)

    def _restart_proxy(self) -> None:
        if self._manager.is_running("proxy"):
            self._manager.stop_service("proxy")
            QTimer.singleShot(500, self._manager.start_proxy)
        else:
            self._manager.start_proxy()
        QTimer.singleShot(800, self.network_page.refresh_now)

    def _toggle_service(self, key: str) -> None:
        if self._manager.is_running(key):
            self._manager.stop_service(key)
        else:
            if   key == "core":        self._manager.start_core()
            elif key == "proxy":       self._manager.start_proxy()
            elif key == "local_hook":  self._manager.start_local_hook()
            elif key == "multi_hook":  self._manager.start_multi_hook()
            elif key == "clipboard":   self._manager.start_clipboard()

    def _refresh_services_status(self) -> None:
        status = self._manager.get_status()
        self.services_page.apply_status(status)
        self.overview.apply_services_status(status)
        up = sum(1 for v in status.values() if v.get("status") == "running")
        self._sb_services.setText(
            f"services {up}/{len(status)}"
        )

    # -- capture events --

    def _on_capture(self, delta: int, providers: dict, total: int) -> None:
        self.overview.apply_capture_count(total)
        self.overview.activity.add_capture(delta, providers, total)
        top = ", ".join(list(providers.keys())[:2]) if providers else "?"
        self.tray.showMessage(
            f"PCE captured +{delta}", f"From: {top}",
            QSystemTrayIcon.Information, 1500,
        )
        self.overview.report_ding(True)

    def _on_capture_tick(self, total: int, ok: bool) -> None:
        if ok:
            self.overview.apply_capture_count(total)
            self._sb_log.setText(
                f"captures {total:,} · last poll {time.strftime('%H:%M:%S')}"
            )

    def _on_capture_error(self, msg: str) -> None:
        self._sb_log.setText(f"core unreachable · {msg[:60]}")
        self.overview.activity.add_error(f"stats poll failed: {msg}")

    # -- sound --

    def _test_ding(self) -> None:
        ok = play_ding()
        logger.info("test ding fired (ok=%s)", ok)
        self.overview.report_ding(ok)
        if not ok:
            QMessageBox.warning(
                self, "Test Ding",
                "Sound API call failed. Check the log at "
                "~/.pce/logs/control_panel.log for details.",
            )

    def _set_muted(self, muted: bool) -> None:
        self.notifier.set_muted(muted)
        self._sb_sound.setText("🔕 sound OFF" if muted else "🔔 sound ON")

    # -- lane status --

    def _update_lanes_status(self, green: int, total: int) -> None:
        self._sb_lanes.setText(f"lanes {green}/{total}")

    def _update_lanes_rollup_status(self, color: str) -> None:
        self._sb_lanes.setStyleSheet(
            f"color: {COLOR_HEX.get(color, INK)};"
        )

    # -- close / quit --

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._force_quit or not QSystemTrayIcon.isSystemTrayAvailable():
            self._teardown()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "PCE is still running",
            "Tray icon kept it alive. Right-click → "
            '"Quit PCE" to fully exit and restore system state.',
            QSystemTrayIcon.Information,
            3500,
        )

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _real_quit(self) -> None:
        self._force_quit = True
        self._teardown()
        QApplication.quit()

    def _teardown(self) -> None:
        try:
            self.notifier.stop()
        except Exception:
            pass
        try:
            self._manager.stop_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stop_all failed during teardown: %r", exc)
        try:
            self._guard.restore(reason="control_panel_teardown")
        except Exception as exc:  # noqa: BLE001
            logger.warning("guard restore failed during teardown: %r", exc)


def _sep() -> QLabel:
    l = QLabel("·")
    l.setStyleSheet(f"color: {BORDER}; padding: 0 6px;")
    return l


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    *,
    auto_start_core: bool = True,
    open_dashboard: bool = False,
    extra_services: tuple[str, ...] = (),
) -> int:
    _setup_file_logging()
    logger.info("=" * 60)
    logger.info("PCE Control Panel launching")
    logger.info("=" * 60)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    manager = ServiceManager()
    panel = ControlPanel(manager)
    panel.show()

    if auto_start_core:
        manager.start_core()
    for key in extra_services:
        if   key == "proxy":       manager.start_proxy()
        elif key == "local_hook":  manager.start_local_hook()
        elif key == "multi_hook":  manager.start_multi_hook()
        elif key == "clipboard":   manager.start_clipboard()

    if open_dashboard:
        QTimer.singleShot(2000, lambda: webbrowser.open(DASHBOARD_URL))

    return app.exec()


__all__ = [
    "ControlPanel", "PCETrayIcon", "CaptureNotifier",
    "play_ding", "run",
]
