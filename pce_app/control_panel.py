# SPDX-License-Identifier: Apache-2.0
"""PCE Desktop Control Panel — independent PySide6 window.

Layout (cockpit style):

    ┌───────────────────────────────────────────────────────────┐
    │                       HERO ZONE                           │
    │              pulsing dot · status text                    │
    │               total · rate · primary actions              │
    ├──────────────────────────┬────────────────────────────────┤
    │                          │                                │
    │     RECENT ACTIVITY      │       SYSTEM STATUS            │
    │   sparkline + scrolling  │     compact services,          │
    │     capture stream       │      lanes, network            │
    │                          │                                │
    ├──────────────────────────┴────────────────────────────────┤
    │   🔔 [Preset ▾] [▶] [📁]            [⚙ More]             │
    └───────────────────────────────────────────────────────────┘

No sidebar navigation — everything that matters fits on one canvas.
Health / capability info opens as a separate dialog on demand.

Pythonw note: ``sys.stdout`` is None under the desktop shortcut. All
diagnostics go to ``~/.pce/logs/control_panel.log``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import math
import random
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
    Property, Qt, QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect,
    QRectF, QSize, QThread, QTimer, Signal,
    QPropertyAnimation,
)
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QFontDatabase, QFontMetrics, QGuiApplication,
    QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFrame,
    QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSpacerItem, QSplitter, QStatusBar, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
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

# An opener that NEVER consults the system proxy. urllib's default
# urlopen reads HTTP_PROXY / Windows registry / etc., which means when
# the user has Clash (or any other VPN-style proxy) configured at the
# OS level, our requests to 127.0.0.1:9800 get routed through it and
# come back as 502 Bad Gateway. PCE's own ingest server lives on
# loopback — there is no scenario where it should be reached through
# a proxy. Use this opener for every internal API call below.
_NO_PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({})
)

LANE_HEALTH_POLL_MS = 30_000
NETWORK_ENV_POLL_MS = 60_000
CAPTURE_POLL_MS = 2_500

LANE_ORDER = ("browser", "desktop", "cli", "mcp")

# ---------------------------------------------------------------------------
# Palette — single source of truth.
#
# Modeled after Apple's macOS Big Sur+ light mode: near-white bg, pure-
# white card surfaces, soft grey borders, near-black ink, restrained
# accent. Status colors keep semantic meaning (green/yellow/red).
# ---------------------------------------------------------------------------

BG          = "#fbfbfd"          # window background (almost-white, Apple)
SURFACE     = "#ffffff"
BORDER      = "#e5e5ea"          # very faint card border
BORDER_2    = "#d1d1d6"          # slightly stronger (toolbar dividers)
INK         = "#1d1d1f"          # primary text (near-black, Apple)
INK_DIM     = "#6e6e73"          # secondary text
INK_FAINT   = "#aeaeb2"          # tertiary text
HOVER       = "#f5f5f7"          # button hover bg
PRESSED     = "#ececef"          # button pressed bg
ACCENT      = "#0071e3"          # primary action (Apple blue)
ACCENT_HOV  = "#0077ED"
ACCENT_PRESS = "#0067CC"

STATUS_GREEN  = "#34c759"        # macOS system green
STATUS_YELLOW = "#ff9500"        # macOS system orange (warmer than yellow)
STATUS_RED    = "#ff3b30"        # macOS system red
STATUS_GREY   = "#c7c7cc"        # idle

COLOR_HEX = {
    "green":  STATUS_GREEN,
    "yellow": STATUS_YELLOW,
    "red":    STATUS_RED,
    "grey":   STATUS_GREY,
}
STATUS_HEX = {
    "ok":    STATUS_GREEN,
    "warn":  STATUS_YELLOW,
    "error": STATUS_RED,
    "info":  STATUS_GREY,
}
SERVICE_COLOR = {
    ServiceStatus.RUNNING:  STATUS_GREEN,
    ServiceStatus.STARTING: STATUS_YELLOW,
    ServiceStatus.ERROR:    STATUS_RED,
    ServiceStatus.STOPPED:  STATUS_GREY,
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ASSETS_DIR = Path.home() / ".pce" / "assets"
LOGS_DIR   = Path.home() / ".pce" / "logs"
LOG_PATH   = LOGS_DIR / "control_panel.log"
DING_VERSION = 2
DEFAULT_PRESET = "chime"
ACTIVE_PRESET_FILE = Path.home() / ".pce" / "state" / "sound_preset.txt"
CUSTOM_WAV_PATH = ASSETS_DIR / "custom.wav"


def _setup_file_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    ))
    root = logging.getLogger()
    if not any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "baseFilename", None) == str(LOG_PATH)
        for h in root.handlers
    ):
        root.addHandler(handler)
    root.setLevel(logging.INFO)


# ===========================================================================
# Sound system — preset registry, generators, play_ding
# ===========================================================================

def _gen_chime():
    """E5→B5 ascending bell with 4 partials per note."""
    framerate = 44100; duration_s = 0.95
    n = int(framerate * duration_s)
    notes = ((0.00, 659.25, 0.55), (0.13, 987.77, 0.70))
    partial_weights = (1.00, 0.45, 0.22, 0.09)
    base_tc = 0.22; attack_s = 0.006
    samples = [0.0] * n
    for start, freq, amp in notes:
        si = int(framerate * start)
        for i in range(si, n):
            t = (i - si) / framerate
            env_a = (t / attack_s) ** 0.5 if t < attack_s else 1.0
            v = 0.0
            for k, w in enumerate(partial_weights, start=1):
                tc = base_tc / k
                env = math.exp(-(t - attack_s) / tc) if t > attack_s else 1.0
                v += w * env * math.sin(2 * math.pi * freq * k * t)
            samples[i] += v * env_a * amp / sum(partial_weights)
    return samples, framerate


def _gen_coin():
    framerate = 44100; duration_s = 0.32
    n = int(framerate * duration_s)
    notes = ((0.00, 987.77, 0.08, 0.45), (0.07, 1318.5, 0.22, 0.65))
    samples = [0.0] * n
    for start, freq, dur, amp in notes:
        si = int(framerate * start); ei = min(n, si + int(framerate * dur))
        for i in range(si, ei):
            t = (i - si) / framerate
            env = math.exp(-t / (dur * 0.55))
            v = (math.sin(2*math.pi*freq*t) + 0.35 * math.sin(2*math.pi*freq*3*t)) * env * amp
            samples[i] += v
    return samples, framerate


def _gen_pop():
    framerate = 44100; duration_s = 0.10
    n = int(framerate * duration_s)
    samples = []
    rng = random.Random(42)
    lp = 0.0
    for i in range(n):
        t = i / framerate
        noise = (rng.random() - 0.5) * 2
        lp = lp * 0.7 + noise * 0.3
        env = math.exp(-t / 0.022)
        body = math.sin(2 * math.pi * 380 * t) * env * 0.35
        samples.append(lp * env * 0.65 + body)
    return samples, framerate


def _gen_bell():
    framerate = 44100; duration_s = 1.6
    n = int(framerate * duration_s); freq = 880.0
    partials = ((1.00, 1.00), (2.00, 0.55), (2.76, 0.30),
                (5.40, 0.15), (8.93, 0.08))
    attack_s = 0.003; base_tc = 0.55
    samples = []
    norm = sum(a for _, a in partials)
    for i in range(n):
        t = i / framerate
        env_a = (t / attack_s) ** 0.5 if t < attack_s else 1.0
        v = 0.0
        for ratio, amp in partials:
            tc = base_tc / max(1.0, ratio ** 0.5)
            env = math.exp(-(t - attack_s) / tc) if t > attack_s else 1.0
            v += amp * env * math.sin(2 * math.pi * freq * ratio * t)
        samples.append(v * env_a / norm)
    return samples, framerate


def _gen_beep():
    framerate = 44100; duration_s = 0.18
    n = int(framerate * duration_s); freq = 800.0
    samples = []
    for i in range(n):
        t = i / framerate
        v = 0.4 if math.sin(2 * math.pi * freq * t) > 0 else -0.4
        v *= max(0, 1 - t / duration_s) ** 0.4
        samples.append(v)
    return samples, framerate


def _gen_boop():
    framerate = 44100; duration_s = 0.30
    n = int(framerate * duration_s)
    f_start, f_end = 900.0, 350.0
    samples = []; phase = 0.0
    for i in range(n):
        t = i / framerate
        freq = f_start + (f_end - f_start) * (t / duration_s) ** 1.3
        phase += 2 * math.pi * freq / framerate
        env = math.exp(-t / 0.13)
        samples.append(math.sin(phase) * env * 0.55)
    return samples, framerate


def _gen_slide():
    framerate = 44100; duration_s = 0.55
    n = int(framerate * duration_s)
    f_start, f_end = 350.0, 1400.0
    samples = []; phase = 0.0
    for i in range(n):
        t = i / framerate
        freq = f_start + (f_end - f_start) * (t / duration_s) ** 1.6
        phase += 2 * math.pi * freq / framerate
        vib = math.sin(2 * math.pi * 7 * t) * 0.008 if t > 0.25 else 0
        env_in = min(1.0, t * 25)
        env_out = max(0.0, 1.0 - max(0, t - 0.45) / 0.10)
        samples.append(math.sin(phase + vib) * env_in * env_out * 0.5)
    return samples, framerate


def _gen_honk():
    framerate = 44100; duration_s = 0.42
    n = int(framerate * duration_s); base = 220.0
    samples = []
    for i in range(n):
        t = i / framerate
        freq = base * (1 + math.sin(2 * math.pi * 8.5 * t) * 0.05)
        v = (math.sin(2*math.pi*freq*t)
             + 0.45 * math.sin(2*math.pi*freq*3*t)
             + 0.15 * math.sin(2*math.pi*freq*5*t))
        if t < 0.04:
            env = (t / 0.04) ** 0.7
        elif t > duration_s - 0.06:
            env = max(0, (duration_s - t) / 0.06)
        else:
            env = 1.0
        samples.append(v * env * 0.32)
    return samples, framerate


def _gen_error():
    framerate = 44100; duration_s = 0.6
    n = int(framerate * duration_s)
    notes = ((0.00, 987.77, 0.5), (0.20, 698.46, 0.5))
    samples = [0.0] * n
    for start, freq, amp in notes:
        si = int(framerate * start)
        for i in range(si, n):
            t = (i - si) / framerate
            env = math.exp(-t / 0.18)
            attack = min(1.0, t / 0.005)
            samples[i] += math.sin(2 * math.pi * freq * t) * env * attack * amp
    return samples, framerate


SOUND_PRESETS: dict = {
    "chime":  ("Chime",  "Two-note rising bell (E5→B5). Default — clean, calm.", _gen_chime),
    "coin":   ("Coin",   "Retro game pickup, 8-bit-ish two notes.",              _gen_coin),
    "pop":    ("Pop",    "Quick percussive tap, ~100ms — minimal.",              _gen_pop),
    "bell":   ("Bell",   "Single sustained bell, inharmonic partials.",          _gen_bell),
    "beep":   ("Beep",   "Retro PC-speaker square wave.",                        _gen_beep),
    "boop":   ("Boop",   "Descending soft cartoon tone, 900→350 Hz.",            _gen_boop),
    "slide":  ("Slide",  "Slide whistle ascending — cartoon style.",             _gen_slide),
    "honk":   ("Honk",   "Goose honk — for the chaos.",                          _gen_honk),
    "error":  ("Error",  "Descending tritone — the 'uh-oh' sound.",              _gen_error),
    "custom": ("Custom", "Plays whatever you drop at ~/.pce/assets/custom.wav",  None),
}


def get_active_preset() -> str:
    try:
        text = ACTIVE_PRESET_FILE.read_text(encoding="utf-8").strip()
    except (OSError, FileNotFoundError):
        return DEFAULT_PRESET
    return text if text in SOUND_PRESETS else DEFAULT_PRESET


def set_active_preset(name: str) -> None:
    if name not in SOUND_PRESETS:
        raise ValueError(f"unknown preset: {name!r}")
    ACTIVE_PRESET_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_PRESET_FILE.write_text(name, encoding="utf-8")
    logger.info("active sound preset → %s", name)


def _write_wav(path: Path, samples: list, framerate: int) -> None:
    peak = max(abs(s) for s in samples) or 1.0
    gain = 0.85 / peak
    n = len(samples)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(struct.pack(
            f"<{n}h",
            *(max(-32768, min(32767, int(s * gain * 32767))) for s in samples),
        ))


def _ensure_preset_wav(name: str) -> Path:
    if name not in SOUND_PRESETS:
        raise ValueError(f"unknown preset: {name!r}")
    if name == "custom":
        if not CUSTOM_WAV_PATH.is_file():
            raise FileNotFoundError(
                f"Custom WAV not found. Drop any .wav at {CUSTOM_WAV_PATH}."
            )
        return CUSTOM_WAV_PATH
    out = ASSETS_DIR / f"{name}_v{DING_VERSION}.wav"
    if out.is_file() and out.stat().st_size > 1_000:
        return out
    _, _, gen = SOUND_PRESETS[name]
    samples, framerate = gen()
    _write_wav(out, samples, framerate)
    logger.info("preset %r generated: %s (%d bytes)",
                name, out, out.stat().st_size)
    return out


def play_ding(preset: Optional[str] = None) -> bool:
    """Play active or specified preset. Returns False on total failure."""
    name = preset or get_active_preset()
    try:
        wav = _ensure_preset_wav(name)
    except (ValueError, FileNotFoundError) as exc:
        logger.warning("preset %r unavailable (%s) — falling back", name, exc)
        try:
            wav = _ensure_preset_wav(DEFAULT_PRESET)
        except Exception as exc2:
            logger.error("default preset also failed: %r", exc2)
            wav = None

    if sys.platform.startswith("win"):
        if wav is not None:
            try:
                import winsound
                winsound.PlaySound(
                    str(wav),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                return True
            except Exception as exc:
                logger.warning("PlaySound failed: %r", exc)
        try:
            import winsound
            winsound.Beep(880, 180)
            return True
        except Exception as exc:
            logger.warning("Beep fallback failed: %r", exc)
        return False

    if sys.platform == "darwin":
        import subprocess
        if wav is not None:
            subprocess.Popen(
                ["afplay", str(wav)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        return False

    import shutil, subprocess
    if wav is not None and shutil.which("paplay"):
        subprocess.Popen(
            ["paplay", str(wav)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    return False


# Backwards-compat alias used by older tests / external callers.
def _ensure_ding_wav() -> Path:
    return _ensure_preset_wav(DEFAULT_PRESET)


# ===========================================================================
# QSS — global stylesheet. Modeled on macOS Big Sur light mode.
# ===========================================================================

def _build_stylesheet() -> str:
    return f"""
    /* Window + central widget */
    QMainWindow, QWidget#central, QDialog {{
        background: {BG};
        color: {INK};
    }}
    QWidget {{
        font-family: "SF Pro Text", "Segoe UI Variable Text", "Segoe UI",
                     "Helvetica Neue", Helvetica, Arial, sans-serif;
        font-size: 13px;
        color: {INK};
    }}

    /* Cards — soft white surface, faint border, drop-shadow applied
       programmatically via QGraphicsDropShadowEffect for depth. */
    QFrame[role="card"] {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
    }}

    /* Typography hierarchy */
    QLabel[role="hero-status"] {{
        font-size: 28px;
        font-weight: 500;
        color: {INK};
    }}
    QLabel[role="hero-meta"] {{
        font-size: 13px;
        color: {INK_DIM};
    }}
    QLabel[role="section-title"] {{
        font-size: 11px;
        font-weight: 600;
        color: {INK_DIM};
        letter-spacing: 1.2px;
        text-transform: uppercase;
        padding: 12px 16px 8px 16px;
    }}
    QLabel[role="row-primary"] {{
        font-size: 13px;
        color: {INK};
    }}
    QLabel[role="row-secondary"] {{
        font-size: 12px;
        color: {INK_DIM};
        font-family: "SF Mono", "Cascadia Code", "Consolas", monospace;
    }}
    QLabel[role="caption"] {{
        font-size: 11px;
        color: {INK_FAINT};
    }}

    /* Buttons — restrained, only Primary uses accent */
    QPushButton {{
        background: {SURFACE};
        border: 1px solid {BORDER_2};
        border-radius: 6px;
        padding: 7px 14px;
        color: {INK};
        font-size: 13px;
    }}
    QPushButton:hover    {{ background: {HOVER}; }}
    QPushButton:pressed  {{ background: {PRESSED}; }}
    QPushButton:disabled {{ color: {INK_FAINT}; background: {HOVER}; }}

    QPushButton[role="primary"] {{
        background: {ACCENT};
        border: 1px solid {ACCENT};
        color: white;
        font-weight: 600;
        padding: 9px 20px;
        font-size: 13px;
    }}
    QPushButton[role="primary"]:hover   {{ background: {ACCENT_HOV}; border-color: {ACCENT_HOV}; }}
    QPushButton[role="primary"]:pressed {{ background: {ACCENT_PRESS}; border-color: {ACCENT_PRESS}; }}

    QPushButton[role="danger"] {{
        background: {STATUS_RED};
        border: 1px solid {STATUS_RED};
        color: white;
        font-weight: 600;
        padding: 9px 20px;
        font-size: 13px;
    }}
    QPushButton[role="danger"]:hover {{
        background: #FF453A; border-color: #FF453A;
    }}

    /* Tool-button used for icon-only triggers in the toolbar */
    QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 5px 10px;
        color: {INK_DIM};
    }}
    QToolButton:hover {{
        background: {HOVER};
        color: {INK};
        border-color: {BORDER};
    }}

    /* Combo box — Apple-style flat */
    QComboBox {{
        background: {SURFACE};
        border: 1px solid {BORDER_2};
        border-radius: 6px;
        padding: 5px 28px 5px 10px;
        color: {INK};
        min-height: 22px;
    }}
    QComboBox:hover  {{ background: {HOVER}; }}
    QComboBox::drop-down {{ width: 22px; border: none; }}
    QComboBox QAbstractItemView {{
        background: {SURFACE};
        border: 1px solid {BORDER_2};
        border-radius: 6px;
        selection-background-color: {ACCENT};
        selection-color: white;
        padding: 4px;
        outline: 0;
    }}

    /* Checkbox */
    QCheckBox {{
        spacing: 8px;
        color: {INK};
    }}

    /* Toolbar (footer) */
    QFrame#toolbar {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 0;
        border-left: 0; border-right: 0; border-bottom: 0;
    }}

    /* Table (used inside dialogs) */
    QTableWidget {{
        background: {SURFACE};
        border: none;
        gridline-color: {BORDER};
        font-size: 12px;
    }}
    QHeaderView::section {{
        background: {BG};
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: 8px;
        font-weight: 600;
        color: {INK_DIM};
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.8px;
    }}

    /* Scroll area — invisible by default */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px; margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER_2};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {INK_FAINT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    /* Menu */
    QMenu {{
        background: {SURFACE};
        border: 1px solid {BORDER_2};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{ padding: 6px 16px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {HOVER}; color: {INK}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
    """


# ===========================================================================
# Visual primitives
# ===========================================================================

def render_status_dot(color_hex: str, size: int = 12) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(color_hex)))
    p.drawEllipse(0, 0, size, size)
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


def _attach_card_shadow(widget: QWidget,
                       blur: int = 18, y: int = 2, alpha: int = 18) -> None:
    """Soft drop-shadow that gives cards a sense of depth without looking
    Material-y. Tuned to be barely-visible-but-present."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, y)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)


def _card() -> QFrame:
    """Build the canonical "card" container: white, rounded, soft shadow."""
    c = QFrame()
    c.setProperty("role", "card")
    c.setAutoFillBackground(False)
    _attach_card_shadow(c)
    return c


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setProperty("role", "section-title")
    return lbl


class PulsingDot(QWidget):
    """The hero status indicator.

    Sizes are in **logical pixels** (Qt scales these to physical pixels
    on high-DPI displays automatically). The painted dot+halo always
    fills whatever box ``setFixedSize`` was called with, so the widget
    handles screen DPI changes transparently — Qt just hands paintEvent
    a higher-resolution surface.

    States:
      - "idle"   → static grey, no animation
      - "active" → static green when no traffic, pulses on each ping()
      - "error"  → static red
    """

    DIAMETER_DEFAULT = 44
    HALO_DIAMETER_DEFAULT = 88

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._diameter = self.DIAMETER_DEFAULT
        self._halo = self.HALO_DIAMETER_DEFAULT
        self.setFixedSize(self._halo, self._halo)
        self._state = "idle"
        self._color = QColor(STATUS_GREY)
        self._halo_alpha = 0.0
        self._scale = 1.0
        self._anim_halo = QPropertyAnimation(self, b"haloAlpha", self)
        self._anim_halo.setDuration(900)
        self._anim_halo.setStartValue(0.55)
        self._anim_halo.setEndValue(0.0)
        self._anim_halo.setEasingCurve(QEasingCurve.OutCubic)
        self._anim_scale = QPropertyAnimation(self, b"scale", self)
        self._anim_scale.setDuration(450)
        self._anim_scale.setStartValue(1.18)
        self._anim_scale.setEndValue(1.0)
        self._anim_scale.setEasingCurve(QEasingCurve.OutBack)

    def set_state(self, state: str) -> None:
        """state ∈ {idle, active, error, warn}"""
        self._state = state
        self._color = QColor({
            "idle":   STATUS_GREY,
            "active": STATUS_GREEN,
            "error":  STATUS_RED,
            "warn":   STATUS_YELLOW,
        }.get(state, STATUS_GREY))
        self.update()

    def ping(self) -> None:
        """Briefly pulse — call once per capture event."""
        if self._state == "idle":
            return
        # restart animations from t=0
        self._anim_halo.stop(); self._anim_halo.start()
        self._anim_scale.stop(); self._anim_scale.start()

    def set_scaled_size(self, diameter: int) -> None:
        """Rescale to a smaller diameter (for narrow / short windows)."""
        diameter = max(20, int(diameter))
        halo = diameter * 2
        if diameter == self._diameter:
            return
        self._diameter = diameter
        self._halo = halo
        self.setFixedSize(halo, halo)
        self.update()

    # Qt animation properties (via Property descriptor)
    def _get_halo_alpha(self): return self._halo_alpha
    def _set_halo_alpha(self, v):
        self._halo_alpha = float(v); self.update()
    haloAlpha = Property(float, _get_halo_alpha, _set_halo_alpha)

    def _get_scale(self): return self._scale
    def _set_scale(self, v):
        self._scale = float(v); self.update()
    scale = Property(float, _get_scale, _set_scale)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._halo, self._halo)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._halo, self._halo)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self.width() / 2
        cy = self.height() / 2

        # Halo (during ping)
        if self._halo_alpha > 0.001:
            halo_r = self._halo / 2
            color = QColor(self._color)
            color.setAlphaF(self._halo_alpha * 0.5)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(color))
            p.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

        # Main dot (scaled during ping)
        r = (self._diameter / 2) * self._scale
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawEllipse(QPointF(cx, cy), r, r)
        # Subtle inner highlight for depth
        glow = QColor(255, 255, 255, 80)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx - r * 0.25, cy - r * 0.25),
                      r * 0.55, r * 0.45)
        p.end()


class Sparkline(QWidget):
    """Tiny inline chart of capture rate over time.

    Owns a fixed-length deque of (timestamp, total_count). Call
    ``push(total)`` on every stats poll; the widget paints a smooth
    delta-per-bucket line.
    """

    MAX_POINTS = 60   # ~60 polls × 2.5s = 2.5 minutes of history

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(34)
        self.setMaximumHeight(36)
        self._totals: deque[tuple[float, int]] = deque(maxlen=self.MAX_POINTS)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(220, 35)

    def push(self, total: int) -> None:
        self._totals.append((time.time(), int(total)))
        self.update()

    def reset(self) -> None:
        self._totals.clear()
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        if len(self._totals) < 2:
            p.setPen(QPen(QColor(INK_FAINT), 1, Qt.DashLine))
            p.drawLine(0, self.height() // 2,
                       self.width(), self.height() // 2)
            return

        # Compute deltas (captures per poll interval).
        deltas = []
        items = list(self._totals)
        for prev, cur in zip(items, items[1:]):
            deltas.append(max(0, cur[1] - prev[1]))
        if not any(deltas):
            p.setPen(QPen(QColor(INK_FAINT), 1, Qt.DashLine))
            p.drawLine(0, self.height() // 2,
                       self.width(), self.height() // 2)
            return

        peak = max(deltas) or 1
        w = self.width()
        h = self.height() - 4
        n = len(deltas)
        step = w / max(1, n - 1) if n > 1 else w

        # Smooth line + filled area under the curve.
        path = QPainterPath()
        for i, d in enumerate(deltas):
            x = i * step
            # Tiny floor so even zero-deltas have a visible baseline
            y = h - (d / peak) * (h - 2) + 2
            if i == 0:
                path.moveTo(x, y)
            else:
                # Quadratic smoothing
                prev_x = (i - 1) * step
                prev_y = h - (deltas[i - 1] / peak) * (h - 2) + 2
                cx = (x + prev_x) / 2
                path.quadTo(prev_x, prev_y, cx, (y + prev_y) / 2)
                path.quadTo(cx, (y + prev_y) / 2, x, y)

        # Fill (gradient)
        fill_path = QPainterPath(path)
        fill_path.lineTo(w, h + 4)
        fill_path.lineTo(0, h + 4)
        fill_path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(0, 113, 227, 56))
        grad.setColorAt(1.0, QColor(0, 113, 227, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(fill_path)

        # Stroke
        p.setPen(QPen(QColor(ACCENT), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # Last-point dot
        if deltas:
            lx = (n - 1) * step
            ly = h - (deltas[-1] / peak) * (h - 2) + 2
            p.setBrush(QBrush(QColor(ACCENT)))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(lx, ly), 2.6, 2.6)
        p.end()


# ===========================================================================
# Background fetchers — run on QThread worker
# ===========================================================================

class _Fetcher(QObject):
    finished = Signal(object)

    def __init__(self, url: str, timeout_s: float = 3.0):
        super().__init__()
        self._url = url; self._timeout = timeout_s

    def run(self) -> None:
        try:
            with _NO_PROXY_OPENER.open(self._url, timeout=self._timeout) as resp:
                raw = resp.read()
            self.finished.emit(json.loads(raw.decode("utf-8")))
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


# ===========================================================================
# Capture notifier — polls /api/v1/stats, dings + emits signals
# ===========================================================================

class CaptureNotifier(QObject):
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

    def start(self) -> None:
        self._timer.start()
        logger.info("CaptureNotifier polling %s every %d ms",
                    STATS_URL, CAPTURE_POLL_MS)
        QTimer.singleShot(150, self._tick)

    def stop(self) -> None:
        self._timer.stop()

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)

    @property
    def last_total(self) -> Optional[int]: return self._last_total

    def _tick(self) -> None:
        try:
            with _NO_PROXY_OPENER.open(STATS_URL, timeout=1.5) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._consec_errors += 1
            self.poll_error.emit(f"{type(exc).__name__}: {exc}")
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
                play_ding()
            self.captured.emit(delta, delta_breakdown, total)
        self._last_total = total
        self._last_breakdown = by_provider


# ===========================================================================
# Hero zone — the panel's emotional centerpiece
# ===========================================================================

class HeroPanel(QWidget):
    """Big centered status indicator + primary actions.

    Responds to its own height: at very-short window heights the
    PulsingDot shrinks and the meta line hides so the buttons stay
    accessible. At normal heights it occupies ~220 dpi-px.

    States:
        idle     — grey dot, "Idle"          → primary CTA is "Start"
        active   — green dot,  "Capturing"   → primary CTA is "Stop"
        error    — red dot,    "Error"       → "Stop" CTA + hint
    """

    start_clicked = Signal()
    stop_clicked  = Signal()
    dashboard_clicked = Signal()

    # Breakpoints in logical pixels. Calibrated against the actual
    # height the hero gets in a normally-sized window (sizeHint = 260,
    # but layout often cedes ~217 to the splitter). These have to be
    # LOWER than that "comfortable" hero height — otherwise the meta
    # line hides even at default window sizes.
    _COMPACT_HEIGHT = 200       # below this, hide meta line
    _VERY_COMPACT_HEIGHT = 155  # below this, also hide status text

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # Min/max so the hero stays comfortably-sized but yields when
        # the window is shrunk hard. Preferred = sizeHint = 260.
        self.setMinimumHeight(140)
        self.setMaximumHeight(320)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 18, 0, 14)
        outer.setSpacing(8)
        outer.setAlignment(Qt.AlignHCenter)

        self._dot = PulsingDot()
        outer.addWidget(self._dot, alignment=Qt.AlignHCenter)

        self._status = QLabel("Idle")
        self._status.setProperty("role", "hero-status")
        self._status.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._status)

        self._meta = QLabel("Click Start Capturing to begin recording")
        self._meta.setProperty("role", "hero-meta")
        self._meta.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._meta)

        outer.addSpacing(14)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()
        self._btn_primary = QPushButton("Start Capturing")
        self._btn_primary.setProperty("role", "primary")
        self._btn_primary.clicked.connect(self._on_primary)
        self._btn_dash = QPushButton("Open Dashboard")
        self._btn_dash.clicked.connect(self.dashboard_clicked.emit)
        btn_row.addWidget(self._btn_primary)
        btn_row.addWidget(self._btn_dash)
        btn_row.addStretch()
        outer.addLayout(btn_row)
        outer.addStretch()

        self._is_active = False
        self._compact = False

    def sizeHint(self) -> QSize:  # noqa: N802
        # Preferred height = comfortable layout (dot + status + meta + buttons).
        return QSize(540, 260)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        h = event.size().height()
        if h < self._VERY_COMPACT_HEIGHT:
            self._dot.set_scaled_size(28)
            self._status.hide()
            self._meta.hide()
            self._compact = True
        elif h < self._COMPACT_HEIGHT:
            self._dot.set_scaled_size(34)
            self._status.show()
            self._meta.hide()
            self._compact = True
        else:
            self._dot.set_scaled_size(PulsingDot.DIAMETER_DEFAULT)
            self._status.show()
            self._meta.show()
            self._compact = False

    # -- public --

    def apply_state(self, is_capturing: bool, error: bool = False) -> None:
        self._is_active = is_capturing
        if error:
            self._dot.set_state("error")
            self._status.setText("Core Unreachable")
            self._meta.setText("PCE core API isn't responding — try Start")
            self._btn_primary.setText("Start Capturing")
        elif is_capturing:
            self._dot.set_state("active")
            self._status.setText("Capturing")
            self._btn_primary.setText("Stop Capturing")
            self._btn_primary.setProperty("role", "danger")
            self._btn_primary.style().unpolish(self._btn_primary)
            self._btn_primary.style().polish(self._btn_primary)
        else:
            self._dot.set_state("idle")
            self._status.setText("Idle")
            self._meta.setText("Click Start Capturing to begin recording")
            self._btn_primary.setText("Start Capturing")
            self._btn_primary.setProperty("role", "primary")
            self._btn_primary.style().unpolish(self._btn_primary)
            self._btn_primary.style().polish(self._btn_primary)

    def apply_metric(self, total: int, recent_rate: Optional[float]) -> None:
        if recent_rate is not None and recent_rate > 0:
            self._meta.setText(
                f"{total:,} events · {recent_rate:.1f}/min in the last 5 min"
            )
        elif total > 0:
            self._meta.setText(f"{total:,} events captured")
        elif self._is_active:
            self._meta.setText("Waiting for the first capture…")

    def ping(self) -> None:
        self._dot.ping()

    def _on_primary(self) -> None:
        if self._is_active:
            self.stop_clicked.emit()
        else:
            self.start_clicked.emit()


# ===========================================================================
# Activity panel — left column, sparkline + scrolling event list
# ===========================================================================

class _ActivityRow(QWidget):
    """One row in the recent-activity list. Three columns: time, source, delta."""

    def __init__(self, ts: str, source: str, delta: int,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 7, 14, 7)
        h.setSpacing(12)

        dot = QLabel()
        dot.setPixmap(render_status_dot(STATUS_GREEN, 8))
        h.addWidget(dot)

        when = QLabel(ts)
        when.setProperty("role", "row-secondary")
        when.setMinimumWidth(64)
        h.addWidget(when)

        src = QLabel(source)
        src.setProperty("role", "row-primary")
        h.addWidget(src, stretch=1)

        d = QLabel(f"+{delta}")
        d.setStyleSheet(f"color: {STATUS_GREEN}; font-weight: 600;")
        h.addWidget(d)


class ActivityPanel(QFrame):
    """Card containing the sparkline + scrolling list of recent captures."""

    MAX_ROWS = 50

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("role", "card")
        _attach_card_shadow(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(_section_title("Recent Activity"))

        spark_wrap = QWidget()
        sw = QVBoxLayout(spark_wrap)
        sw.setContentsMargins(16, 0, 16, 8)
        sw.setSpacing(4)
        self.sparkline = Sparkline()
        sw.addWidget(self.sparkline)
        self._spark_caption = QLabel("Waiting for stats…")
        self._spark_caption.setProperty("role", "caption")
        sw.addWidget(self._spark_caption)
        outer.addWidget(spark_wrap)

        # Subtle divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {BORDER};")
        outer.addWidget(div)

        # Scrolling list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 4, 0, 4)
        self._list.setSpacing(0)
        self._list.addStretch()
        scroll.setWidget(inner)
        outer.addWidget(scroll, stretch=1)

        self._rows: deque[_ActivityRow] = deque(maxlen=self.MAX_ROWS)
        self._empty = QLabel(
            "  Captures will appear here as soon as you message an AI tool.\n"
            "  The dot above pulses on every event."
        )
        self._empty.setProperty("role", "caption")
        self._empty.setStyleSheet(f"color: {INK_FAINT}; padding: 24px 18px;")
        self._empty.setAlignment(Qt.AlignTop)
        self._list.insertWidget(0, self._empty)

    def push_sample(self, total: int, ok: bool) -> None:
        if ok:
            self.sparkline.push(total)
            self._update_caption()

    def add_capture(self, delta: int, providers: dict, total: int) -> None:
        if self._empty is not None:
            self._empty.deleteLater()
            self._empty = None

        ts = time.strftime("%H:%M:%S")
        # Pick the highest-delta provider as the row's source label.
        source = "(unknown)"
        if providers:
            source = max(providers.items(), key=lambda kv: kv[1])[0]
            if len(providers) > 1:
                source += f" +{len(providers) - 1} more"
        row = _ActivityRow(ts, source, delta)
        self._list.insertWidget(0, row)
        self._rows.append(row)
        # Trim layout if deque dropped any
        if self._list.count() - 1 > self.MAX_ROWS:
            for i in range(self._list.count() - 2, -1, -1):
                item = self._list.itemAt(i)
                w = item.widget() if item else None
                if w is None: continue
                if w not in self._rows:
                    self._list.removeWidget(w); w.deleteLater()
                    break

        # Subtle row pulse
        row.setStyleSheet(f"background: rgba(52, 199, 89, 0.10);")
        QTimer.singleShot(700, lambda r=row: r.setStyleSheet(""))

    def _update_caption(self) -> None:
        deltas = []
        items = list(self.sparkline._totals)  # noqa: SLF001 (we own this widget)
        for prev, cur in zip(items, items[1:]):
            deltas.append(max(0, cur[1] - prev[1]))
        if not deltas:
            self._spark_caption.setText("Waiting for stats…")
            return
        per_window = sum(deltas)
        window_s = max(1, items[-1][0] - items[0][0])
        per_min = (per_window / window_s) * 60.0 if window_s else 0
        self._spark_caption.setText(
            f"{per_min:.1f} captures / minute  ·  last {len(items)} samples"
        )


# ===========================================================================
# System Status — right column with compact Services / Lanes / Network
# ===========================================================================

class _MiniServiceRow(QWidget):
    toggle_clicked = Signal(str)

    def __init__(self, key: str, label: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._key = key
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 5, 16, 5)
        h.setSpacing(10)

        self._dot = QLabel()
        self._dot.setPixmap(render_status_dot(STATUS_GREY, 9))
        h.addWidget(self._dot)

        self._name = QLabel(label)
        self._name.setProperty("role", "row-primary")
        h.addWidget(self._name, stretch=1)

        self._meta = QLabel("")
        self._meta.setProperty("role", "row-secondary")
        h.addWidget(self._meta)

        # Small hover-only toggle button
        self._toggle = QToolButton()
        self._toggle.setText("Start")
        self._toggle.setMinimumWidth(54)
        self._toggle.clicked.connect(lambda: self.toggle_clicked.emit(self._key))
        h.addWidget(self._toggle)

    def apply_status(self, info: dict) -> None:
        st = ServiceStatus(info.get("status", "stopped"))
        self._dot.setPixmap(render_status_dot(SERVICE_COLOR[st], 9))
        port = info.get("port") or 0
        if st == ServiceStatus.RUNNING:
            self._meta.setText(f":{port}" if port else "")
            self._toggle.setText("Stop")
        elif st == ServiceStatus.STARTING:
            self._meta.setText("starting…")
            self._toggle.setText("Stop")
        elif st == ServiceStatus.ERROR:
            self._meta.setText("error")
            self._meta.setStyleSheet(f"color: {STATUS_RED};")
            self._toggle.setText("Start")
        else:
            self._meta.setText(f":{port}" if port else "")
            self._meta.setStyleSheet(f"color: {INK_FAINT};")
            self._toggle.setText("Start")


class _MiniLanesGrid(QWidget):
    """Tiny lane × target dot matrix. Renders nothing if the core hasn't
    emitted any beacons yet."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 2, 16, 8)
        v.setSpacing(4)

        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(4)
        v.addWidget(self._grid_widget)

        self._empty = QLabel("Waiting for beacons…")
        self._empty.setProperty("role", "caption")
        v.addWidget(self._empty)

    def apply(self, payload: dict) -> None:
        # Clear grid
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()

        lanes_payload = payload.get("lanes") or {}
        all_targets: list[str] = sorted({
            t for ln in LANE_ORDER
            for t in (lanes_payload.get(ln) or {}).get("targets", {})
        })

        if not all_targets:
            self._empty.setText("Waiting for beacons…")
            self._empty.show()
            return
        self._empty.hide()

        # Column headers
        for ci, target in enumerate(all_targets):
            short = target.split(".")[0][:6]
            lbl = QLabel(short)
            lbl.setProperty("role", "caption")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setToolTip(target)
            self._grid.addWidget(lbl, 0, ci + 1)

        for ri, lane in enumerate(LANE_ORDER):
            lane_label = QLabel(lane)
            lane_label.setProperty("role", "row-primary")
            self._grid.addWidget(lane_label, ri + 1, 0)

            lane_data = lanes_payload.get(lane) or {}
            targets = lane_data.get("targets") or {}
            for ci, target in enumerate(all_targets):
                entry = targets.get(target)
                dot = QLabel()
                if entry is None:
                    dot.setText("·")
                    dot.setStyleSheet(f"color: {INK_FAINT};")
                else:
                    color_key = entry.get("color") or "grey"
                    dot.setPixmap(render_status_dot(
                        COLOR_HEX.get(color_key, STATUS_GREY), 8,
                    ))
                    dot.setToolTip(
                        f"{lane} × {target}\n"
                        f"color: {color_key}\n"
                        f"pass_rate: {entry.get('pass_rate_24h', 0):.0%}"
                    )
                dot.setAlignment(Qt.AlignCenter)
                self._grid.addWidget(dot, ri + 1, ci + 1)


class SystemStatusPanel(QFrame):
    """The right column: stacked compact subsections + 'view all' links."""

    toggle_service = Signal(str)
    health_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("role", "card")
        _attach_card_shadow(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- Services --
        outer.addWidget(_section_title("Services"))
        self._svc_rows: dict[str, _MiniServiceRow] = {}
        for key, label in (
            ("core",       "Core API"),
            ("proxy",      "Network Proxy"),
            ("local_hook", "Local Hook"),
            ("multi_hook", "Multi-Hook"),
            ("clipboard",  "Clipboard"),
        ):
            row = _MiniServiceRow(key, label)
            row.toggle_clicked.connect(self.toggle_service.emit)
            outer.addWidget(row)
            self._svc_rows[key] = row

        outer.addWidget(self._divider())

        # -- Capture Lanes --
        outer.addWidget(_section_title("Capture Lanes"))
        self._lanes = _MiniLanesGrid()
        outer.addWidget(self._lanes)
        self._lanes_summary = QLabel("")
        self._lanes_summary.setProperty("role", "caption")
        self._lanes_summary.setContentsMargins(16, 0, 16, 8)
        outer.addWidget(self._lanes_summary)

        outer.addWidget(self._divider())

        # -- Network --
        outer.addWidget(_section_title("Network"))
        net_wrap = QWidget()
        nw = QVBoxLayout(net_wrap)
        nw.setContentsMargins(16, 0, 16, 12)
        nw.setSpacing(4)
        self._net_status = QLabel("Detecting…")
        self._net_status.setProperty("role", "row-primary")
        self._net_status.setWordWrap(True)
        nw.addWidget(self._net_status)
        self._net_sub = QLabel("")
        self._net_sub.setProperty("role", "caption")
        self._net_sub.setWordWrap(True)
        nw.addWidget(self._net_sub)
        outer.addWidget(net_wrap)

        outer.addWidget(self._divider())

        # -- "View capability check" footer link --
        link_wrap = QWidget()
        lw = QHBoxLayout(link_wrap)
        lw.setContentsMargins(12, 8, 12, 12)
        self._health_btn = QToolButton()
        self._health_btn.setText("View capability check  ›")
        self._health_btn.setCursor(Qt.PointingHandCursor)
        self._health_btn.clicked.connect(self.health_clicked.emit)
        lw.addWidget(self._health_btn)
        lw.addStretch()
        outer.addWidget(link_wrap)

        outer.addStretch()

    @staticmethod
    def _divider() -> QFrame:
        d = QFrame()
        d.setFixedHeight(1)
        d.setStyleSheet(f"background: {BORDER}; margin: 0 16px;")
        return d

    # -- public --

    def apply_services(self, status: dict) -> None:
        for key, row in self._svc_rows.items():
            row.apply_status(status.get(key, {}))

    def apply_lanes(self, payload: dict) -> int:
        """Returns the count of GREEN cells (for status bar feedback)."""
        if isinstance(payload, dict) and "lanes" in payload:
            self._lanes.apply(payload)
            lanes_payload = payload.get("lanes") or {}
            green = sum(1 for ln in lanes_payload.values()
                        for t in (ln.get("targets") or {}).values()
                        if t.get("color") == "green")
            total = sum(len(ln.get("targets") or {})
                        for ln in lanes_payload.values())
            self._lanes_summary.setText(
                f"{green}/{total} GREEN" if total else ""
            )
            return green
        return 0

    def apply_lanes_error(self, _exc: Exception) -> None:
        self._lanes_summary.setText("Core unreachable")

    def apply_network(self, env) -> None:
        action = env.recommended_action
        best = env.best_upstream
        if action == "chain_upstream" and best is not None:
            self._net_status.setText(f"Chained → {best.likely_vendor or '?'}")
            self._net_status.setStyleSheet(f"color: {STATUS_GREEN};")
            self._net_sub.setText(f"{best.kind} {best.host}:{best.port}")
        elif action == "warn_conflict":
            self._net_status.setText("Enterprise CA detected")
            self._net_status.setStyleSheet(f"color: {STATUS_YELLOW};")
            self._net_sub.setText(", ".join(env.foreign_root_cas[:2]))
        elif env.has_tun:
            self._net_status.setText("TUN VPN active")
            self._net_status.setStyleSheet(f"color: {INK};")
            self._net_sub.setText(", ".join(env.tun_interfaces[:2]))
        else:
            self._net_status.setText("Direct (no proxy chain)")
            self._net_status.setStyleSheet(f"color: {INK};")
            self._net_sub.setText("mitmproxy runs without upstream")

    def apply_network_error(self, exc: Exception) -> None:
        self._net_status.setText("Detect failed")
        self._net_status.setStyleSheet(f"color: {STATUS_RED};")
        self._net_sub.setText(f"{type(exc).__name__}: {exc}")


# ===========================================================================
# Footer toolbar — sound preset + mute + more menu
# ===========================================================================

class SoundToolbar(QFrame):
    """Bottom bar: preset dropdown, preview, custom-folder, mute, More."""

    mute_toggled = Signal(bool)
    preset_changed = Signal(str)
    preview_requested = Signal()
    more_menu_requested = Signal(QPoint)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("toolbar")
        self.setFixedHeight(52)

        h = QHBoxLayout(self)
        h.setContentsMargins(20, 8, 20, 8)
        h.setSpacing(10)

        # Mute toggle (icon-only)
        self._mute_btn = QToolButton()
        self._mute_btn.setCheckable(True)
        self._mute_btn.setChecked(True)
        self._mute_btn.setMinimumWidth(36)
        self._update_mute_icon()
        self._mute_btn.toggled.connect(self._on_mute)
        self._mute_btn.setToolTip("Mute / unmute the capture chime")
        h.addWidget(self._mute_btn)

        h.addWidget(self._divider())

        # Preset combo
        self._combo = QComboBox()
        self._combo.setMinimumWidth(150)
        for key, (display, _desc, _gen) in SOUND_PRESETS.items():
            self._combo.addItem(display, key)
        active = get_active_preset()
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == active:
                self._combo.setCurrentIndex(i); break
        self._combo.currentIndexChanged.connect(self._on_preset_changed)
        h.addWidget(self._combo)

        # Preview
        self._preview_btn = QToolButton()
        self._preview_btn.setText("▶  Preview")
        self._preview_btn.clicked.connect(self.preview_requested.emit)
        self._preview_btn.setToolTip("Play the selected preset right now")
        h.addWidget(self._preview_btn)

        # Open assets folder
        self._open_btn = QToolButton()
        self._open_btn.setText("📁  Custom…")
        self._open_btn.setToolTip(
            f"Open {ASSETS_DIR} — drop any .wav as 'custom.wav' to use it "
            f"via the Custom preset"
        )
        self._open_btn.clicked.connect(self._open_assets)
        h.addWidget(self._open_btn)

        h.addStretch()

        # Status text + More menu
        self._status = QLabel("")
        self._status.setProperty("role", "caption")
        h.addWidget(self._status)

        self._more_btn = QToolButton()
        self._more_btn.setText("⚙  More")
        self._more_btn.clicked.connect(self._emit_more)
        h.addWidget(self._more_btn)

    @staticmethod
    def _divider() -> QFrame:
        d = QFrame()
        d.setFixedSize(1, 24)
        d.setStyleSheet(f"background: {BORDER};")
        return d

    def selected_preset(self) -> str:
        return self._combo.currentData() or DEFAULT_PRESET

    def set_status(self, text: str, color: str = INK_DIM) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}; font-size: 11px;")

    def set_muted_external(self, muted: bool) -> None:
        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(not muted)
        self._mute_btn.blockSignals(False)
        self._update_mute_icon()

    # -- handlers --

    def _on_mute(self, checked: bool) -> None:
        self._update_mute_icon()
        self.mute_toggled.emit(not checked)

    def _update_mute_icon(self) -> None:
        # checked == sound ON
        self._mute_btn.setText("🔔" if self._mute_btn.isChecked() else "🔕")

    def _on_preset_changed(self, _idx: int) -> None:
        name = self.selected_preset()
        try: set_active_preset(name)
        except ValueError: return
        self.preset_changed.emit(name)
        self._refresh_status_for_preset(name)

    def _refresh_status_for_preset(self, name: str) -> None:
        if name == "custom":
            if CUSTOM_WAV_PATH.is_file():
                kb = CUSTOM_WAV_PATH.stat().st_size / 1024
                self.set_status(f"custom.wav ✓ ({kb:.1f} KB)", STATUS_GREEN)
            else:
                self.set_status(
                    "custom.wav missing — falls back to Chime",
                    STATUS_YELLOW,
                )
        else:
            self.set_status("")

    def _open_assets(self) -> None:
        import os, subprocess as sp
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(ASSETS_DIR))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                sp.Popen(["open", str(ASSETS_DIR)])
            else:
                sp.Popen(["xdg-open", str(ASSETS_DIR)])
        except Exception as exc:  # noqa: BLE001
            logger.warning("open assets folder failed: %r", exc)

    def _emit_more(self) -> None:
        global_pt = self._more_btn.mapToGlobal(
            QPoint(0, self._more_btn.height())
        )
        self.more_menu_requested.emit(global_pt)


# ===========================================================================
# Dialogs
# ===========================================================================

class HealthDialog(QDialog):
    """Capability self-check popup. Modal-less, can be left open."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("PCE — Capability Check")
        self.resize(680, 520)
        self.setStyleSheet(_build_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        title = QLabel("Capability Check")
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {INK};")
        outer.addWidget(title)
        sub = QLabel(
            "Probes for everything PCE may rely on. INFO rows are optional."
        )
        sub.setProperty("role", "row-secondary")
        outer.addWidget(sub)

        bar = QHBoxLayout()
        self._refresh = QPushButton("↻  Re-check")
        self._refresh.clicked.connect(self.refresh_now)
        bar.addWidget(self._refresh)
        self._summary = QLabel("")
        self._summary.setProperty("role", "caption")
        bar.addWidget(self._summary)
        bar.addStretch()
        outer.addLayout(bar)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget()
        self._inner = QVBoxLayout(inner)
        self._inner.setContentsMargins(0, 0, 0, 0)
        self._inner.setSpacing(0)
        self._inner.addStretch()
        scroll.setWidget(inner)

        card = _card()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 6, 0, 6)
        cv.addWidget(scroll)
        outer.addWidget(card, stretch=1)

        QTimer.singleShot(150, self.refresh_now)

    def refresh_now(self) -> None:
        while self._inner.count() > 1:
            item = self._inner.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        results = run_capability_checks()
        for r in results:
            self._inner.insertWidget(self._inner.count() - 1,
                                     self._build_row(r))
        ok = sum(1 for r in results if r.status == "ok")
        warn = sum(1 for r in results if r.status == "warn")
        err = sum(1 for r in results if r.status == "error")
        self._summary.setText(
            f"{ok} ok    {warn} warn    {err} error    (total {len(results)})"
        )

    @staticmethod
    def _build_row(result) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(16, 7, 16, 7)
        h.setSpacing(12)

        dot = QLabel()
        dot.setPixmap(render_status_dot(
            STATUS_HEX.get(result.status, STATUS_GREY), 10,
        ))
        h.addWidget(dot)

        name = QLabel(result.name)
        name.setProperty("role", "row-primary")
        name.setMinimumWidth(180)
        h.addWidget(name)

        detail = QLabel(result.detail)
        detail.setProperty("role", "row-primary")
        detail.setStyleSheet(f"color: {INK_DIM};")
        h.addWidget(detail, stretch=1)

        if result.hint:
            hint = QLabel(result.hint)
            hint.setProperty("role", "caption")
            hint.setStyleSheet(
                f"color: {INK_FAINT}; font-style: italic; font-size: 11px;"
            )
            h.addWidget(hint)
        return row


# ===========================================================================
# System tray
# ===========================================================================

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
        act_dash = QAction("Open Dashboard (browser)", menu)
        act_dash.triggered.connect(lambda: webbrowser.open(DASHBOARD_URL))
        menu.addAction(act_dash)
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

    def set_status_color(self, color: str) -> None:
        hex_ = COLOR_HEX.get(color, ACCENT) if color != "grey" else ACCENT
        self.setIcon(render_app_icon(hex_))
        self.setToolTip(f"PCE — lane health: {color}")

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_panel_requested.emit()


# ===========================================================================
# Main window
# ===========================================================================

class ControlPanel(QMainWindow):
    """Hero on top, two columns in the middle, toolbar on bottom."""

    def __init__(self, manager: ServiceManager):
        super().__init__()
        self._manager = manager
        self._force_quit = False

        _setup_file_logging()
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

        # Window — sized in logical (device-independent) pixels so the
        # window comes up the "right" size at any DPI. Minimums are
        # deliberately low so the panel fits on a 1366×768 laptop
        # at 125% scaling.
        self.setWindowTitle("PCE")
        self.setWindowIcon(render_app_icon(ACCENT))
        self.resize(1100, 760)
        self.setMinimumSize(720, 540)
        self.setStyleSheet(_build_stylesheet())

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- Hero --
        self.hero = HeroPanel()
        # Hero is Fixed vertically (its sizeHint controls), Expanding
        # horizontally so it stays centered as the window widens.
        self.hero.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        root.addWidget(self.hero)

        # -- Two-column main area, user-resizable splitter --
        # QSplitter is preferred over QHBoxLayout here because:
        # (1) the user can drag the divider to taste; (2) at narrow
        # widths each side enforces its own minimumSizeHint, avoiding
        # the "crushed card" failure mode that plain stretch factors
        # produce.
        cols_wrap = QWidget()
        cols_outer = QVBoxLayout(cols_wrap)
        cols_outer.setContentsMargins(16, 0, 16, 12)
        cols_outer.setSpacing(0)

        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setHandleWidth(10)
        self._splitter.setChildrenCollapsible(False)
        # Style the handle so it's barely-visible-but-grab-able.
        self._splitter.setStyleSheet(
            f"QSplitter::handle {{ background: transparent; }}"
            f"QSplitter::handle:hover {{ background: {BORDER}; }}"
        )

        self.activity = ActivityPanel()
        self.activity.setMinimumWidth(320)
        self.activity.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.system_status = SystemStatusPanel()
        self.system_status.setMinimumWidth(280)
        self.system_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        self._splitter.addWidget(self.activity)
        self._splitter.addWidget(self.system_status)
        # Initial proportional split. QSplitter requires integer sizes,
        # so use logical pixels at startup; user can drag afterwards.
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([660, 440])

        cols_outer.addWidget(self._splitter)
        root.addWidget(cols_wrap, stretch=1)

        # -- Footer toolbar --
        self.toolbar = SoundToolbar()
        self.toolbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        root.addWidget(self.toolbar)

        # Tray
        self.tray = PCETrayIcon(parent=self)
        self.tray.show_panel_requested.connect(self._show_window)
        self.tray.quit_requested.connect(self._real_quit)
        self.tray.start_all_requested.connect(self._start_all)
        self.tray.stop_all_requested.connect(manager.stop_all)
        self.tray.show()

        # Health dialog (lazy)
        self._health_dialog: Optional[HealthDialog] = None

        # -- Signals --
        self.hero.start_clicked.connect(self._start_all)
        self.hero.stop_clicked.connect(manager.stop_all)
        self.hero.dashboard_clicked.connect(
            lambda: webbrowser.open(DASHBOARD_URL)
        )
        self.system_status.toggle_service.connect(self._toggle_service)
        self.system_status.health_clicked.connect(self._open_health)
        self.toolbar.mute_toggled.connect(self._on_mute)
        self.toolbar.preset_changed.connect(self._on_preset_changed)
        self.toolbar.preview_requested.connect(self._on_preview)
        self.toolbar.more_menu_requested.connect(self._show_more_menu)

        # -- Polling --
        self._svc_poll = QTimer(self)
        self._svc_poll.setInterval(1000)
        self._svc_poll.timeout.connect(self._refresh_services_status)
        self._svc_poll.start()
        self._refresh_services_status()
        manager.on_change(
            lambda: QTimer.singleShot(0, self._refresh_services_status)
        )

        # Lane health (background-fetched)
        self._lanes_thread: Optional[QThread] = None
        self._lanes_worker: Optional[_Fetcher] = None
        self._lanes_timer = QTimer(self)
        self._lanes_timer.setInterval(LANE_HEALTH_POLL_MS)
        self._lanes_timer.timeout.connect(self._fetch_lanes)
        self._lanes_timer.start()
        QTimer.singleShot(800, self._fetch_lanes)

        # Network env
        self._net_thread: Optional[QThread] = None
        self._net_worker: Optional[_NetworkEnvFetcher] = None
        self._net_timer = QTimer(self)
        self._net_timer.setInterval(NETWORK_ENV_POLL_MS)
        self._net_timer.timeout.connect(self._fetch_network)
        self._net_timer.start()
        QTimer.singleShot(500, self._fetch_network)

        # Capture notifier
        self.notifier = CaptureNotifier(parent=self)
        self.notifier.captured.connect(self._on_capture)
        self.notifier.ticked.connect(self._on_capture_tick)
        self.notifier.poll_error.connect(self._on_capture_error)
        QTimer.singleShot(2200, self.notifier.start)

        # First-launch welcome (tray balloon — confirms tray is alive)
        QTimer.singleShot(900, lambda: self.tray.showMessage(
            "PCE ready",
            "Click the tray icon any time to reopen.",
            QSystemTrayIcon.Information, 2500,
        ))

        # React to the user dragging the window to a screen with a
        # different DPI. Qt re-lays out automatically, but custom-
        # painted widgets sometimes need an explicit nudge to redraw
        # with the new device-pixel-ratio.
        try:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(self._on_screen_changed)
            else:
                # windowHandle is None until the window is shown — defer.
                QTimer.singleShot(0, self._wire_screen_signal)
        except Exception:  # noqa: BLE001
            logger.debug("could not wire screenChanged", exc_info=True)

    def _wire_screen_signal(self) -> None:
        handle = self.windowHandle()
        if handle is None:
            return
        handle.screenChanged.connect(self._on_screen_changed)
        # Also wire screen.logicalDotsPerInchChanged for the
        # in-place DPI-change case (user changes display scaling
        # without moving the window).
        screen = handle.screen()
        if screen is not None:
            screen.logicalDotsPerInchChanged.connect(
                lambda *_: self._refresh_for_dpi_change()
            )

    def _on_screen_changed(self, screen) -> None:
        logger.info(
            "screen changed → %s @ %.2f× scale",
            screen.name() if screen else "?",
            screen.devicePixelRatio() if screen else 1.0,
        )
        self._refresh_for_dpi_change()
        # Subscribe to the new screen's DPI signal too.
        try:
            screen.logicalDotsPerInchChanged.connect(
                lambda *_: self._refresh_for_dpi_change()
            )
        except Exception:
            pass

    def _refresh_for_dpi_change(self) -> None:
        """Force a clean repaint of the whole tree.

        Qt usually does this automatically, but custom-painted widgets
        (PulsingDot, Sparkline) sometimes hold stale pixmaps. Re-applying
        the stylesheet also recomputes em-based sizes.
        """
        self.setStyleSheet(_build_stylesheet())
        # Bubble update() to all descendants so paintEvent fires.
        for w in self.findChildren(QWidget):
            w.update()
        self.update()

    # -- Service actions --

    def _start_all(self) -> None:
        self._manager.start_core()
        if self._manager.proxy_available():
            self._manager.start_proxy()
        self._manager.start_local_hook()

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
        self.system_status.apply_services(status)
        is_capturing = any(
            v.get("status") == "running" for v in status.values()
        )
        core_running = status.get("core", {}).get("status") == "running"
        # Hero state: error if user clicked Start but core never came up
        # within 5s. For simplicity, just reflect running state.
        self.hero.apply_state(
            is_capturing=is_capturing,
            error=False,
        )

    # -- Lane fetch --

    def _fetch_lanes(self) -> None:
        if self._lanes_thread is not None and self._lanes_thread.isRunning():
            return
        self._lanes_thread = QThread(self)
        self._lanes_worker = _Fetcher(MATRIX_URL)
        self._lanes_worker.moveToThread(self._lanes_thread)
        self._lanes_thread.started.connect(self._lanes_worker.run)
        self._lanes_worker.finished.connect(self._on_lanes_done)
        self._lanes_worker.finished.connect(self._lanes_thread.quit)
        self._lanes_thread.finished.connect(self._cleanup_lanes)
        self._lanes_thread.start()

    def _cleanup_lanes(self) -> None:
        if self._lanes_worker: self._lanes_worker.deleteLater(); self._lanes_worker = None
        if self._lanes_thread: self._lanes_thread.deleteLater(); self._lanes_thread = None

    def _on_lanes_done(self, result) -> None:
        if isinstance(result, Exception):
            self.system_status.apply_lanes_error(result)
            self.tray.set_status_color("grey")
            return
        green = self.system_status.apply_lanes(result)
        # Roll tray color up from cells
        lanes_payload = result.get("lanes") or {}
        worst = "grey"
        sev = ("green", "grey", "yellow", "red")
        for ln in lanes_payload.values():
            for t in (ln.get("targets") or {}).values():
                c = t.get("color") or "grey"
                if sev.index(c) > sev.index(worst):
                    worst = c
        self.tray.set_status_color(worst)

    # -- Network fetch --

    def _fetch_network(self) -> None:
        if self._net_thread is not None and self._net_thread.isRunning():
            return
        self._net_thread = QThread(self)
        self._net_worker = _NetworkEnvFetcher()
        self._net_worker.moveToThread(self._net_thread)
        self._net_thread.started.connect(self._net_worker.run)
        self._net_worker.finished.connect(self._on_net_done)
        self._net_worker.finished.connect(self._net_thread.quit)
        self._net_thread.finished.connect(self._cleanup_net)
        self._net_thread.start()

    def _cleanup_net(self) -> None:
        if self._net_worker: self._net_worker.deleteLater(); self._net_worker = None
        if self._net_thread: self._net_thread.deleteLater(); self._net_thread = None

    def _on_net_done(self, result) -> None:
        if isinstance(result, Exception):
            self.system_status.apply_network_error(result)
        else:
            self.system_status.apply_network(result)

    # -- Capture events --

    def _on_capture(self, delta: int, providers: dict, total: int) -> None:
        self.activity.add_capture(delta, providers, total)
        self.hero.ping()
        top = ", ".join(list(providers.keys())[:2]) if providers else "?"
        self.tray.showMessage(
            f"PCE captured +{delta}", f"From: {top}",
            QSystemTrayIcon.Information, 1500,
        )

    def _on_capture_tick(self, total: int, ok: bool) -> None:
        self.activity.push_sample(total, ok)
        if ok:
            # Compute approx recent rate from sparkline buffer
            items = list(self.activity.sparkline._totals)  # noqa: SLF001
            if len(items) >= 2:
                delta_total = items[-1][1] - items[0][1]
                window_s = max(1, items[-1][0] - items[0][0])
                per_min = (delta_total / window_s) * 60.0
            else:
                per_min = None
            self.hero.apply_metric(total, per_min)
            self.toolbar.set_status(
                f"last poll {time.strftime('%H:%M:%S')}", INK_DIM,
            )

    def _on_capture_error(self, msg: str) -> None:
        self.toolbar.set_status(f"core unreachable", STATUS_RED)

    # -- Sound --

    def _on_mute(self, muted: bool) -> None:
        self.notifier.set_muted(muted)

    def _on_preset_changed(self, name: str) -> None:
        logger.info("preset changed → %s", name)

    def _on_preview(self) -> None:
        name = self.toolbar.selected_preset()
        ok = play_ding(name)
        ts = time.strftime("%H:%M:%S")
        if ok:
            self.toolbar.set_status(f"previewed {name} · {ts}", STATUS_GREEN)
        else:
            self.toolbar.set_status(f"preview failed · see log", STATUS_RED)

    # -- More menu --

    def _show_more_menu(self, global_pt: QPoint) -> None:
        m = QMenu(self)
        a1 = QAction("Open log file", m)
        a1.triggered.connect(self._open_log)
        m.addAction(a1)
        a2 = QAction("Restart all services", m)
        a2.triggered.connect(self._restart_all)
        m.addAction(a2)
        a3 = QAction("Open assets folder", m)
        a3.triggered.connect(self.toolbar._open_assets)
        m.addAction(a3)
        m.addSeparator()
        a4 = QAction("Quit PCE (restores system state)", m)
        a4.triggered.connect(self._real_quit)
        m.addAction(a4)
        m.exec(global_pt)

    def _open_log(self) -> None:
        import os, subprocess as sp
        try:
            if sys.platform == "win32":
                os.startfile(str(LOG_PATH))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                sp.Popen(["open", str(LOG_PATH)])
            else:
                sp.Popen(["xdg-open", str(LOG_PATH)])
        except Exception as exc:  # noqa: BLE001
            logger.warning("open log failed: %r", exc)

    def _restart_all(self) -> None:
        self._manager.stop_all()
        QTimer.singleShot(500, self._start_all)

    def _open_health(self) -> None:
        if self._health_dialog is None:
            self._health_dialog = HealthDialog(self)
        self._health_dialog.show()
        self._health_dialog.raise_()
        self._health_dialog.activateWindow()

    # -- Close / quit --

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._force_quit or not QSystemTrayIcon.isSystemTrayAvailable():
            self._teardown()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "PCE is still running",
            'Right-click the tray → "Quit PCE" to exit and restore.',
            QSystemTrayIcon.Information, 3500,
        )

    def _show_window(self) -> None:
        self.showNormal(); self.raise_(); self.activateWindow()

    def _real_quit(self) -> None:
        self._force_quit = True
        self._teardown()
        QApplication.quit()

    def _teardown(self) -> None:
        try: self.notifier.stop()
        except Exception: pass
        try: self._manager.stop_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stop_all failed during teardown: %r", exc)
        try: self._guard.restore(reason="control_panel_teardown")
        except Exception as exc:  # noqa: BLE001
            logger.warning("guard restore failed during teardown: %r", exc)


# ===========================================================================
# Entry point
# ===========================================================================

def _configure_highdpi() -> None:
    """Set Qt's HighDPI policy BEFORE QApplication is created.

    PassThrough means we don't round 1.25× or 1.5× scale factors to
    integers — fractional-DPI displays render crisply instead of
    being snapped to 100% or 200%. Must be invoked before the very
    first ``QApplication()`` call in the process, otherwise it has
    no effect.
    """
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("setHighDpiScaleFactorRoundingPolicy failed: %r", exc)


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

    _configure_highdpi()
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
    "HeroPanel", "ActivityPanel", "SystemStatusPanel", "SoundToolbar",
    "PulsingDot", "Sparkline", "HealthDialog",
    "SOUND_PRESETS", "play_ding", "get_active_preset", "set_active_preset",
    "run",
]
