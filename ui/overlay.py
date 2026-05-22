"""
ui/overlay.py — ARIA Always-On-Top HUD Overlay
Semi-transparent floating panel: task name, mic status, gesture toggle, quick chat input.
"""

import sys
import json
import subprocess
import time
from datetime import datetime
from typing import Optional

import psutil
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QGraphicsDropShadowEffect,
    QFrame, QSizeGrip, QMenu
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtSignal, QPoint, QRect, QSize, QThread
)
from PyQt6.QtCore import pyqtProperty  # type: ignore[attr-defined]
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QLinearGradient,
    QFont, QFontDatabase, QPainterPath, QRegion, QCursor
)

try:
    import GPUtil  # type: ignore
except Exception:
    GPUtil = None

try:
    import pynvml  # type: ignore
except Exception:
    pynvml = None


# ── Colour tokens ──────────────────────────────────────────────────────────────
CLR_BG          = QColor(8,  11, 20,  200)   # deep space, 78% opaque
CLR_SURFACE     = QColor(13, 17, 32,  220)
CLR_BORDER      = QColor(0,  229, 255, 60)   # cyan dim
CLR_ACCENT      = QColor(0,  229, 255)        # electric cyan
CLR_ACCENT2     = QColor(139, 92, 246)        # violet
CLR_SUCCESS     = QColor(16,  185, 129)       # emerald
CLR_TEXT        = QColor(226, 232, 240)
CLR_MUTED       = QColor(100, 116, 139)
CLR_DANGER      = QColor(239, 68,  68)


STYLE_BASE = """
QWidget {
    background: transparent;
    color: #e2e8f0;
    font-family: 'Consolas', 'Courier New', monospace;
}
QLineEdit {
    background: rgba(13, 17, 32, 180);
    border: 1px solid rgba(0, 229, 255, 80);
    border-radius: 6px;
    color: #e2e8f0;
    padding: 6px 10px;
    font-size: 11px;
    font-family: 'Consolas', monospace;
    selection-background-color: rgba(0, 229, 255, 60);
}
QLineEdit:focus {
    border: 1px solid rgba(0, 229, 255, 200);
    background: rgba(0, 229, 255, 8);
}
QLineEdit::placeholder {
    color: #475569;
}
QPushButton {
    background: transparent;
    border: none;
    color: #94a3b8;
    font-size: 10px;
    padding: 2px 6px;
}
QPushButton:hover {
    color: #00e5ff;
}
"""


class PulsingDot(QWidget):
    """Animated status indicator dot."""

    def __init__(self, color: QColor = CLR_SUCCESS, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color  = color
        self._radius = 4.0
        self._anim   = QPropertyAnimation(self, b"dotRadius", self)
        self._anim.setDuration(900)
        self._anim.setStartValue(3.0)
        self._anim.setEndValue(5.0)
        self._anim.setEasingCurve(QEasingCurve.Type.SineCurve)
        self._anim.setLoopCount(-1)
        self._anim.start()

    def getDotRadius(self):  return self._radius
    def setDotRadius(self, v):
        self._radius = v
        self.update()
    dotRadius = pyqtProperty(float, getDotRadius, setDotRadius)

    def setColor(self, color: QColor):
        self._color = color
        self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QColor(self._color)
        glow.setAlpha(50)
        p.setBrush(QBrush(glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRect(0, 0, 10, 10))
        p.setBrush(QBrush(self._color))
        cx, cy = 5, 5
        r = self._radius
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))


class GlowLabel(QLabel):
    """Label that renders with a soft cyan glow."""

    def __init__(self, text="", glow_color: QColor = CLR_ACCENT, parent=None):
        super().__init__(text, parent)
        fx = QGraphicsDropShadowEffect(self)
        fx.setBlurRadius(12)
        fx.setOffset(0, 0)
        fx.setColor(glow_color)
        self.setGraphicsEffect(fx)


class ScanlineOverlay(QWidget):
    """Subtle scanline texture painted on top of everything."""

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(0, 229, 255, 6))
        pen.setWidth(1)
        p.setPen(pen)
        y = 0
        while y < self.height():
            p.drawLine(0, y, self.width(), y)
            y += 3


class CornerAccent(QWidget):
    """Decorative L-shaped corner bracket."""

    def __init__(self, corner: str = "tl", size: int = 14, parent=None):
        super().__init__(parent)
        self.corner = corner
        self.sz     = size
        self.setFixedSize(size, size)

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(CLR_ACCENT, 1.5)
        p.setPen(pen)
        s = self.sz - 1
        if self.corner == "tl":
            p.drawLine(0, s, 0, 0); p.drawLine(0, 0, s, 0)
        elif self.corner == "tr":
            p.drawLine(0, 0, s, 0); p.drawLine(s, 0, s, s)
        elif self.corner == "bl":
            p.drawLine(0, 0, 0, s); p.drawLine(0, s, s, s)
        elif self.corner == "br":
            p.drawLine(s, 0, s, s); p.drawLine(0, s, s, s)


class PieGauge(QWidget):
    """Circular gauge with centered percentage."""

    def __init__(self, color: QColor = CLR_ACCENT, parent=None):
        super().__init__(parent)
        self._color = color
        self._percent = 0.0
        self._text = "0%"
        self.setFixedSize(48, 48)

    def set_value(self, percent: float, text: str) -> None:
        self._percent = max(0.0, min(100.0, float(percent)))
        self._text = text
        self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(5, 5, -5, -5)

        base_pen = QPen(QColor(30, 45, 69, 160), 5)
        p.setPen(base_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        arc_pen = QPen(self._color, 5)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc_pen)
        span = -int(self._percent / 100.0 * 360 * 16)
        p.drawArc(rect, 90 * 16, span)

        p.setPen(QPen(CLR_TEXT))
        font = QFont("Consolas", 8)
        font.setBold(True)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


class StatTile(QWidget):
    """Name + pie gauge + value text."""

    def __init__(self, label: str, color: QColor, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self._label = QLabel(label)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            "font-size:8px; letter-spacing:1px; color:#94a3b8;"
        )

        self._gauge = PieGauge(color)

        self._value = QLabel("--")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value.setStyleSheet(
            "font-size:8px; letter-spacing:1px; color:#e2e8f0;"
        )

        layout.addWidget(self._label)
        layout.addWidget(self._gauge, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._value)

        self.setFixedWidth(64)

    def set_value(self, percent: float, text: str) -> None:
        self._gauge.set_value(percent, f"{percent:0.0f}%")
        self._value.setText(text)


class AriaOverlay(QWidget):
    """
    Main always-on-top HUD overlay for ARIA.

    Signals
    -------
    command_submitted(str)   — user pressed Enter in the quick-input box
    gesture_toggled(bool)    — gesture mode turned on/off
    mic_toggled(bool)        — mic mute toggled
    """

    command_submitted = pyqtSignal(str)
    gesture_toggled   = pyqtSignal(bool)
    mic_toggled       = pyqtSignal(bool)
    model_selected     = pyqtSignal(str)

    # Public state
    MIC_IDLE      = "IDLE"
    MIC_LISTENING = "LISTENING"
    MIC_PROCESSING= "PROCESSING"

    def __init__(self):
        super().__init__()
        self._drag_pos      = None
        self._mic_state     = self.MIC_IDLE
        self._gesture_on    = False
        self._mic_muted     = False
        self._task_name     = "No active task"
        self._collapsed     = False
        self._last_disk_io  = None
        self._last_disk_ts  = None
        self._nvml_ready    = False
        self._selected_model = self._get_default_model()

        self._init_window()
        self._build_ui()
        self._apply_styles()

        self._sync_model_button()

        # Blink timer for processing state
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_tick)
        self._blink_phase = True

        psutil.cpu_percent(interval=None)
        self._init_nvml()
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(1000)

    # ── Window setup ──────────────────────────────────────────────────────────

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMinimumWidth(280)
        self.resize(420, 290)
        # Position: top-right of primary screen
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.geometry()
            self.move(geo.width() - 330, 20)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # ── Header row ────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(6)

        self._aria_label = GlowLabel("◈ ARIA", CLR_ACCENT)
        self._aria_label.setStyleSheet(
            "font-size: 11px; font-weight: bold; letter-spacing: 3px; color: #00e5ff;"
        )

        self._version_label = QLabel("v1.0")
        self._version_label.setStyleSheet(
            "font-size: 9px; color: #334155; letter-spacing: 1px; margin-top:2px;"
        )

        self._collapse_btn = QPushButton("▾")
        self._collapse_btn.setFixedSize(18, 18)
        self._collapse_btn.setToolTip("Collapse")
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(18, 18)
        self._close_btn.setToolTip("Hide overlay")
        self._close_btn.clicked.connect(self.hide)

        header.addWidget(self._aria_label)
        header.addWidget(self._version_label)
        header.addStretch()
        header.addWidget(self._collapse_btn)
        header.addWidget(self._close_btn)
        root.addLayout(header)

        # Divider line
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: qlineargradient("
            "x1:0,y1:0,x2:1,y2:0,"
            "stop:0 transparent, stop:0.3 rgba(0,229,255,80),"
            "stop:0.7 rgba(0,229,255,80), stop:1 transparent);")
        root.addWidget(line)

        # ── Collapsible body ──────────────────────────────────────────────────
        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 4, 0, 0)
        body_layout.setSpacing(7)

        # Task row
        task_row = QHBoxLayout()
        task_icon = QLabel("◎")
        task_icon.setStyleSheet("color: #8b5cf6; font-size: 10px;")
        self._task_label = QLabel(self._task_name)
        self._task_label.setStyleSheet(
            "color: #e2e8f0; font-size: 11px; font-family: Consolas;"
        )
        self._task_label.setWordWrap(False)
        task_row.addWidget(task_icon)
        task_row.addWidget(self._task_label)
        task_row.addStretch()
        body_layout.addLayout(task_row)

        # Status row (mic + gesture)
        status_row = QHBoxLayout()
        status_row.setSpacing(10)

        # Mic status
        self._mic_dot    = PulsingDot(CLR_MUTED)
        self._mic_label  = QLabel("MIC · IDLE")
        self._mic_label.setStyleSheet(
            "font-size: 9px; letter-spacing: 2px; color: #64748b;"
        )
        mic_btn = QPushButton("⏺")
        mic_btn.setToolTip("Toggle mute")
        mic_btn.setFixedSize(22, 18)
        mic_btn.setStyleSheet(
            "font-size: 11px; color:#334155; border:none; background:transparent;"
        )
        mic_btn.clicked.connect(self._toggle_mic)

        # Gesture toggle
        self._gesture_btn = QPushButton("GESTURE  OFF")
        self._gesture_btn.setCheckable(True)
        self._gesture_btn.setStyleSheet(self._gesture_style(False))
        self._gesture_btn.clicked.connect(self._toggle_gesture)
        self._gesture_btn.setFixedHeight(20)

        status_row.addWidget(self._mic_dot)
        status_row.addWidget(self._mic_label)
        status_row.addWidget(mic_btn)
        status_row.addStretch()
        status_row.addWidget(self._gesture_btn)
        body_layout.addLayout(status_row)

        # ── Time + telemetry ───────────────────────────────────────────────
        time_row = QHBoxLayout()
        time_row.setSpacing(8)

        self._time_label = GlowLabel("--:--:--", CLR_ACCENT)
        self._time_label.setStyleSheet(
            "font-size:16px; letter-spacing:2px; color:#e2e8f0;"
        )
        self._date_label = QLabel("-- --- ----")
        self._date_label.setStyleSheet(
            "font-size:9px; letter-spacing:2px; color:#64748b;"
        )

        time_col = QVBoxLayout()
        time_col.setSpacing(1)
        time_col.addWidget(self._time_label)
        time_col.addWidget(self._date_label)
        time_row.addLayout(time_col)
        time_row.addStretch()
        body_layout.addLayout(time_row)

        stats_grid = QGridLayout()
        stats_grid.setSpacing(8)

        self._cpu_tile = StatTile("CPU", CLR_ACCENT)
        self._gpu_tile = StatTile("GPU", CLR_ACCENT2)
        self._ram_tile = StatTile("RAM", CLR_SUCCESS)
        self._ssd_tile = StatTile("SSD", QColor(251, 191, 36))
        self._disk_tile = StatTile("C:\\", QColor(148, 163, 184))

        stats_grid.addWidget(self._cpu_tile, 0, 0)
        stats_grid.addWidget(self._gpu_tile, 0, 1)
        stats_grid.addWidget(self._ram_tile, 0, 2)
        stats_grid.addWidget(self._ssd_tile, 0, 3)
        stats_grid.addWidget(self._disk_tile, 0, 4)

        stats_widget = QWidget()
        stats_widget.setLayout(stats_grid)
        body_layout.addWidget(stats_widget)

        # ── Quick chat input ──────────────────────────────────────────────────
        chat_row = QHBoxLayout()
        chat_row.setSpacing(6)

        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText("⌘  Quick command…")
        self._chat_input.setFixedHeight(28)
        self._chat_input.returnPressed.connect(self._on_submit)

        self._model_btn = QPushButton("MODEL")
        self._model_btn.setFixedSize(52, 28)
        self._model_btn.setToolTip("Select Ollama model")
        self._model_btn.setStyleSheet(
            "background: rgba(30,45,69,80); border: 1px solid rgba(30,45,69,180);"
            "border-radius:6px; color:#94a3b8; font-size:9px; letter-spacing:1px;"
        )
        self._model_btn.clicked.connect(self._open_model_menu)

        send_btn = QPushButton("↵")
        send_btn.setFixedSize(28, 28)
        send_btn.setStyleSheet(
            "background: rgba(0,229,255,15); border: 1px solid rgba(0,229,255,60);"
            "border-radius:6px; color:#00e5ff; font-size:12px;"
        )
        send_btn.clicked.connect(self._on_submit)

        chat_row.addWidget(self._chat_input)
        chat_row.addWidget(self._model_btn)
        chat_row.addWidget(send_btn)
        body_layout.addLayout(chat_row)

        root.addWidget(self._body)

        # Corner accents (purely decorative, overlaid)
        self._corners = [
            CornerAccent("tl", 12, self),
            CornerAccent("tr", 12, self),
            CornerAccent("bl", 12, self),
            CornerAccent("br", 12, self),
        ]

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = self.rect().adjusted(1, 1, -1, -1)

        # Background
        p.setBrush(QBrush(CLR_BG))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 10, 10)

        # Border gradient
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor(0, 229, 255, 80))
        grad.setColorAt(0.5, QColor(139, 92, 246, 50))
        grad.setColorAt(1.0, QColor(0, 229, 255, 30))
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QBrush(grad), 1.2)
        p.setPen(pen)
        p.drawRoundedRect(r, 10, 10)

        # Top accent bar
        bar_grad = QLinearGradient(20, 0, self.width() - 20, 0)
        bar_grad.setColorAt(0.0, QColor(0, 229, 255, 0))
        bar_grad.setColorAt(0.3, QColor(0, 229, 255, 120))
        bar_grad.setColorAt(0.7, QColor(139, 92, 246, 100))
        bar_grad.setColorAt(1.0, QColor(0, 229, 255, 0))
        p.setBrush(QBrush(bar_grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(20, 0, self.width() - 40, 2, 1, 1)

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        w, h = self.width(), self.height()
        corners = self._corners
        corners[0].move(4, 4)
        corners[1].move(w - 18, 4)
        corners[2].move(4, h - 18)
        corners[3].move(w - 18, h - 18)

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(STYLE_BASE)

    @staticmethod
    def _gesture_style(on: bool) -> str:
        if on:
            return (
                "QPushButton { background: rgba(0,229,255,20); border: 1px solid rgba(0,229,255,150);"
                " border-radius:4px; color:#00e5ff; font-size:9px; letter-spacing:2px; padding:0 6px; }"
                "QPushButton:hover { background: rgba(0,229,255,35); }"
            )
        return (
            "QPushButton { background: rgba(30,45,69,80); border: 1px solid rgba(30,45,69,180);"
            " border-radius:4px; color:#475569; font-size:9px; letter-spacing:2px; padding:0 6px; }"
            "QPushButton:hover { border-color: rgba(0,229,255,80); color:#64748b; }"
        )

    # ── Telemetry ───────────────────────────────────────────────────────────

    def _refresh_stats(self) -> None:
        now = datetime.now()
        self._time_label.setText(now.strftime("%H:%M:%S"))
        self._date_label.setText(now.strftime("%a %d %b %Y").upper())

        cpu = psutil.cpu_percent(interval=None)
        self._cpu_tile.set_value(cpu, f"{cpu:0.0f}%")

        ram = psutil.virtual_memory().percent
        self._ram_tile.set_value(ram, f"{ram:0.0f}%")

        gpu = self._get_gpu_load()
        if gpu is None:
            gpu = 0.0
        self._gpu_tile.set_value(gpu, f"{gpu:0.0f}%")

        ssd_speed = self._get_disk_speed_mb()
        ssd_percent = min(100.0, max(0.0, (ssd_speed / 1000.0) * 100.0))
        self._ssd_tile.set_value(ssd_percent, f"{ssd_speed:0.0f} MB/s")

        try:
            usage = psutil.disk_usage("C:\\")
            free_pct = (usage.free / max(1, usage.total)) * 100.0
            self._disk_tile.set_value(free_pct, f"{free_pct:0.0f}% free")
        except Exception:
            self._disk_tile.set_value(0.0, "0% free")

    def _get_gpu_load(self) -> Optional[float]:
        if GPUtil is None:
            return self._get_nvml_load()
        try:
            gpus = GPUtil.getGPUs()
            if not gpus:
                return self._get_nvml_load()
            return max(0.0, min(100.0, gpus[0].load * 100.0))
        except Exception:
            return self._get_nvml_load()

    def _init_nvml(self) -> None:
        if pynvml is None:
            return
        try:
            pynvml.nvmlInit()
            self._nvml_ready = True
        except Exception:
            self._nvml_ready = False

    def _get_nvml_load(self) -> Optional[float]:
        if pynvml is None or not self._nvml_ready:
            return None
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return max(0.0, min(100.0, float(util.gpu)))
        except Exception:
            return None

    def _get_disk_speed_mb(self) -> float:
        try:
            io = psutil.disk_io_counters()
            now = time.monotonic()
            if self._last_disk_io is None or self._last_disk_ts is None:
                self._last_disk_io = io
                self._last_disk_ts = now
                return 0.0
            last_io = self._last_disk_io
            if last_io is None:
                return 0.0
            dt = max(1e-6, now - self._last_disk_ts)
            d_read = io.read_bytes - last_io.read_bytes
            d_write = io.write_bytes - last_io.write_bytes
            self._last_disk_io = io
            self._last_disk_ts = now
            return max(0.0, (d_read + d_write) / dt / (1024 * 1024))
        except Exception:
            return 0.0

    # ── Dragging ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, a0):
        if a0 is None:
            return
        if a0.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = a0.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, a0):
        if a0 is None:
            return
        if self._drag_pos is not None and a0.buttons() == Qt.MouseButton.LeftButton:
            self.move(a0.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, a0):
        self._drag_pos = None

    # ── Public API ────────────────────────────────────────────────────────────

    def set_task(self, name: str):
        """Update the current task display."""
        self._task_name = name
        self._task_label.setText(name)

    def set_mic_state(self, state: str):
        """
        state: 'IDLE' | 'LISTENING' | 'PROCESSING'
        """
        self._mic_state = state
        if state == self.MIC_LISTENING:
            self._mic_dot.setColor(CLR_SUCCESS)
            self._mic_label.setText("MIC · LISTENING")
            self._mic_label.setStyleSheet(
                "font-size:9px; letter-spacing:2px; color:#10b981;"
            )
            self._blink_timer.stop()
        elif state == self.MIC_PROCESSING:
            self._mic_dot.setColor(CLR_ACCENT)
            self._mic_label.setText("MIC · PROCESSING")
            self._mic_label.setStyleSheet(
                "font-size:9px; letter-spacing:2px; color:#00e5ff;"
            )
            self._blink_timer.start(450)
        else:
            self._mic_dot.setColor(CLR_MUTED)
            self._mic_label.setText("MIC · IDLE")
            self._mic_label.setStyleSheet(
                "font-size:9px; letter-spacing:2px; color:#64748b;"
            )
            self._blink_timer.stop()

    def set_opacity(self, value: float):
        """0.0 – 1.0"""
        self.setWindowOpacity(max(0.1, min(1.0, value)))

    def set_voice_enabled(self, enabled: bool):
        """Sync the overlay mic toggle state from an external controller."""
        self._mic_muted = not enabled
        if not enabled:
            self._mic_dot.setColor(CLR_DANGER)
            self._mic_label.setText("MIC · MUTED")
            self._mic_label.setStyleSheet(
                "font-size:9px; letter-spacing:2px; color:#ef4444;"
            )
            self._blink_timer.stop()
        else:
            # Restore visual state based on current mic state.
            self.set_mic_state(self._mic_state)

    def set_gesture_enabled(self, enabled: bool):
        """Sync the gesture toggle from an external controller."""
        self._gesture_on = bool(enabled)
        self._gesture_btn.blockSignals(True)
        try:
            self._gesture_btn.setChecked(self._gesture_on)
            self._gesture_btn.setText(f"GESTURE  {'ON' if self._gesture_on else 'OFF'}")
            self._gesture_btn.setStyleSheet(self._gesture_style(self._gesture_on))
        finally:
            self._gesture_btn.blockSignals(False)

    # ── Private slots ─────────────────────────────────────────────────────────

    def _on_submit(self):
        text = self._chat_input.text().strip()
        if text:
            self.command_submitted.emit(text)
            self._chat_input.clear()

    def _toggle_gesture(self, checked: bool):
        self._gesture_on = checked
        self._gesture_btn.setText(f"GESTURE  {'ON' if checked else 'OFF'}")
        self._gesture_btn.setStyleSheet(self._gesture_style(checked))
        self.gesture_toggled.emit(checked)

    def _toggle_mic(self):
        self._mic_muted = not self._mic_muted
        self.mic_toggled.emit(not self._mic_muted)
        if self._mic_muted:
            self._mic_dot.setColor(CLR_DANGER)
            self._mic_label.setText("MIC · MUTED")
            self._mic_label.setStyleSheet(
                "font-size:9px; letter-spacing:2px; color:#ef4444;"
            )

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        self._body.setVisible(not self._collapsed)
        self._collapse_btn.setText("▸" if self._collapsed else "▾")
        self.adjustSize()

    def _blink_tick(self):
        self._blink_phase = not self._blink_phase
        color = CLR_ACCENT if self._blink_phase else CLR_MUTED
        self._mic_dot.setColor(color)

    # ── Model selection ─────────────────────────────────────────────────────

    def _open_model_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: rgba(13,17,32,235); color:#e2e8f0;"
            " border:1px solid rgba(0,229,255,60); }"
            "QMenu::item:selected { background: rgba(0,229,255,30); }"
        )

        models = self._get_ollama_models()
        if self._selected_model and self._selected_model not in models:
            models = [self._selected_model] + models
        if not models:
            action = menu.addAction("No Ollama models found")
            action.setEnabled(False)
        else:
            for name in models:
                label = name
                if name == self._selected_model:
                    label = f"✓ {name}"
                action = menu.addAction(label)
                action.triggered.connect(lambda _=False, n=name: self._set_model(n))

        menu.exec(self._model_btn.mapToGlobal(self._model_btn.rect().bottomLeft()))

    def _set_model(self, name: str):
        self._selected_model = name
        self._sync_model_button()
        self.model_selected.emit(name)

    def _sync_model_button(self) -> None:
        short = (self._selected_model or "MODEL").split(":")[0].upper()[:6]
        self._model_btn.setText(short)

    def _get_default_model(self) -> str:
        try:
            from config.settings import get as get_setting
            return str(get_setting("llm.model", ""))
        except Exception:
            return ""

    def _get_ollama_models(self) -> list[str]:
        try:
            result = subprocess.run(
                ["ollama", "list", "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                payload = json.loads(result.stdout or "{}").get("models", [])
                models = [m.get("name", "") for m in payload if m.get("name")]
                if models:
                    return models
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            if result.returncode != 0:
                return []
            lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
            if len(lines) <= 1:
                return []
            models: list[str] = []
            for line in lines[1:]:
                name = line.split()[0]
                if name:
                    models.append(name)
            return models
        except Exception:
            return []


 