"""The PySide6 JARVIS window: the GLSL core as a glowing backdrop, with a
QPainter HUD overlay and a dev-input line, all driven by the same EventBus.

Layering (bottom -> top):
    GLCore            full-window GPU orb with real bloom
    HudOverlay        translucent QPainter HUD (gauges, clock, telemetry, log)
    QLineEdit         dev input (F2 toggles, Enter submits)

Bus events arrive on worker threads; a tiny QObject bridge re-emits them as a
queued Qt signal so all UI mutation happens on the GUI thread.
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import QLineEdit, QMainWindow, QWidget

from ..core.event_bus import Event, EventBus
from ..core.states import JarvisState
from ..skills.system_info import handler as sysinfo
from .gl_core import GLCore

# palette (matches the Iron-Man HUD aesthetic of the Tk version)
_ACCENT = QColor("#39c6ff")
_ACCENT_HI = QColor("#7be3ff")
_DIM = QColor("#1b5e80")
_DIMMER = QColor("#0d3554")
_OK = QColor("#42ff9e")
_WARN = QColor("#ffb454")
_ERR = QColor("#ff5a5a")
_LOG_COL = QColor("#5fa8c9")
_FONT = "Consolas"


class _Bridge(QObject):
    """Re-emits bus messages onto the GUI thread via a queued signal."""
    event = Signal(object)


class HudOverlay(QWidget):
    """Transparent HUD painted over the GL core. Mouse-transparent so the dev
    input below it still receives focus/clicks."""

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self.state = JarvisState.IDLE
        self.metrics: dict = {"cpu": None, "memory": None,
                              "disk": None, "battery": None}
        self.log_lines: list[tuple[str, QColor]] = []
        self._start = time.time()
        self._frames = 0
        self._fps = 0

    # ---- data in -----------------------------------------------------------
    def set_state(self, state: JarvisState) -> None:
        self.state = state

    def set_metrics(self, m: dict) -> None:
        self.metrics = m

    def append_log(self, payload: dict) -> None:
        level = payload.get("level", "info")
        source = payload.get("source", "core")
        message = payload.get("message", "")
        ts = datetime.now().strftime("%H:%M:%S")
        col = {"warn": _WARN, "error": _ERR}.get(level, _LOG_COL)
        prefix = {"warn": "!", "error": "X"}.get(level, "›")
        self.log_lines.append((f"{ts} {prefix} [{source}] {message}", col))
        self.log_lines = self.log_lines[-7:]

    def tick_fps(self) -> None:
        self._frames += 1

    def sample_fps(self) -> None:
        self._fps = self._frames
        self._frames = 0

    # ---- painting ----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        w, h = self.width(), self.height()

        self._brackets(p, w, h)
        self._title(p)
        self._status(p)
        self._gauges(p)
        self._indicators(p, w)
        self._clock_cluster(p, w)
        self._telemetry(p, h)
        self._heading(p, w, h)
        p.end()

    def _text(self, p, x, y, s, col, size, *, bold=False, anchor="w") -> None:
        font = QFont(_FONT, size)
        font.setBold(bold)
        p.setFont(font)
        p.setPen(QPen(col))
        metrics = p.fontMetrics()
        if anchor == "e":
            x -= metrics.horizontalAdvance(s)
        elif anchor == "c":
            x -= metrics.horizontalAdvance(s) / 2
        p.drawText(int(x), int(y + metrics.ascent() / 2), s)

    def _brackets(self, p, w, h) -> None:
        p.setPen(QPen(_DIMMER, 2))
        leg, m = 22, 12
        for (x0, y0, x1, y1) in (
            (m, m, m + leg, m), (m, m, m, m + leg),
            (w - m, m, w - m - leg, m), (w - m, m, w - m, m + leg),
            (m, h - m, m + leg, h - m), (m, h - m, m, h - m - leg),
            (w - m, h - m, w - m - leg, h - m), (w - m, h - m, w - m, h - m - leg),
        ):
            p.drawLine(x0, y0, x1, y1)

    def _title(self, p) -> None:
        self._text(p, 34, 30, "J.A.R.V.I.S", _ACCENT, 26, bold=True)
        self._text(p, 36, 58, "JUST  A  RATHER  VERY  INTELLIGENT  SYSTEM",
                   _DIM, 8, bold=True)

    def _status(self, p) -> None:
        label, col = {
            JarvisState.IDLE:      ("◉ ONLINE", _OK),
            JarvisState.LISTENING: ("◉ LISTENING", _ACCENT_HI),
            JarvisState.THINKING:  ("◉ PROCESSING", _ACCENT_HI),
            JarvisState.SPEAKING:  ("◉ SPEAKING", _OK),
            JarvisState.ERROR:     ("⚠ ALERT", _ERR),
        }[self.state]
        self._text(p, 36, 84, "SYSTEM STATUS", _DIM, 8)
        self._text(p, 36, 102, label, col, 13, bold=True)

    def _gauges(self, p) -> None:
        rows = [("CPU LOAD", "cpu"), ("MEMORY", "memory"),
                ("DISK", "disk"), ("ENERGY", "battery")]
        y = 138
        for label, key in rows:
            self._text(p, 34, y, label, _DIM, 8)
            val = self.metrics.get(key)
            self._text(p, 230, y, "--%" if val is None else f"{val:5.1f}%",
                       _ACCENT, 9, bold=True, anchor="e")
            seg_w, gap, x0, n = 9, 1, 34, 20
            lit = 0 if val is None else int(round(n * val / 100.0))
            for i in range(n):
                x = x0 + i * (seg_w + gap)
                if i < lit:
                    if val > 88:   c = _ERR
                    elif val > 70: c = _WARN
                    else:          c = _ACCENT_HI if i >= lit - 2 else _ACCENT
                else:
                    c = QColor("#06141f")
                p.fillRect(x, y + 8, seg_w, 10, c)
            y += 42

    def _indicators(self, p, w) -> None:
        rows = [("LISTENING", JarvisState.LISTENING),
                ("PROCESSING", JarvisState.THINKING),
                ("SPEAKING", JarvisState.SPEAKING)]
        ry = 92
        for label, st in rows:
            on = (st == self.state)
            p.setBrush(_ACCENT_HI if on else _DIMMER)
            p.setPen(Qt.NoPen)
            p.drawEllipse(w - 30, ry - 5, 10, 10)
            self._text(p, w - 38, ry, label, _ACCENT_HI if on else _DIM,
                       13, bold=True, anchor="e")
            ry += 36

    def _clock_cluster(self, p, w) -> None:
        now = datetime.now()
        self._text(p, w - 34, 30, now.strftime("%H:%M:%S"), _ACCENT_HI, 18,
                   bold=True, anchor="e")
        self._text(p, w - 34, 54, now.strftime("%a %d %b %Y").upper(), _DIM, 9,
                   anchor="e")
        up = int(time.time() - self._start)
        hh, rem = divmod(up, 3600)
        mm, ss = divmod(rem, 60)
        self._text(p, w - 34, 72, f"UPTIME {hh:02d}:{mm:02d}:{ss:02d}", _DIM, 9,
                   anchor="e")
        self._text(p, w - 34, 90, f"FPS {self._fps:02d}", _DIM, 9, anchor="e")

    def _telemetry(self, p, h) -> None:
        self._text(p, 34, h - 116, "◆ TELEMETRY", _DIM, 8, bold=True)
        y = h - 96
        for line, col in self.log_lines:
            self._text(p, 34, y, line, col, 9)
            y += 14

    def _heading(self, p, w, h) -> None:
        deg = int((time.time() * 6) % 360)
        comp = ("N" if deg < 45 or deg >= 315 else "E" if deg < 135
                else "S" if deg < 225 else "W")
        self._text(p, w - 34, h - 60, "HEADING", _DIM, 8, bold=True, anchor="e")
        self._text(p, w - 34, h - 40, f"{deg:03d}°{comp}", _ACCENT, 14,
                   bold=True, anchor="e")


class JarvisQtWindow(QMainWindow):
    def __init__(self, config, bus: EventBus, orchestrator) -> None:
        super().__init__()
        self.config = config
        self.bus = bus
        self.orch = orchestrator
        self._on_close_cb = None
        self._metric_tick = 0

        self.setWindowTitle("J.A.R.V.I.S")
        self.resize(config.window_width, config.window_height)
        self.setMinimumSize(900, 600)
        self.setStyleSheet("background:#02060d;")

        self.gl = GLCore(config)
        self.setCentralWidget(self.gl)

        # HUD + dev input are children of the GL widget so they composite above it
        self.hud = HudOverlay(config, self.gl)
        self.hud.setGeometry(self.gl.rect())

        self.dev = QLineEdit(self.gl)
        self.dev.setPlaceholderText("▶  type a command and press Enter…")
        self.dev.setStyleSheet(
            "QLineEdit{background:#06141f;color:#7be3ff;border:1px solid #1b5e80;"
            "border-radius:3px;padding:6px 10px;}"
            "QLineEdit:focus{border:1px solid #39c6ff;}")
        self.dev.setFont(QFont(_FONT, 11))
        self.dev.returnPressed.connect(self._submit_dev)
        self._dev_visible = True

        QShortcut(QKeySequence(Qt.Key_Escape), self,
                  activated=lambda: self.orch.tts.stop())
        QShortcut(QKeySequence(Qt.Key_F2), self, activated=self._toggle_dev)

        # bus bridge -> GUI thread
        self._bridge = _Bridge()
        self._bridge.event.connect(self._on_event)
        bus.subscribe_all(lambda m: self._bridge.event.emit(m))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._frame)

    # ---- lifecycle (mirrors the Tk window's public surface) ----------------
    def set_on_close(self, cb) -> None:
        self._on_close_cb = cb

    def start(self) -> None:
        self._place_dev()
        self.dev.setFocus()
        self._timer.start(max(16, int(1000 / self.config.target_fps)))

    def closeEvent(self, event) -> None:
        self._timer.stop()
        if self._on_close_cb:
            self._on_close_cb()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.hud.setGeometry(self.gl.rect())
        self._place_dev()

    # ---- frame loop --------------------------------------------------------
    def _frame(self) -> None:
        self.gl.update()
        self.hud.tick_fps()
        self._metric_tick += 1
        if self._metric_tick % max(1, self.config.target_fps) == 0:
            self.hud.set_metrics(sysinfo.read_metrics())
            self.hud.sample_fps()
        self.hud.update()

    # ---- events ------------------------------------------------------------
    def _on_event(self, msg) -> None:
        ev = msg.event
        if ev == Event.STATE_CHANGED:
            self.gl.set_state(msg.payload)
            self.hud.set_state(msg.payload)
        elif ev == Event.AUDIO_LEVEL:
            self.gl.push_audio(msg.payload)
        elif ev == Event.SPEAK_LEVEL:
            self.gl.push_speak(msg.payload)
        elif ev == Event.LOG:
            self.hud.append_log(msg.payload)

    # ---- dev input ---------------------------------------------------------
    def _place_dev(self) -> None:
        if not self._dev_visible:
            self.dev.hide()
            return
        w, h = self.gl.width(), self.gl.height()
        dw = int(w * 0.55)
        self.dev.setGeometry((w - dw) // 2, int(h * 0.93), dw, 34)
        self.dev.show()
        self.dev.raise_()

    def _toggle_dev(self) -> None:
        self._dev_visible = not self._dev_visible
        self._place_dev()
        if self._dev_visible:
            self.dev.setFocus()

    def _submit_dev(self) -> None:
        text = self.dev.text().strip()
        self.dev.clear()
        if text:
            self.orch.submit_text(text)
