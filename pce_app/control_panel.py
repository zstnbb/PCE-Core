# SPDX-License-Identifier: Apache-2.0
"""PCE Desktop Control Panel — independent PySide6 window.

The Control Panel is a native desktop window for *operating* PCE: start
/ stop background services, see which capture lanes are healthy, watch
captures arrive in real time (with an optional ding), and confirm the
host has every capability PCE depends on. It is deliberately separate
from the web dashboard, which is for *looking at captured data*.

When the panel takes over the system-proxy slot (because the user
started the Network Proxy service) it snapshots the prior settings via
:mod:`pce_app.system_state_guard`. Closing the panel, killing it, or
crashing it all funnel back through that guard so the host is restored.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Optional

from PySide6.QtCore import (
    Qt, QObject, QSize, QThread, QTimer, Signal,
)
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSpacerItem, QSystemTrayIcon, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .capability_check import CheckResult, run_all as run_capability_checks
from .service_manager import ServiceManager, ServiceStatus
from .system_state_guard import get_guard

logger = logging.getLogger("pce.control_panel")

# ---------------------------------------------------------------------------
# Palette + small visual helpers
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


def render_status_dot(color_hex: str, size: int = 14) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#1f2937"), 0.5))
    painter.setBrush(QBrush(QColor(color_hex)))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return pm


def render_app_icon(color_hex: str = "#7c83ff", size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(color_hex)))
    radius = size // 8
    painter.drawRoundedRect(0, 0, size, size, radius, radius)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.6))
    painter.setFont(font)
    painter.setPen(QPen(QColor("white")))
    painter.drawText(pm.rect(), Qt.AlignCenter, "P")
    painter.end()
    return QIcon(pm)


# ---------------------------------------------------------------------------
# Capture notifier — polls /api/v1/stats and dings on count increase
# ---------------------------------------------------------------------------

def _play_ding() -> None:
    """Best-effort cross-platform short beep.

    Windows: ``winsound.MessageBeep(MB_OK)`` (uses the system "Asterisk"
    sound, ~120 ms, no audio file shipping needed).
    macOS:   ``afplay /System/Library/Sounds/Tink.aiff`` if present.
    Linux:   ``\\a`` on stdout — last-resort terminal bell.
    """
    try:
        if sys.platform.startswith("win"):
            import winsound  # type: ignore[import-not-found]
            # MB_OK = 0x0; runs async, returns immediately.
            winsound.MessageBeep(0x0)
            return
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(
                ["afplay", "/System/Library/Sounds/Tink.aiff"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        # Linux fallback — paplay if available, else bell.
        import shutil, subprocess
        paplay = shutil.which("paplay")
        if paplay:
            subprocess.Popen(
                [paplay, "/usr/share/sounds/freedesktop/stereo/message.oga"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception as exc:  # noqa: BLE001
        logger.debug("ding failed: %r", exc)


class CaptureNotifier(QObject):
    """Watch ``/api/v1/stats`` and emit a signal whenever
    ``total_captures`` increases.

    The panel uses this to ding a notification sound AND to drop a
    short tray balloon ("captured: chatgpt.com") so the user sees
    real-time confirmation that PCE is working.
    """

    captured = Signal(int, dict)  # (delta, breakdown_by_provider)
    poll_error = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._last_total: Optional[int] = None
        self._last_breakdown: dict = {}
        self.muted: bool = False  # set externally by UI checkbox
        self._timer = QTimer(self)
        self._timer.setInterval(CAPTURE_POLL_MS)
        self._timer.timeout.connect(self._tick)

    # -- public --

    def start(self) -> None:
        self._timer.start()
        # First tick right away so we establish a baseline quickly
        # rather than dinging on whatever was already in the DB.
        QTimer.singleShot(150, self._tick)

    def stop(self) -> None:
        self._timer.stop()

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)

    @property
    def last_total(self) -> Optional[int]:
        return self._last_total

    # -- internal --

    def _tick(self) -> None:
        try:
            with urllib.request.urlopen(STATS_URL, timeout=1.5) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.poll_error.emit(f"{type(exc).__name__}: {exc}")
            return

        total = int(payload.get("total_captures", 0))
        by_provider = dict(payload.get("by_provider", {}) or {})

        if self._last_total is None:
            # First poll → establish baseline silently. No ding.
            self._last_total = total
            self._last_breakdown = by_provider
            return

        delta = total - self._last_total
        if delta > 0:
            # Diff provider counts so the toast can name the source.
            delta_breakdown = {
                k: v - int(self._last_breakdown.get(k, 0))
                for k, v in by_provider.items()
                if v > int(self._last_breakdown.get(k, 0))
            }
            if not self.muted:
                _play_ding()
            self.captured.emit(delta, delta_breakdown)

        self._last_total = total
        self._last_breakdown = by_provider


# ---------------------------------------------------------------------------
# Lane health: background fetch on a worker QThread
# ---------------------------------------------------------------------------

class _MatrixFetcher(QObject):
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


# ---------------------------------------------------------------------------
# Services pane
# ---------------------------------------------------------------------------

class ServiceRow(QWidget):
    toggle_clicked = Signal(str)

    def __init__(self, key: str, label: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        self._dot = QLabel()
        self._dot.setPixmap(render_status_dot(SERVICE_COLOR[ServiceStatus.STOPPED]))
        layout.addWidget(self._dot)

        self._name = QLabel(label)
        self._name.setMinimumWidth(180)
        f = self._name.font(); f.setBold(True); self._name.setFont(f)
        layout.addWidget(self._name)

        self._meta = QLabel("stopped")
        self._meta.setStyleSheet("color: #6b7280;")
        self._meta.setMinimumWidth(260)
        layout.addWidget(self._meta)

        layout.addItem(QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self._toggle = QPushButton("Start")
        self._toggle.setFixedWidth(80)
        self._toggle.clicked.connect(lambda: self.toggle_clicked.emit(self._key))
        layout.addWidget(self._toggle)

    def apply_status(self, info: dict) -> None:
        st = ServiceStatus(info.get("status", "stopped"))
        self._dot.setPixmap(render_status_dot(SERVICE_COLOR[st]))
        port = info.get("port") or 0
        pid = info.get("pid")
        err = info.get("error")
        if st == ServiceStatus.RUNNING:
            self._meta.setText(f":{port}    pid {pid}")
            self._meta.setStyleSheet("color: #374151;")
            self._toggle.setText("Stop")
        elif st == ServiceStatus.STARTING:
            self._meta.setText("starting…")
            self._meta.setStyleSheet("color: #b45309;")
            self._toggle.setText("Stop")
        elif st == ServiceStatus.ERROR:
            self._meta.setText(f"error: {err or '?'}")
            self._meta.setStyleSheet("color: #b91c1c;")
            self._toggle.setText("Start")
        else:
            self._meta.setText(f":{port}    stopped" if port else "stopped")
            self._meta.setStyleSheet("color: #6b7280;")
            self._toggle.setText("Start")


class ServicesPanel(QGroupBox):
    """Container for the five service rows + bulk-action toolbar + notif row."""

    notify_muted_changed = Signal(bool)

    def __init__(self, manager: ServiceManager, parent: Optional[QWidget] = None):
        super().__init__("Services", parent)
        self._manager = manager

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 14, 10, 10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._btn_start_all = QPushButton("▶ Start All")
        self._btn_stop_all = QPushButton("■ Stop All")
        self._btn_restart_all = QPushButton("↻ Restart All")
        self._btn_dashboard = QPushButton("Open Dashboard")
        for b in (self._btn_start_all, self._btn_stop_all,
                  self._btn_restart_all, self._btn_dashboard):
            toolbar.addWidget(b)
        toolbar.addItem(QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        outer.addLayout(toolbar)

        self._btn_start_all.clicked.connect(self._start_all)
        self._btn_stop_all.clicked.connect(self._stop_all)
        self._btn_restart_all.clicked.connect(self._restart_all)
        self._btn_dashboard.clicked.connect(lambda: webbrowser.open(DASHBOARD_URL))

        self._rows: dict[str, ServiceRow] = {}
        for key, label in (
            ("core",       "Core API Server"),
            ("proxy",      "Network Proxy"),
            ("local_hook", "Local Model Hook"),
            ("multi_hook", "Multi-Hook (auto)"),
            ("clipboard",  "Clipboard Monitor"),
        ):
            row = ServiceRow(key, label, parent=self)
            row.toggle_clicked.connect(self._on_toggle)
            outer.addWidget(row)
            self._rows[key] = row

        # Notification settings row
        notif = QHBoxLayout()
        self._mute = QCheckBox("🔔 Ding when a new capture arrives")
        self._mute.setChecked(True)
        self._mute.setToolTip(
            "When ON, a short system sound plays each time a new capture\n"
            "is ingested. Uncheck for silent mode."
        )
        self._mute.stateChanged.connect(
            lambda state: self.notify_muted_changed.emit(not bool(state))
        )
        notif.addWidget(self._mute)
        self._cap_label = QLabel("captures: —")
        self._cap_label.setStyleSheet("color: #6b7280;")
        notif.addWidget(self._cap_label)
        notif.addItem(QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        outer.addLayout(notif)

        self._manager.on_change(self._marshal_refresh)
        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self.refresh)
        self._poll.start()
        self.refresh()

    # -- public --

    def refresh(self) -> None:
        status = self._manager.get_status()
        for key, row in self._rows.items():
            row.apply_status(status.get(key, {}))

    def update_capture_count(self, total: int) -> None:
        """Called by ControlPanel when CaptureNotifier reports a tick."""
        self._cap_label.setText(f"captures: {total:,}")
        self._cap_label.setStyleSheet("color: #374151;")

    def flash_capture(self, delta: int, providers: dict) -> None:
        """Briefly highlight the capture label after a new capture arrives."""
        # Brief green flash then back to neutral.
        self._cap_label.setStyleSheet("color: #15803d; font-weight: bold;")
        top = ", ".join(list(providers.keys())[:2]) if providers else "?"
        prior = self._cap_label.text()
        self._cap_label.setText(f"{prior}   +{delta} ({top})")
        QTimer.singleShot(1500, lambda: self._cap_label.setStyleSheet("color: #374151;"))

    # -- actions --

    def _start_all(self) -> None:
        self._manager.start_core()
        if self._manager.proxy_available():
            self._manager.start_proxy()
        self._manager.start_local_hook()

    def _stop_all(self) -> None:
        self._manager.stop_all()

    def _restart_all(self) -> None:
        self._stop_all()
        QTimer.singleShot(500, self._start_all)

    def _on_toggle(self, key: str) -> None:
        if self._manager.is_running(key):
            self._manager.stop_service(key)
        else:
            if   key == "core":        self._manager.start_core()
            elif key == "proxy":       self._manager.start_proxy()
            elif key == "local_hook":  self._manager.start_local_hook()
            elif key == "multi_hook":  self._manager.start_multi_hook()
            elif key == "clipboard":   self._manager.start_clipboard()

    def _marshal_refresh(self) -> None:
        QTimer.singleShot(0, self.refresh)


# ---------------------------------------------------------------------------
# Lane Health pane
# ---------------------------------------------------------------------------

class LaneHealthPanel(QGroupBox):
    rollup_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("Capture Lane Health", parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 14, 10, 10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self.refresh_now)
        toolbar.addWidget(self._refresh_btn)
        self._status_label = QLabel("never polled")
        self._status_label.setStyleSheet("color: #6b7280;")
        toolbar.addWidget(self._status_label)
        toolbar.addItem(QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        outer.addLayout(toolbar)

        self._table = QTableWidget(len(LANE_ORDER), 1, self)
        self._table.setVerticalHeaderLabels(list(LANE_ORDER))
        self._table.setHorizontalHeaderLabels(["(waiting for data)"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        outer.addWidget(self._table)

        self._summary = QLabel("")
        self._summary.setStyleSheet("color: #6b7280; padding-top: 4px;")
        outer.addWidget(self._summary)

        self._rollup = "grey"
        self._thread: Optional[QThread] = None
        self._worker: Optional[_MatrixFetcher] = None

        self._poll = QTimer(self)
        self._poll.setInterval(LANE_HEALTH_POLL_MS)
        self._poll.timeout.connect(self.refresh_now)
        self._poll.start()
        QTimer.singleShot(800, self.refresh_now)

    def refresh_now(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._status_label.setText("refreshing…")
        self._thread = QThread(self)
        self._worker = _MatrixFetcher(MATRIX_URL)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_fetched)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def rollup_color(self) -> str:
        return self._rollup

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    def _on_fetched(self, result) -> None:
        if isinstance(result, Exception):
            self._status_label.setText(f"unreachable: {type(result).__name__}")
            self._summary.setText(
                "Core server isn't answering — start it to populate this view."
            )
            self._set_rollup("grey")
            return
        if not isinstance(result, dict) or "lanes" not in result:
            self._status_label.setText("bad response shape")
            return
        self._render(result)
        self._status_label.setText(
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
                "No health beacons in the window — start using PCE to emit some."
            )
            return

        self._table.setColumnCount(len(columns))
        self._table.setHorizontalHeaderLabels(columns)

        worst = "grey"
        for r, lane in enumerate(LANE_ORDER):
            lane_data = lanes_payload.get(lane) or {}
            targets = lane_data.get("targets") or {}
            for c, target in enumerate(columns):
                entry = targets.get(target)
                if entry is None:
                    cell = QTableWidgetItem("·")
                    cell.setForeground(QBrush(QColor("#cbd5e1")))
                    cell.setTextAlignment(Qt.AlignCenter)
                    self._table.setItem(r, c, cell)
                    continue
                color = entry.get("color") or "grey"
                cell = QTableWidgetItem("●")
                cell.setForeground(QBrush(QColor(COLOR_HEX.get(color, "#94a3b8"))))
                cell.setTextAlignment(Qt.AlignCenter)
                fails = entry.get("fail_count_24h", 0)
                rate = entry.get("pass_rate_24h", 0.0)
                tier = entry.get("tier") or "—"
                cell.setToolTip(
                    f"{lane} × {target}\ncolor: {color}\ntier: {tier}\n"
                    f"fails (24h): {fails}\npass_rate: {rate:.0%}"
                )
                self._table.setItem(r, c, cell)
                worst = _max_severity(worst, color)

        self._set_rollup(worst)
        green = sum(1 for ln in lanes_payload.values()
                    for t in (ln.get("targets") or {}).values()
                    if t.get("color") == "green")
        total = sum(len(ln.get("targets") or {}) for ln in lanes_payload.values())
        self._summary.setText(
            f"{green}/{total} (lane × target) cells GREEN — rollup is "
            f"{self._rollup.upper()}"
        )

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
# Network Environment pane
# ---------------------------------------------------------------------------

class _NetworkEnvFetcher(QObject):
    finished = Signal(object)

    def run(self) -> None:
        try:
            from pce_core.network_env import detect
            self.finished.emit(detect())
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(exc)


class NetworkEnvPanel(QGroupBox):
    chain_changed = Signal(bool)

    def __init__(self, manager: ServiceManager, parent: Optional[QWidget] = None):
        super().__init__("Network Environment (VPN adaptation)", parent)
        self._manager = manager

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 14, 10, 10)
        outer.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._auto_chain = QCheckBox("Auto-chain mitmproxy through detected VPN")
        self._auto_chain.setChecked(self._manager.auto_chain_proxy)
        self._auto_chain.setToolTip(
            "When ON, starting the Network Proxy runs mitmproxy in\n"
            "upstream-chain mode against the detected local proxy."
        )
        self._auto_chain.stateChanged.connect(self._on_chain_toggled)
        toolbar.addWidget(self._auto_chain)

        self._refresh_btn = QPushButton("↻ Re-detect")
        self._refresh_btn.clicked.connect(self.refresh_now)
        toolbar.addWidget(self._refresh_btn)

        self._restart_btn = QPushButton("↻ Restart Proxy")
        self._restart_btn.clicked.connect(self._restart_proxy)
        toolbar.addWidget(self._restart_btn)
        toolbar.addItem(QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        outer.addLayout(toolbar)

        self._headline = QLabel("not yet detected")
        f = self._headline.font(); f.setBold(True); self._headline.setFont(f)
        outer.addWidget(self._headline)

        self._aux = QLabel("")
        self._aux.setStyleSheet("color: #6b7280;")
        outer.addWidget(self._aux)

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(
            ["Port", "Vendor", "Protocol", "Confidence"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setMaximumHeight(140)
        outer.addWidget(self._table)

        self._stamp = QLabel("never polled")
        self._stamp.setStyleSheet("color: #6b7280;")
        outer.addWidget(self._stamp)

        self._thread: Optional[QThread] = None
        self._worker: Optional[_NetworkEnvFetcher] = None
        self._poll = QTimer(self)
        self._poll.setInterval(NETWORK_ENV_POLL_MS)
        self._poll.timeout.connect(self.refresh_now)
        self._poll.start()
        QTimer.singleShot(400, self.refresh_now)

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
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    def _on_detected(self, result) -> None:
        if isinstance(result, Exception):
            self._headline.setText("detection failed")
            self._aux.setText(f"{type(result).__name__}: {result}")
            self._aux.setStyleSheet("color: #b91c1c;")
            self._stamp.setText(f"last attempt {time.strftime('%H:%M:%S')}")
            return

        env = result
        action = env.recommended_action
        best = env.best_upstream
        if action == "chain_upstream" and best is not None:
            head = f"✅ Will chain upstream → {best.display}"
            self._headline.setStyleSheet("color: #15803d;")
        elif action == "warn_conflict":
            head = (
                f"⚠ Enterprise TLS CA detected "
                f"({', '.join(env.foreign_root_cas[:2])}) — running un-chained."
            )
            self._headline.setStyleSheet("color: #b45309;")
        elif env.has_tun:
            head = (
                f"ℹ TUN-mode VPN active ({', '.join(env.tun_interfaces[:2])}) — "
                f"no chaining needed."
            )
            self._headline.setStyleSheet("color: #1e40af;")
        else:
            head = "No upstream proxy detected — running mitmproxy directly."
            self._headline.setStyleSheet("color: #374151;")
        self._headline.setText(head)

        aux_parts: list[str] = []
        if env.tun_interfaces:
            aux_parts.append(f"TUN: {', '.join(env.tun_interfaces[:3])}")
        if env.foreign_root_cas:
            aux_parts.append(f"foreign CA: {', '.join(env.foreign_root_cas)}")
        self._aux.setText("    ".join(aux_parts))
        self._aux.setStyleSheet(
            "color: #b45309;" if env.foreign_root_cas else "color: #6b7280;"
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

    def _on_chain_toggled(self, state) -> None:
        enabled = bool(state)
        self._manager.auto_chain_proxy = enabled
        self.chain_changed.emit(enabled)
        logger.info("auto_chain_proxy = %s", enabled)

    def _restart_proxy(self) -> None:
        if self._manager.is_running("proxy"):
            self._manager.stop_service("proxy")
            QTimer.singleShot(500, self._manager.start_proxy)
        else:
            self._manager.start_proxy()
        QTimer.singleShot(800, self.refresh_now)


# ---------------------------------------------------------------------------
# Capability pane
# ---------------------------------------------------------------------------

class CapabilityPanel(QGroupBox):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("Capability Check", parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 14, 10, 10)

        toolbar = QHBoxLayout()
        self._refresh_btn = QPushButton("↻ Re-check")
        self._refresh_btn.clicked.connect(self.refresh_now)
        toolbar.addWidget(self._refresh_btn)
        self._summary_label = QLabel("not yet run")
        self._summary_label.setStyleSheet("color: #6b7280;")
        toolbar.addWidget(self._summary_label)
        toolbar.addItem(QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        outer.addLayout(toolbar)

        scroller = QScrollArea(self)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        self._inner_layout = QVBoxLayout(inner)
        self._inner_layout.setContentsMargins(0, 0, 0, 0)
        self._inner_layout.setSpacing(2)
        self._inner_layout.addStretch()
        scroller.setWidget(inner)
        outer.addWidget(scroller)

        QTimer.singleShot(200, self.refresh_now)

    def refresh_now(self) -> None:
        while self._inner_layout.count() > 1:
            item = self._inner_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        results = run_capability_checks()
        for result in results:
            self._inner_layout.insertWidget(
                self._inner_layout.count() - 1,
                _build_capability_row(result),
            )

        ok = sum(1 for r in results if r.status == "ok")
        warn = sum(1 for r in results if r.status == "warn")
        err = sum(1 for r in results if r.status == "error")
        self._summary_label.setText(
            f"{ok} ok    {warn} warn    {err} error    (total {len(results)})"
        )


def _build_capability_row(result: CheckResult) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(6, 2, 6, 2)
    h.setSpacing(10)

    dot = QLabel()
    dot.setPixmap(render_status_dot(STATUS_HEX.get(result.status, "#94a3b8")))
    h.addWidget(dot)

    name = QLabel(result.name)
    name.setMinimumWidth(190)
    f = name.font(); f.setBold(True); name.setFont(f)
    h.addWidget(name)

    detail = QLabel(result.detail)
    detail.setStyleSheet("color: #374151;")
    detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
    h.addWidget(detail)

    h.addItem(QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))

    if result.hint:
        hint = QLabel(result.hint)
        hint.setStyleSheet("color: #6b7280; font-style: italic;")
        h.addWidget(hint)

    row.setToolTip(result.hint or result.detail or result.name)
    return row


# ---------------------------------------------------------------------------
# System tray
# ---------------------------------------------------------------------------

class PCETrayIcon(QSystemTrayIcon):
    show_panel_requested = Signal()
    quit_requested = Signal()

    def __init__(self, manager: ServiceManager, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._manager = manager
        self.setIcon(render_app_icon("#7c83ff"))
        self.setToolTip("PCE Control Panel")

        menu = QMenu()
        self._act_show = QAction("Open Control Panel", menu)
        self._act_show.triggered.connect(self.show_panel_requested)
        menu.addAction(self._act_show)
        menu.addSeparator()

        self._act_dashboard = QAction("Open Dashboard (browser)", menu)
        self._act_dashboard.triggered.connect(
            lambda: webbrowser.open(DASHBOARD_URL)
        )
        menu.addAction(self._act_dashboard)
        menu.addSeparator()

        self._act_start_all = QAction("Start All Services", menu)
        self._act_start_all.triggered.connect(self._start_all)
        menu.addAction(self._act_start_all)

        self._act_stop_all = QAction("Stop All Services", menu)
        self._act_stop_all.triggered.connect(self._manager.stop_all)
        menu.addAction(self._act_stop_all)

        menu.addSeparator()
        self._act_quit = QAction("Quit PCE (restores system state)", menu)
        self._act_quit.triggered.connect(self.quit_requested)
        menu.addAction(self._act_quit)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def set_rollup_color(self, color: str) -> None:
        hex_ = COLOR_HEX.get(color, "#7c83ff") if color != "grey" else "#7c83ff"
        self.setIcon(render_app_icon(hex_))
        self.setToolTip(f"PCE — lane health: {color}")

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_panel_requested.emit()

    def _start_all(self) -> None:
        self._manager.start_core()
        if self._manager.proxy_available():
            self._manager.start_proxy()
        self._manager.start_local_hook()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ControlPanel(QMainWindow):
    """Main desktop window. Hosts panes + tray + system-state guard."""

    def __init__(self, manager: ServiceManager):
        super().__init__()
        self._manager = manager
        self._force_quit = False

        # ── Crash recovery (BEFORE the rest of the panel mutates anything). ──
        # If a snapshot from a previous run is still on disk it means the
        # last PCE run didn't clean up. Revert immediately so the user
        # starts from a known-good baseline.
        self._guard = get_guard()
        try:
            recovered = self._guard.recover_from_crash()
            if recovered:
                logger.info("system state restored from stale snapshot")
        except Exception as exc:  # noqa: BLE001 — never block startup
            logger.warning("crash recovery failed: %r", exc)
        # Tie atexit / signal handlers so we still clean up on crash.
        try:
            self._guard.install_signal_handlers()
        except Exception:  # noqa: BLE001
            logger.debug("install_signal_handlers failed (non-fatal)", exc_info=True)

        self.setWindowTitle("PCE Control Panel")
        self.setWindowIcon(render_app_icon("#7c83ff"))
        self.resize(880, 940)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.services = ServicesPanel(manager, parent=central)
        self.lanes = LaneHealthPanel(parent=central)
        self.network_env = NetworkEnvPanel(manager, parent=central)
        self.capabilities = CapabilityPanel(parent=central)
        layout.addWidget(self.services)
        layout.addWidget(self.lanes)
        layout.addWidget(self.network_env)
        layout.addWidget(self.capabilities, stretch=1)

        # Tray
        self.tray = PCETrayIcon(manager, parent=self)
        self.tray.show_panel_requested.connect(self._show_window)
        self.tray.quit_requested.connect(self._real_quit)
        self.lanes.rollup_changed.connect(self.tray.set_rollup_color)
        self.tray.show()

        # Capture notifier — auto-starts after a short delay so the core
        # has a chance to come up.
        self.notifier = CaptureNotifier(parent=self)
        self.services.notify_muted_changed.connect(self.notifier.set_muted)
        self.notifier.captured.connect(self._on_capture)
        self.notifier.poll_error.connect(self._on_capture_poll_error)
        QTimer.singleShot(2500, self.notifier.start)

    # -- capture events --

    def _on_capture(self, delta: int, providers: dict) -> None:
        total = self.notifier.last_total or 0
        self.services.update_capture_count(total)
        self.services.flash_capture(delta, providers)
        # Tray balloon — keep short so it doesn't pile up if the user
        # is heavy-using AI tools.
        top = ", ".join(list(providers.keys())[:2]) if providers else "?"
        self.tray.showMessage(
            f"PCE captured +{delta}",
            f"From: {top}",
            QSystemTrayIcon.Information,
            1500,
        )

    def _on_capture_poll_error(self, msg: str) -> None:
        # Don't spam — just track on the label.
        self.services._cap_label.setText(f"captures: (core down)")
        self.services._cap_label.setStyleSheet("color: #b45309;")

    # -- window close → hide to tray, NOT full quit --

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
            "\"Quit PCE\" to fully exit and restore system state.",
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
        """The single funnel for "PCE is exiting, put things back."""
        try:
            self.notifier.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._manager.stop_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stop_all failed during teardown: %r", exc)
        # ServiceManager.stop_service("proxy") already calls guard.restore().
        # Call once more here so a user who never started proxy but did
        # change other state (future expansion) is still cleaned up.
        try:
            self._guard.restore(reason="control_panel_teardown")
        except Exception as exc:  # noqa: BLE001
            logger.warning("guard restore failed during teardown: %r", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    *,
    auto_start_core: bool = True,
    open_dashboard: bool = False,
    extra_services: tuple[str, ...] = (),
) -> int:
    """Spawn the Qt app, the control panel window and the tray."""
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
    "run",
]
