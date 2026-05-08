"""
ui/tray_icon.py — ARIA System Tray Icon
Minimal-footprint tray presence: right-click menu for all core actions.
"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QWidgetAction, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QFrame
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QPoint, QSize
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QBrush, QPen,
    QLinearGradient, QFont, QAction, QRadialGradient
)


# ── Colour tokens ──────────────────────────────────────────────────────────────
CLR_ACCENT   = QColor(0,   229, 255)
CLR_ACCENT2  = QColor(139, 92,  246)
CLR_SUCCESS  = QColor(16,  185, 129)
CLR_DANGER   = QColor(239, 68,  68)
CLR_BG       = QColor(8,   11,  20)
CLR_SURFACE  = QColor(13,  17,  32)
CLR_BORDER   = QColor(30,  45,  69)
CLR_TEXT     = QColor(226, 232, 240)
CLR_MUTED    = QColor(100, 116, 139)


MENU_STYLE = """
QMenu {
    background-color: #0d1120;
    border: 1px solid rgba(0, 229, 255, 60);
    border-radius: 10px;
    padding: 6px 0px;
    font-family: 'Consolas', monospace;
    font-size: 11px;
    color: #e2e8f0;
}
QMenu::item {
    padding: 7px 18px 7px 36px;
    background: transparent;
    color: #e2e8f0;
    letter-spacing: 0.5px;
}
QMenu::item:selected {
    background: rgba(0, 229, 255, 12);
    color: #00e5ff;
    border-left: 2px solid #00e5ff;
}
QMenu::item:disabled {
    color: #334155;
}
QMenu::separator {
    height: 1px;
    background: rgba(0, 229, 255, 25);
    margin: 4px 12px;
}
QMenu::indicator {
    width: 14px;
    height: 14px;
    margin-left: 10px;
}
QMenu::indicator:checked {
    image: none;
    background: rgba(0,229,255,180);
    border-radius: 3px;
}
QMenu::indicator:unchecked {
    background: rgba(30,45,69,200);
    border: 1px solid rgba(0,229,255,60);
    border-radius: 3px;
}
"""


# ── Tray icon painter ─────────────────────────────────────────────────────────

def _make_tray_icon(state: str = "idle") -> QIcon:
    """
    Programmatically renders the ARIA tray icon (22×22 px).
    state: 'idle' | 'listening' | 'processing' | 'muted'
    """
    size = 64
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)

    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Outer ring colour by state
    ring_color = {
        "idle":       QColor(100, 116, 139),
        "listening":  CLR_SUCCESS,
        "processing": CLR_ACCENT,
        "muted":      CLR_DANGER,
    }.get(state, CLR_MUTED := QColor(100, 116, 139))

    # Glow halo
    rad = QRadialGradient(32, 32, 30)
    glow = QColor(ring_color)
    glow.setAlpha(40)
    rad.setColorAt(0.6, glow)
    rad.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setBrush(QBrush(rad))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 60, 60)

    # Dark background circle
    p.setBrush(QBrush(QColor(8, 11, 20, 230)))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(6, 6, 52, 52)

    # Outer ring
    pen = QPen(ring_color, 2.5)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(8, 8, 48, 48)

    # Inner "A" hexagon / diamond symbol
    center = 32
    # Diamond shape for the ARIA logo
    grad = QLinearGradient(20, 20, 44, 44)
    grad.setColorAt(0, CLR_ACCENT)
    grad.setColorAt(1, CLR_ACCENT2)
    p.setBrush(QBrush(grad))
    p.setPen(Qt.PenStyle.NoPen)

    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QPolygonF
    diamond = QPolygonF([
        QPointF(center,     center - 13),   # top
        QPointF(center + 9, center),        # right
        QPointF(center,     center + 13),   # bottom
        QPointF(center - 9, center),        # left
    ])
    p.drawPolygon(diamond)

    # Inner diamond outline
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(0, 0, 0, 80), 1))
    p.drawPolygon(diamond)

    # Horizontal crosshair in diamond
    p.setPen(QPen(QColor(8, 11, 20, 160), 1.5))
    p.drawLine(int(center - 5), center, int(center + 5), center)

    p.end()
    return QIcon(px)


# ── Status header widget (embedded in menu) ───────────────────────────────────

class TrayStatusHeader(QWidget):
    """Custom widget shown at top of the context menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(62)
        self.setMinimumWidth(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(2)

        # Title
        title = QLabel("◈  A R I A")
        title.setStyleSheet(
            "color: #00e5ff; font-size: 12px; font-weight: bold;"
            "letter-spacing: 4px; font-family: Consolas, monospace;"
            "background: transparent;"
        )

        self._status_label = QLabel("● ACTIVE — IDLE")
        self._status_label.setStyleSheet(
            "color: #10b981; font-size: 9px; letter-spacing: 2px;"
            "font-family: Consolas, monospace; background: transparent;"
        )

        layout.addWidget(title)
        layout.addWidget(self._status_label)

    def set_status(self, text: str, color: str = "#10b981"):
        self._status_label.setText(f"●  {text}")
        self._status_label.setStyleSheet(
            f"color: {color}; font-size: 9px; letter-spacing: 2px;"
            "font-family: Consolas, monospace; background: transparent;"
        )

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(13, 17, 32)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(self.rect())
        # Bottom separator
        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0, QColor(0, 229, 255, 0))
        grad.setColorAt(0.4, QColor(0, 229, 255, 60))
        grad.setColorAt(0.8, QColor(139, 92, 246, 40))
        grad.setColorAt(1, QColor(0, 229, 255, 0))
        p.setBrush(QBrush(grad))
        p.drawRect(0, self.height() - 1, self.width(), 1)


# ── Main tray icon ────────────────────────────────────────────────────────────

class AriaTrayIcon(QSystemTrayIcon):
    """
    ARIA system tray icon with full context menu.

    Signals
    -------
    voice_toggled(bool)       — voice enabled/disabled
    gesture_toggled(bool)     — gesture mode on/off
    open_scheduler()          — open scheduler view
    open_settings()           — open settings
    open_chat()               — open chat window
    show_overlay()            — show/hide overlay
    quit_requested()          — exit signal
    """

    voice_toggled    = pyqtSignal(bool)
    gesture_toggled  = pyqtSignal(bool)
    open_scheduler   = pyqtSignal()
    open_settings    = pyqtSignal()
    open_chat        = pyqtSignal()
    show_overlay     = pyqtSignal()
    quit_requested   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._voice_enabled   = True
        self._gesture_enabled = False
        self._status          = "ACTIVE — IDLE"
        self._status_color    = "#10b981"

        self._icon_idle       = _make_tray_icon("idle")
        self._icon_listening  = _make_tray_icon("listening")
        self._icon_processing = _make_tray_icon("processing")
        self._icon_muted      = _make_tray_icon("muted")

        self.setIcon(self._icon_idle)
        self.setToolTip("ARIA — Active")

        self._build_menu()

        # Pulse animation timer (icon swap)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_phase  = False

        self.activated.connect(self._on_activated)

    # ── Menu construction ─────────────────────────────────────────────────────

    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet(MENU_STYLE)

        # ── Status header ──────────────────────────────────────────────────────
        self._header = TrayStatusHeader()
        header_action = QWidgetAction(menu)
        header_action.setDefaultWidget(self._header)
        menu.addAction(header_action)
        menu.addSeparator()

        # ── Toggle: Voice ──────────────────────────────────────────────────────
        self._voice_action = QAction("⬤  Voice Recognition", menu)
        self._voice_action.setCheckable(True)
        self._voice_action.setChecked(self._voice_enabled)
        self._voice_action.triggered.connect(self._on_voice_toggle)
        menu.addAction(self._voice_action)

        # ── Toggle: Gesture ────────────────────────────────────────────────────
        self._gesture_action = QAction("◈  Gesture Mode", menu)
        self._gesture_action.setCheckable(True)
        self._gesture_action.setChecked(self._gesture_enabled)
        self._gesture_action.triggered.connect(self._on_gesture_toggle)
        menu.addAction(self._gesture_action)

        # ── Toggle: Overlay ────────────────────────────────────────────────────
        overlay_action = QAction("▣  Toggle Overlay", menu)
        overlay_action.triggered.connect(self.show_overlay.emit)
        menu.addAction(overlay_action)

        menu.addSeparator()

        # ── Open chat ──────────────────────────────────────────────────────────
        chat_action = QAction("◫  Open Chat Panel", menu)
        chat_action.triggered.connect(self.open_chat.emit)
        menu.addAction(chat_action)

        # ── Open scheduler ─────────────────────────────────────────────────────
        sched_action = QAction("◷  Open Scheduler", menu)
        sched_action.triggered.connect(self.open_scheduler.emit)
        menu.addAction(sched_action)

        # ── Open settings ──────────────────────────────────────────────────────
        settings_action = QAction("◬  Settings", menu)
        settings_action.triggered.connect(self.open_settings.emit)
        menu.addAction(settings_action)

        menu.addSeparator()

        # ── Quit ───────────────────────────────────────────────────────────────
        quit_action = QAction("⏻  Quit ARIA", menu)
        quit_action.setProperty("class", "danger")
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_state(self, state: str):
        """
        state: 'idle' | 'listening' | 'processing' | 'muted'
        Updates icon and status text accordingly.
        """
        labels = {
            "idle":       ("ACTIVE — IDLE",       "#10b981", self._icon_idle),
            "listening":  ("LISTENING…",           "#00e5ff", self._icon_listening),
            "processing": ("PROCESSING…",          "#8b5cf6", self._icon_processing),
            "muted":      ("VOICE MUTED",          "#ef4444", self._icon_muted),
        }
        text, color, icon = labels.get(state, ("UNKNOWN", "#64748b", self._icon_idle))
        self._status = text
        self._status_color = color
        self._header.set_status(text, color)
        self.setToolTip(f"ARIA — {text}")

        if state == "processing":
            self._pulse_timer.start(400)
        else:
            self._pulse_timer.stop()
            self.setIcon(icon)

    def show_notification(self, title: str, message: str,
                          duration_ms: int = 3000):
        """Show a system tray balloon notification."""
        self.showMessage(
            title, message,
            QSystemTrayIcon.MessageIcon.Information,
            duration_ms
        )

    # ── Private slots ─────────────────────────────────────────────────────────

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_chat.emit()

    def _on_voice_toggle(self, checked: bool):
        self._voice_enabled = checked
        label = "⬤  Voice Recognition"
        self._voice_action.setText(label)
        self.voice_toggled.emit(checked)
        state = "idle" if checked else "muted"
        self.set_state(state)

    def _on_gesture_toggle(self, checked: bool):
        self._gesture_enabled = checked
        self.gesture_toggled.emit(checked)

    def _pulse_tick(self):
        self._pulse_phase = not self._pulse_phase
        self.setIcon(self._icon_processing if self._pulse_phase else self._icon_idle)


# ── Standalone demo ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("System tray not available.")
        sys.exit(1)

    tray = AriaTrayIcon()
    tray.show()
    tray.show_notification("ARIA", "Assistant is online and ready.")

    # Demo: cycle through states
    states = ["idle", "listening", "processing", "muted"]
    _idx = [0]
    def _cycle():
        tray.set_state(states[_idx[0] % len(states)])
        _idx[0] += 1
    timer = QTimer()
    timer.timeout.connect(_cycle)
    timer.start(2000)

    sys.exit(app.exec())