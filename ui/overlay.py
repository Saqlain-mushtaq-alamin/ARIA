"""
ui/overlay.py — ARIA Iron Man HUD Overlay
Cyberpunk always-on-top transparent HUD: task, mic, gesture, chat, system telemetry.
Inspired by Tony Stark's helmet display — compact, advanced, fully transparent bg.
"""

import sys
import json
import math
import subprocess
import time
from datetime import datetime
from typing import Optional, cast

import psutil
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QGraphicsDropShadowEffect,
    QFrame, QSizeGrip, QMenu
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtSignal, QPoint, QRect, QSize, QThread, QRectF, QPointF
)
from PyQt6.QtCore import pyqtProperty  # type: ignore[attr-defined]
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QLinearGradient, QRadialGradient,
    QFont, QFontDatabase, QPainterPath, QRegion, QCursor, QConicalGradient,
    QPolygonF
)

try:
    import GPUtil  # type: ignore
except Exception:
    GPUtil = None

try:
    import pynvml  # type: ignore
except Exception:
    pynvml = None


# ── Iron Man Colour Palette ────────────────────────────────────────────────────
CLR_BG          = QColor(4,   8,  18,  210)   # near-black deep space
CLR_SURFACE     = QColor(8,  14,  28,  230)
CLR_BORDER      = QColor(0,  220, 255,  55)   # cyan dim
CLR_ACCENT      = QColor(0,  220, 255)         # electric cyan (Jarvis blue)
CLR_ACCENT2     = QColor(255, 140,  0)         # iron man gold/amber
CLR_ACCENT3     = QColor(180,  60, 255)        # purple-violet
CLR_SUCCESS     = QColor(0,   255, 140)        # emerald green
CLR_TEXT        = QColor(200, 230, 255)
CLR_MUTED       = QColor(70,  100, 130)
CLR_DANGER      = QColor(255,  60,  60)
CLR_WARN        = QColor(255, 200,   0)
CLR_SCANLINE    = QColor(0,   180, 255,   8)


STYLE_BASE = """
QWidget {
    background: transparent;
    color: #c8e6ff;
    font-family: 'Consolas', 'Courier New', monospace;
}
QLineEdit {
    background: rgba(0, 10, 30, 190);
    border: 1px solid rgba(0, 220, 255, 70);
    border-radius: 4px;
    color: #c8e6ff;
    padding: 5px 10px;
    font-size: 11px;
    font-family: 'Consolas', monospace;
    selection-background-color: rgba(0, 220, 255, 55);
}
QLineEdit:focus {
    border: 1px solid rgba(0, 220, 255, 220);
    background: rgba(0, 220, 255, 10);
}
QLineEdit::placeholder {
    color: #2a4060;
}
QPushButton {
    background: transparent;
    border: none;
    color: #7090a0;
    font-size: 10px;
    padding: 2px 6px;
}
QPushButton:hover {
    color: #00dcff;
}
"""


# ─────────────────────────── Animated Components ──────────────────────────────

class PulsingDot(QWidget):
    def __init__(self, color: QColor = CLR_SUCCESS, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color  = color
        self._radius = 4.0
        self._anim   = QPropertyAnimation(self, b"dotRadius", self)
        self._anim.setDuration(900)
        self._anim.setStartValue(2.5)
        self._anim.setEndValue(4.5)
        self._anim.setEasingCurve(QEasingCurve.Type.SineCurve)
        self._anim.setLoopCount(-1)
        self._anim.start()

    def getDotRadius(self):  return self._radius
    def setDotRadius(self, v):
        self._radius = v; self.update()
    dotRadius = pyqtProperty(float, getDotRadius, setDotRadius)

    def setColor(self, color: QColor):
        self._color = color; self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QColor(self._color); glow.setAlpha(45)
        p.setBrush(QBrush(glow)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRect(0, 0, 10, 10))
        p.setBrush(QBrush(self._color))
        cx, cy, r = 5, 5, self._radius
        p.drawEllipse(int(cx-r), int(cy-r), int(r*2), int(r*2))


class GlowLabel(QLabel):
    def __init__(self, text="", glow_color: QColor = CLR_ACCENT, radius=14, parent=None):
        super().__init__(text, parent)
        fx = QGraphicsDropShadowEffect(self)
        fx.setBlurRadius(radius)
        fx.setOffset(0, 0)
        fx.setColor(glow_color)
        self.setGraphicsEffect(fx)


class SweepRadar(QWidget):
    """Rotating radar sweep — Iron Man HUD style."""

    def __init__(self, size=44, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0.0
        self._dots = [(0.3, 0.4), (0.6, 0.7), (0.8, 0.3)]
        t = QTimer(self); t.timeout.connect(self._tick); t.start(35)

    def _tick(self):
        self._angle = (self._angle + 3.5) % 360; self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width(); cx = cy = s / 2; r = s / 2 - 3

        # Rings
        for frac in [1.0, 0.67, 0.33]:
            pen = QPen(QColor(0, 180, 255, 30), 0.8)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            cr = r * frac
            p.drawEllipse(QRectF(cx-cr, cy-cr, cr*2, cr*2))

        # Cross-hairs
        p.setPen(QPen(QColor(0, 180, 255, 25), 0.6))
        p.drawLine(int(cx-r), int(cy), int(cx+r), int(cy))
        p.drawLine(int(cx), int(cy-r), int(cx), int(cy+r))

        # Sweep gradient
        a_rad = math.radians(-self._angle)
        grad = QConicalGradient(cx, cy, self._angle)
        grad.setColorAt(0.0,  QColor(0, 220, 255, 120))
        grad.setColorAt(0.15, QColor(0, 220, 255, 25))
        grad.setColorAt(0.16, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0,  QColor(0, 0, 0, 0))
        p.setBrush(QBrush(grad)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx-r, cy-r, r*2, r*2))

        # Sweep leading edge
        ex = cx + r * math.cos(a_rad)
        ey = cy - r * math.sin(a_rad)
        p.setPen(QPen(CLR_ACCENT, 1.2))
        p.drawLine(int(cx), int(cy), int(ex), int(ey))

        # Blips
        for (fx, fy) in self._dots:
            bx = cx + (fx - 0.5) * 2 * r
            by = cy + (fy - 0.5) * 2 * r
            blip_col = QColor(0, 255, 140, 200)
            p.setBrush(QBrush(blip_col)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(bx-2, by-2, 4, 4))

        # Center dot
        p.setBrush(QBrush(CLR_ACCENT)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx-2, cy-2, 4, 4))


class ArcReactor(QWidget):
    """Animated arc-reactor pulse ring."""

    def __init__(self, size=36, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._phase = 0.0
        self._active = True
        t = QTimer(self); t.timeout.connect(self._tick); t.start(40)

    def _tick(self):
        self._phase = (self._phase + 0.06) % (2 * math.pi); self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width(); cx = cy = s / 2; r = s / 2 - 3

        pulse = 0.5 + 0.5 * math.sin(self._phase)

        # Outer glow
        glow = QRadialGradient(cx, cy, r)
        glow.setColorAt(0,   QColor(0, 180, 255, int(60 * pulse)))
        glow.setColorAt(0.5, QColor(0, 100, 200, int(30 * pulse)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(glow)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx-r, cy-r, r*2, r*2))

        # Rotating ring segments
        for i in range(6):
            angle = self._phase + i * (2 * math.pi / 6)
            alpha = int(120 + 80 * math.sin(angle))
            pen = QPen(QColor(0, 220, 255, alpha), 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            start = int(math.degrees(angle) * 16)
            p.drawArc(QRectF(cx-r, cy-r, r*2, r*2), start, 45 * 16)

        # Inner hexagon
        hex_r = r * 0.42
        pts = []
        for i in range(6):
            a = self._phase * 0.4 + i * math.pi / 3
            pts.append(QPoint(int(cx + hex_r * math.cos(a)),
                              int(cy + hex_r * math.sin(a))))
        pen2 = QPen(QColor(100, 200, 255, int(140 + 60 * pulse)), 1)
        p.setPen(pen2); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(float(pt.x()), float(pt.y())) for pt in pts]))

        # Center core
        core_alpha = int(180 + 60 * pulse)
        core = QRadialGradient(cx, cy, hex_r * 0.6)
        core.setColorAt(0,   QColor(180, 240, 255, core_alpha))
        core.setColorAt(0.5, QColor(0,   160, 255, 100))
        core.setColorAt(1.0, QColor(0,    60, 120, 0))
        p.setBrush(QBrush(core)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx-hex_r*0.6, cy-hex_r*0.6, hex_r*1.2, hex_r*1.2))


class WaveformWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._mode = "idle"
        self._color = CLR_MUTED
        t = QTimer(self); t.timeout.connect(self._tick); t.start(40)
        self.setFixedSize(80, 18)

    def set_mode(self, mode: str):
        self._mode = mode
        self._color = {
            "listening":  CLR_SUCCESS,
            "processing": CLR_ACCENT,
        }.get(mode, CLR_MUTED)
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.38) % 1000; self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bar_count = 12; gap = 2
        bar_w = max(1, (w - (bar_count-1)*gap) // bar_count)
        mid = h / 2
        base_amp = 2 if self._mode == "idle" else 6
        pulse = 4 if self._mode == "processing" else 2
        glow = QColor(self._color); glow.setAlpha(50)

        for i in range(bar_count):
            x = i * (bar_w + gap)
            wave = math.sin(self._phase + i * 0.7)
            amp = base_amp + pulse * (0.5 + 0.5 * wave)
            if self._mode == "idle": amp *= 0.5
            y = mid - amp / 2
            p.setBrush(QBrush(glow)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(x, int(y)-1, bar_w, int(amp)+2, 1, 1)
            p.setBrush(QBrush(self._color))
            p.drawRoundedRect(x, int(y), bar_w, int(amp), 1, 1)


class ArcBar(QWidget):
    """Thin horizontal arc-style progress bar with glow."""

    def __init__(self, color: QColor = CLR_ACCENT, parent=None):
        super().__init__(parent)
        self._color = color
        self._pct = 0.0
        self.setFixedHeight(6)
        self.setMinimumWidth(60)

    def set_value(self, pct: float):
        self._pct = max(0.0, min(100.0, pct)); self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Track
        p.setBrush(QBrush(QColor(20, 35, 55, 160)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 1, w, h-2, 2, 2)

        # Fill
        fw = int(w * self._pct / 100)
        if fw > 0:
            grad = QLinearGradient(0, 0, fw, 0)
            c2 = QColor(self._color); c2.setAlpha(180)
            grad.setColorAt(0.0, c2)
            grad.setColorAt(1.0, self._color)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(0, 1, fw, h-2, 2, 2)

            # Glow tip
            tip = QRadialGradient(fw, h//2, 6)
            glow = QColor(self._color); glow.setAlpha(160)
            tip.setColorAt(0, glow); tip.setColorAt(1, QColor(0,0,0,0))
            p.setBrush(QBrush(tip)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(max(0, fw-6), 0, 12, h)


class StatPanel(QWidget):
    """Label + ArcBar + value — compact HUD stat row."""

    def __init__(self, label: str, color: QColor, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._lbl = QLabel(label)
        self._lbl.setFixedWidth(28)
        self._lbl.setStyleSheet(
            f"font-size:8px; letter-spacing:1px; color:#{color.red():02x}{color.green():02x}{color.blue():02x};"
        )

        self._bar = ArcBar(color)
        self._val = QLabel("--")
        self._val.setFixedWidth(46)
        self._val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._val.setStyleSheet("font-size:8px; letter-spacing:1px; color:#a0c8e8;")

        layout.addWidget(self._lbl)
        layout.addWidget(self._bar, 1)
        layout.addWidget(self._val)

    def set_value(self, pct: float, text: str):
        self._bar.set_value(pct)
        self._val.setText(text)


class HexGrid(QWidget):
    """Decorative animated hex-grid background element."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        t = QTimer(self); t.timeout.connect(self._tick); t.start(80)
        self.setFixedSize(50, 28)

    def _tick(self):
        self._phase = (self._phase + 0.05) % (2*math.pi); self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hex_r = 7; cols = 4; rows = 2
        for row in range(rows):
            for col in range(cols):
                cx = col * hex_r * 1.75 + (row % 2) * hex_r * 0.87 + hex_r
                cy = row * hex_r * 1.5 + hex_r
                phase_offset = (row + col) * 0.8
                alpha = int(18 + 14 * math.sin(self._phase + phase_offset))
                pen = QPen(QColor(0, 200, 255, alpha), 0.7)
                p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
                pts = []
                for i in range(6):
                    a = i * math.pi / 3
                    pts.append(QPoint(int(cx + hex_r*0.85*math.cos(a)),
                                      int(cy + hex_r*0.85*math.sin(a))))
                p.drawPolygon(QPolygonF([QPointF(float(pt.x()), float(pt.y())) for pt in pts]))


class CornerBracket(QWidget):
    """Animated L-bracket corner — glows on hover."""

    def __init__(self, corner="tl", size=16, parent=None):
        super().__init__(parent)
        self.corner = corner
        self.sz = size
        self.setFixedSize(size, size)
        self._alpha = 160
        self._anim = QPropertyAnimation(self, b"bracketAlpha", self)
        self._anim.setDuration(1800)
        self._anim.setStartValue(80)
        self._anim.setEndValue(220)
        self._anim.setEasingCurve(QEasingCurve.Type.SineCurve)
        self._anim.setLoopCount(-1)
        self._anim.start()

    def getBracketAlpha(self): return self._alpha
    def setBracketAlpha(self, v): self._alpha = v; self.update()
    bracketAlpha = pyqtProperty(int, getBracketAlpha, setBracketAlpha)

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(0, 220, 255, self._alpha), 1.5)
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


class NetworkBar(QWidget):
    """Animated network activity bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._bars = [0.1] * 16
        self._idx = 0
        self.setFixedSize(80, 14)
        t = QTimer(self); t.timeout.connect(self._tick); t.start(150)

    def _tick(self):
        import random
        self._phase += 0.2
        self._bars[self._idx % 16] = random.uniform(0.05, 0.85)
        self._idx += 1
        self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = 16; bw = w // n - 1
        for i in range(n):
            v = self._bars[(self._idx - n + i) % 16]
            bh = max(2, int(h * v))
            by = h - bh
            alpha = int(60 + 120 * v)
            color = QColor(0, 220, 255, alpha) if v < 0.7 else QColor(255, 180, 0, alpha)
            p.setBrush(QBrush(color)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(i * (bw+1), by, bw, bh)


# ─────────────────────────── Main HUD Overlay ─────────────────────────────────

class AriaOverlay(QWidget):
    """
    ARIA Iron Man HUD Overlay.

    Signals
    -------
    command_submitted(str)
    gesture_toggled(bool)
    mic_toggled(bool)
    model_selected(str)
    pause_requested()
    """

    command_submitted = pyqtSignal(str)
    gesture_toggled   = pyqtSignal(bool)
    mic_toggled       = pyqtSignal(bool)
    model_selected    = pyqtSignal(str)
    pause_requested   = pyqtSignal()

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
        self._pause_visible = False
        self._scan_phase    = 0.0

        self._init_window()
        self._build_ui()
        self._apply_styles()
        self._sync_model_button()

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_tick)
        self._blink_phase = True

        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(self._scan_tick)
        self._scan_timer.start(35)

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
        self.setMinimumWidth(260)
        self.resize(340, 320)
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.geometry()
            self.move(geo.width() - 360, 20)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(6)

        # ── Header ────────────────────────────────────────────────────────────
        header = QHBoxLayout(); header.setSpacing(6)

        self._arc = ArcReactor(32)
        self._arc.setToolTip("ARIA Core")

        name_col = QVBoxLayout(); name_col.setSpacing(0)
        self._aria_label = GlowLabel("◈ ARIA", CLR_ACCENT, radius=16)
        self._aria_label.setStyleSheet(
            "font-size:13px; font-weight:bold; letter-spacing:4px; color:#00dcff;"
        )
        self._status_label = QLabel("SYSTEM ONLINE")
        self._status_label.setStyleSheet(
            "font-size:7px; letter-spacing:3px; color:#00ff8c; margin-top:1px;"
        )
        name_col.addWidget(self._aria_label)
        name_col.addWidget(self._status_label)

        self._time_label = GlowLabel("--:--:--", CLR_ACCENT, radius=10)
        self._time_label.setStyleSheet(
            "font-size:14px; letter-spacing:2px; color:#c8e6ff; font-weight:bold;"
        )
        self._date_label = QLabel("-- --- ----")
        self._date_label.setStyleSheet("font-size:7px; letter-spacing:2px; color:#2a5070;")
        time_col = QVBoxLayout(); time_col.setSpacing(0)
        time_col.addWidget(self._time_label)
        time_col.addWidget(self._date_label)

        self._collapse_btn = QPushButton("▾")
        self._collapse_btn.setFixedSize(16, 16)
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(16, 16)
        self._close_btn.clicked.connect(self.hide)

        header.addWidget(self._arc)
        header.addLayout(name_col)
        header.addStretch()
        header.addLayout(time_col)
        header.addSpacing(4)
        header.addWidget(self._collapse_btn)
        header.addWidget(self._close_btn)
        root.addLayout(header)

        # Divider
        root.addWidget(self._make_divider())

        # ── Collapsible body ──────────────────────────────────────────────────
        self._body = QWidget()
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 2, 0, 0)
        bl.setSpacing(6)

        # ── Task row ──────────────────────────────────────────────────────────
        task_row = QHBoxLayout(); task_row.setSpacing(5)
        task_icon = QLabel("◎")
        task_icon.setStyleSheet("color:#8b3cf7; font-size:9px;")
        self._task_label = QLabel(self._task_name)
        self._task_label.setStyleSheet(
            "color:#c8e6ff; font-size:10px; letter-spacing:1px;"
        )
        self._task_label.setWordWrap(False)
        task_row.addWidget(task_icon)
        task_row.addWidget(self._task_label)
        task_row.addStretch()
        bl.addLayout(task_row)

        # ── Mic + Radar row ───────────────────────────────────────────────────
        mic_row = QHBoxLayout(); mic_row.setSpacing(8)
        self._mic_dot  = PulsingDot(CLR_MUTED)
        self._mic_wave = WaveformWidget()
        self._mic_wave.set_mode("idle")
        mic_btn = QPushButton("🎙️")
        mic_btn.setFixedSize(20, 16)
        mic_btn.setToolTip("Toggle mic")
        mic_btn.setStyleSheet("font-size:10px; color:#334155; border:none; background:transparent;")
        mic_btn.clicked.connect(self._toggle_mic)

        self._gesture_btn = QPushButton("GESTURE  OFF")
        self._gesture_btn.setCheckable(True)
        self._gesture_btn.setStyleSheet(self._gesture_style(False))
        self._gesture_btn.clicked.connect(self._toggle_gesture)
        self._gesture_btn.setFixedHeight(18)

        self._radar = SweepRadar(40)

        mic_row.addWidget(self._mic_dot)
        mic_row.addWidget(self._mic_wave)
        mic_row.addWidget(mic_btn)
        mic_row.addStretch()
        mic_row.addWidget(self._gesture_btn)
        mic_row.addWidget(self._radar)
        bl.addLayout(mic_row)

        bl.addWidget(self._make_divider(opacity=40))

        # ── System stats ──────────────────────────────────────────────────────
        stats_lbl = QLabel("SYS TELEMETRY")
        stats_lbl.setStyleSheet(
            "font-size:7px; letter-spacing:3px; color:#1e4060; margin-bottom:1px;"
        )
        bl.addWidget(stats_lbl)

        self._cpu_stat  = StatPanel("CPU", CLR_ACCENT)
        self._gpu_stat  = StatPanel("GPU", CLR_ACCENT2)
        self._ram_stat  = StatPanel("RAM", CLR_SUCCESS)
        self._ssd_stat  = StatPanel("SSD", QColor(180, 80, 255))
        self._disk_stat = StatPanel("DSK", QColor(148, 163, 184))

        for stat in [self._cpu_stat, self._gpu_stat, self._ram_stat,
                     self._ssd_stat, self._disk_stat]:
            bl.addWidget(stat)

        bl.addWidget(self._make_divider(opacity=40))

        # ── Network + Hex ──────────────────────────────────────────────────────
        net_row = QHBoxLayout(); net_row.setSpacing(8)
        net_lbl = QLabel("NET")
        net_lbl.setStyleSheet("font-size:7px; letter-spacing:2px; color:#1e4060;")
        self._net_bar = NetworkBar()
        self._hex_grid = HexGrid()
        net_row.addWidget(net_lbl)
        net_row.addWidget(self._net_bar)
        net_row.addStretch()
        net_row.addWidget(self._hex_grid)
        bl.addLayout(net_row)

        bl.addWidget(self._make_divider(opacity=40))

        # ── Chat input ────────────────────────────────────────────────────────
        chat_row = QHBoxLayout(); chat_row.setSpacing(5)

        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText("⌘  Quick command…")
        self._chat_input.setFixedHeight(26)
        self._chat_input.returnPressed.connect(self._on_submit)

        self._pause_btn = QPushButton("⏸")
        self._pause_btn.setFixedSize(26, 26)
        self._pause_btn.setToolTip("Pause")
        self._pause_btn.setStyleSheet(
            "background:rgba(239,68,68,20); border:1px solid rgba(239,68,68,100);"
            "border-radius:4px; color:#ef4444; font-size:11px;"
        )
        self._pause_btn.clicked.connect(self._on_pause)
        self._pause_btn.setVisible(False)

        self._model_btn = QPushButton("MODEL")
        self._model_btn.setFixedSize(48, 26)
        self._model_btn.setToolTip("Select Ollama model")
        self._model_btn.setStyleSheet(
            "background:rgba(0,60,100,80); border:1px solid rgba(0,150,200,80);"
            "border-radius:4px; color:#406080; font-size:8px; letter-spacing:1px;"
        )
        self._model_btn.clicked.connect(self._open_model_menu)

        send_btn = QPushButton("↵")
        send_btn.setFixedSize(26, 26)
        send_btn.setStyleSheet(
            "background:rgba(0,220,255,12); border:1px solid rgba(0,220,255,80);"
            "border-radius:4px; color:#00dcff; font-size:12px;"
        )
        send_btn.clicked.connect(self._on_submit)

        chat_row.addWidget(self._chat_input)
        chat_row.addWidget(self._pause_btn)
        chat_row.addWidget(self._model_btn)
        chat_row.addWidget(send_btn)
        bl.addLayout(chat_row)

        # AI response label
        self._response_label = QLabel("")
        self._response_label.setWordWrap(True)
        self._response_label.setStyleSheet(
            "font-size:9px; color:#6090a8; letter-spacing:0.5px; padding:2px 0;"
        )
        self._response_label.setVisible(False)
        bl.addWidget(self._response_label)

        root.addWidget(self._body)

        # Corner brackets
        self._corners = [
            CornerBracket("tl", 14, self),
            CornerBracket("tr", 14, self),
            CornerBracket("bl", 14, self),
            CornerBracket("br", 14, self),
        ]

    @staticmethod
    def _make_divider(opacity: int = 70) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 transparent, stop:0.25 rgba(0,220,255,{opacity}),"
            f"stop:0.75 rgba(0,220,255,{opacity}), stop:1 transparent);"
        )
        return line

    # ── Painting ──────────────────────────────────────────────────────────────

    def _scan_tick(self):
        self._scan_phase = (self._scan_phase + 1.2) % self.height()
        self.update()

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        w, h = self.width(), self.height()

        # Background panel with subtle gradient
        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0.0, QColor(6,  12, 26, 215))
        bg_grad.setColorAt(0.5, QColor(4,   8, 18, 210))
        bg_grad.setColorAt(1.0, QColor(8,  14, 28, 220))
        p.setBrush(QBrush(bg_grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 8, 8)

        # Scanlines
        p.setPen(QPen(CLR_SCANLINE, 1))
        y = 0
        while y < h:
            p.drawLine(0, y, w, y)
            y += 4

        # Moving scan bar
        scan_y = int(self._scan_phase)
        scan_grad = QLinearGradient(0, scan_y - 8, 0, scan_y + 8)
        scan_grad.setColorAt(0.0, QColor(0, 200, 255, 0))
        scan_grad.setColorAt(0.5, QColor(0, 200, 255, 18))
        scan_grad.setColorAt(1.0, QColor(0, 200, 255, 0))
        p.setBrush(QBrush(scan_grad)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(0, max(0, scan_y-8), w, 16)

        # Border
        border_grad = QLinearGradient(0, 0, w, h)
        border_grad.setColorAt(0.0, QColor(0,  220, 255, 90))
        border_grad.setColorAt(0.4, QColor(180,  60, 255, 50))
        border_grad.setColorAt(0.7, QColor(255, 140,   0, 40))
        border_grad.setColorAt(1.0, QColor(0,  220, 255, 60))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(border_grad), 1.2))
        p.drawRoundedRect(r, 8, 8)

        # Top accent bar
        top_grad = QLinearGradient(20, 0, w-20, 0)
        top_grad.setColorAt(0.0, QColor(0, 220, 255, 0))
        top_grad.setColorAt(0.3, QColor(0, 220, 255, 140))
        top_grad.setColorAt(0.7, QColor(180, 60, 255, 110))
        top_grad.setColorAt(1.0, QColor(0, 220, 255, 0))
        p.setBrush(QBrush(top_grad)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(16, 0, w-32, 2, 1, 1)

        # Bottom accent bar
        bot_grad = QLinearGradient(20, 0, w-20, 0)
        bot_grad.setColorAt(0.0, QColor(255, 140, 0, 0))
        bot_grad.setColorAt(0.4, QColor(255, 140, 0, 80))
        bot_grad.setColorAt(0.6, QColor(255, 140, 0, 80))
        bot_grad.setColorAt(1.0, QColor(255, 140, 0, 0))
        p.setBrush(QBrush(bot_grad))
        p.drawRoundedRect(16, h-2, w-32, 2, 1, 1)

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        w, h = self.width(), self.height()
        self._corners[0].move(4, 4)
        self._corners[1].move(w-18, 4)
        self._corners[2].move(4, h-18)
        self._corners[3].move(w-18, h-18)

    # ── Style helpers ─────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(STYLE_BASE)

    @staticmethod
    def _gesture_style(on: bool) -> str:
        if on:
            return (
                "QPushButton { background:rgba(0,220,255,18); border:1px solid rgba(0,220,255,160);"
                " border-radius:3px; color:#00dcff; font-size:8px; letter-spacing:2px; padding:0 5px; }"
                "QPushButton:hover { background:rgba(0,220,255,30); }"
            )
        return (
            "QPushButton { background:rgba(20,40,65,80); border:1px solid rgba(30,55,80,160);"
            " border-radius:3px; color:#304558; font-size:8px; letter-spacing:2px; padding:0 5px; }"
            "QPushButton:hover { border-color:rgba(0,220,255,80); color:#4a6070; }"
        )

    # ── Telemetry ─────────────────────────────────────────────────────────────

    def _refresh_stats(self):
        now = datetime.now()
        self._time_label.setText(now.strftime("%H:%M:%S"))
        self._date_label.setText(now.strftime("%a %d %b %Y").upper())

        cpu = psutil.cpu_percent(interval=None)
        self._cpu_stat.set_value(cpu, f"{cpu:.0f}%")

        ram = psutil.virtual_memory().percent
        self._ram_stat.set_value(ram, f"{ram:.0f}%")

        gpu = self._get_gpu_load()
        if gpu is None: gpu = 0.0
        self._gpu_stat.set_value(gpu, f"{gpu:.0f}%")

        ssd_speed = self._get_disk_speed_mb()
        ssd_pct = min(100.0, (ssd_speed / 800.0) * 100.0)
        self._ssd_stat.set_value(ssd_pct, f"{ssd_speed:.0f}M/s")

        try:
            usage = psutil.disk_usage("C:\\")
            free_pct = (usage.free / max(1, usage.total)) * 100.0
            self._disk_stat.set_value(free_pct, f"{free_pct:.0f}%fr")
        except Exception:
            self._disk_stat.set_value(0.0, "--")

    def _get_gpu_load(self) -> Optional[float]:
        # Try pynvml first (Nvidia)
        load = self._get_nvml_load()
        if load is not None:
            return load
        if GPUtil is not None:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    return max(0.0, min(100.0, gpus[0].load * 100.0))
            except Exception:
                pass
        return None

    def _init_nvml(self):
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
            if io is None:
                return 0.0
            now = time.monotonic()
            if self._last_disk_io is None or self._last_disk_ts is None:
                self._last_disk_io = io
                self._last_disk_ts = now
                return 0.0
            last_io = cast(object, self._last_disk_io)
            dt = max(1e-6, now - self._last_disk_ts)
            d_read  = io.read_bytes  - cast(int, getattr(last_io, "read_bytes", 0))
            d_write = io.write_bytes - cast(int, getattr(last_io, "write_bytes", 0))
            self._last_disk_io = io
            self._last_disk_ts = now
            return max(0.0, (d_read + d_write) / dt / (1024 * 1024))
        except Exception:
            return 0.0

    # ── Dragging ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, a0):
        if a0 is None: return
        if a0.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = a0.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, a0):
        if a0 is None: return
        if self._drag_pos is not None and a0.buttons() == Qt.MouseButton.LeftButton:
            self.move(a0.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, a0):
        self._drag_pos = None

    # ── Public API ────────────────────────────────────────────────────────────

    def set_task(self, name: str):
        self._task_name = name
        self._task_label.setText(name)

    def set_mic_state(self, state: str):
        self._mic_state = state
        if state == self.MIC_LISTENING:
            self._mic_dot.setColor(CLR_SUCCESS)
            self._mic_wave.set_mode("listening")
            self._status_label.setText("LISTENING…")
            self._status_label.setStyleSheet("font-size:7px; letter-spacing:3px; color:#00ff8c;")
            self._blink_timer.stop()
        elif state == self.MIC_PROCESSING:
            self._mic_dot.setColor(CLR_ACCENT)
            self._mic_wave.set_mode("processing")
            self._status_label.setText("PROCESSING…")
            self._status_label.setStyleSheet("font-size:7px; letter-spacing:3px; color:#00dcff;")
            self._blink_timer.start(450)
        else:
            self._mic_dot.setColor(CLR_MUTED)
            self._mic_wave.set_mode("idle")
            self._status_label.setText("SYSTEM ONLINE")
            self._status_label.setStyleSheet("font-size:7px; letter-spacing:3px; color:#00ff8c;")
            self._blink_timer.stop()

    def set_opacity(self, value: float):
        self.setWindowOpacity(max(0.1, min(1.0, value)))

    def set_voice_enabled(self, enabled: bool):
        self._mic_muted = not enabled
        if not enabled:
            self._mic_dot.setColor(CLR_DANGER)
            self._mic_wave.set_mode("idle")
            self._blink_timer.stop()
        else:
            self.set_mic_state(self._mic_state)

    def set_gesture_enabled(self, enabled: bool):
        self._gesture_on = bool(enabled)
        self._gesture_btn.blockSignals(True)
        try:
            self._gesture_btn.setChecked(self._gesture_on)
            self._gesture_btn.setText(f"GESTURE  {'ON' if self._gesture_on else 'OFF'}")
            self._gesture_btn.setStyleSheet(self._gesture_style(self._gesture_on))
        finally:
            self._gesture_btn.blockSignals(False)

    def set_response_text(self, text: str):
        """Display a short AI response snippet below the chat input."""
        if text:
            self._response_label.setText(f"▶ {text[:120]}")
            self._response_label.setVisible(True)
        else:
            self._response_label.setVisible(False)
        self.adjustSize()

    # ── Private slots ─────────────────────────────────────────────────────────

    def _on_submit(self):
        text = self._chat_input.text().strip()
        if not text:
            return
        # Show "thinking" state
        self._response_label.setText("◈ Thinking…")
        self._response_label.setVisible(True)
        self.set_mic_state(self.MIC_PROCESSING)
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
            self._mic_wave.set_mode("idle")

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        self._body.setVisible(not self._collapsed)
        self._collapse_btn.setText("▸" if self._collapsed else "▾")
        self.adjustSize()

    def _blink_tick(self):
        self._blink_phase = not self._blink_phase
        self._mic_dot.setColor(CLR_ACCENT if self._blink_phase else CLR_MUTED)

    def set_pause_visible(self, visible: bool):
        self._pause_visible = bool(visible)
        self._pause_btn.setVisible(self._pause_visible)

    def set_pause_busy(self, busy: bool):
        base = "border-radius:4px; color:#ef4444; font-size:11px;"
        if busy:
            self._pause_btn.setEnabled(False)
            self._pause_btn.setStyleSheet(
                f"background:rgba(239,68,68,35); border:1px solid rgba(239,68,68,200); {base}"
            )
        else:
            self._pause_btn.setEnabled(True)
            self._pause_btn.setStyleSheet(
                f"background:rgba(239,68,68,20); border:1px solid rgba(239,68,68,100); {base}"
            )

    def _on_pause(self):
        self.pause_requested.emit()

    # ── Model selection ───────────────────────────────────────────────────────

    def _open_model_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:rgba(6,12,26,240); color:#c8e6ff;"
            " border:1px solid rgba(0,220,255,60); font-size:10px; }"
            "QMenu::item { padding:4px 16px; }"
            "QMenu::item:selected { background:rgba(0,220,255,25); }"
        )
        models = self._get_ollama_models()
        if self._selected_model and self._selected_model not in models:
            models = [self._selected_model] + models
        if not models:
            act = menu.addAction("No Ollama models found")
            if act: act.setEnabled(False)
        else:
            for name in models:
                label = f"✓ {name}" if name == self._selected_model else name
                act = menu.addAction(label)
                if act:
                    act.triggered.connect(lambda _=False, n=name: self._set_model(n))
        menu.exec(self._model_btn.mapToGlobal(self._model_btn.rect().bottomLeft()))

    def _set_model(self, name: str):
        self._selected_model = name
        self._sync_model_button()
        self.model_selected.emit(name)

    def _sync_model_button(self):
        short = (self._selected_model or "MODEL").split(":")[0].upper()[:6]
        self._model_btn.setText(short)

    def _get_default_model(self) -> str:
        try:
            from config.settings import get as get_setting
            return str(get_setting("llm.model", ""))
        except Exception:
            return ""

    def _get_ollama_models(self) -> list[str]:
        # Try JSON format first
        try:
            result = subprocess.run(
                ["ollama", "list", "--json"],
                capture_output=True, text=True, check=False, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                payload = json.loads(result.stdout or "{}").get("models", [])
                models = [m.get("name", "") for m in payload if m.get("name")]
                if models:
                    return models
        except Exception:
            pass
        # Fallback: plain text
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, check=False, timeout=2,
            )
            if result.returncode != 0:
                return []
            lines = [l.strip() for l in (result.stdout or "").splitlines() if l.strip()]
            if len(lines) <= 1:
                return []
            return [line.split()[0] for line in lines[1:] if line.split()]
        except Exception:
            return []


 