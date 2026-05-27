"""
ui/tray_icon.py — ARIA System Tray Icon  ·  v2
═══════════════════════════════════════════════════════════════════════════════
Cyberpunk-HUD aesthetic — layered rings with compass ticks, crisp A-glyph
with cyan→violet gradient, state-driven scan-sweep animation (processing),
breathing pulse (listening), and a rich status-header with network / device
count badges and HUD corner brackets.
"""

from __future__ import annotations
import sys, math
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QWidgetAction, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QPointF, QRectF
from PyQt6.QtGui  import (
    QIcon, QPixmap, QPainter, QColor, QBrush, QPen,
    QLinearGradient, QFont, QAction, QRadialGradient,
    QConicalGradient, QPainterPath,
)


# ══════════════════════════════════════════════════════════════════════════════
#  PALETTE
# ══════════════════════════════════════════════════════════════════════════════

def _c(r: int, g: int, b: int, a: int = 255) -> QColor:
    return QColor(r, g, b, a)

CYAN    = _c(  0, 229, 255)
VIOLET  = _c(139,  92, 246)
GREEN   = _c( 16, 185, 129)
RED     = _c(239,  68,  68)
BG      = _c(  6,   9,  18)
SURF    = _c( 10,  14,  28)
BORDER  = _c( 25,  40,  65)
TEXT    = _c(220, 230, 242)
MUTED   = _c( 80, 100, 130)

# state → (ring_QColor, header_label, hex_color_str)
_STATE: dict[str, tuple[QColor, str, str]] = {
    "idle":       (GREEN,  "ACTIVE  ·  IDLE",  "#10b981"),
    "listening":  (CYAN,   "LISTENING …",       "#00e5ff"),
    "processing": (VIOLET, "PROCESSING …",      "#8b5cf6"),
    "muted":      (RED,    "VOICE MUTED",       "#ef4444"),
}

_PROC_N = 12   # processing animation frame count


# ══════════════════════════════════════════════════════════════════════════════
#  MENU STYLESHEET
# ══════════════════════════════════════════════════════════════════════════════

MENU_STYLE = """
QMenu {
    background-color: #060912;
    border: 1px solid rgba(0, 229, 255, 45);
    border-radius: 12px;
    padding: 4px 0;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    color: #dce6f2;
}
QMenu::item {
    padding: 7px 18px 7px 38px;
    background: transparent;
    color: #dce6f2;
    letter-spacing: 0.4px;
}
QMenu::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(0,229,255,20),
        stop:1 rgba(139,92,246,10));
    color: #00e5ff;
    border-left: 2px solid #00e5ff;
    padding-left: 36px;
}
QMenu::item:disabled {
    color: rgba(0, 229, 255, 60);
    font-size: 9px;
    letter-spacing: 3px;
    padding: 5px 18px 2px 14px;
}
QMenu::separator {
    height: 1px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0.0 rgba(0,229,255,0),
        stop:0.3 rgba(0,229,255,45),
        stop:0.7 rgba(139,92,246,35),
        stop:1.0 rgba(0,229,255,0));
    margin: 3px 10px;
}
QMenu::indicator { width: 13px; height: 13px; margin-left: 12px; }
QMenu::indicator:checked {
    background: rgba(0, 229, 255, 200);
    border-radius: 3px;
}
QMenu::indicator:unchecked {
    background: rgba(25, 40, 65, 200);
    border: 1px solid rgba(0, 229, 255, 70);
    border-radius: 3px;
}
"""


# ══════════════════════════════════════════════════════════════════════════════
#  ICON RENDERER
# ══════════════════════════════════════════════════════════════════════════════

def _draw_icon(state: str = "idle", sweep_deg: float = 0.0) -> QIcon:
    """
    Render a 64×64 ARIA tray icon.
    sweep_deg — conical sweep angle; only matters when state='processing'.

    Layers (back → front):
      1. Outer glow halos
      2. Background disc (radial gradient)
      3. Outer ring — 4 arcs with N/E/S/W gaps
      4. Compass tick marks
      5. Scan sweep (processing only)
      6. Inner ring (subtle)
      7. "A" glyph — violet→cyan gradient stroke
      8. Apex jewel glow
      9. Base node dots
    """
    S  = 64
    cx = cy = S // 2   # 32
    R  = 26            # outer ring radius from centre
    Ri = 18            # inner ring radius

    ring_col = _STATE.get(state, (MUTED, "", ""))[0]

    px = QPixmap(S, S)
    px.fill(Qt.GlobalColor.transparent)
    p  = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # ── 1. Outer glow halos ────────────────────────────────────────────────
    for r, alpha in ((31, 32), (25, 16)):
        h = QRadialGradient(cx, cy, r)
        gc = QColor(ring_col); gc.setAlpha(alpha)
        h.setColorAt(0.5, gc)
        h.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(h)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - r, cy - r, 2*r, 2*r)

    # ── 2. Background disc ─────────────────────────────────────────────────
    bg = QRadialGradient(cx - 5, cy - 5, 28)
    bg.setColorAt(0.0, QColor(15, 22, 44))
    bg.setColorAt(1.0, QColor( 4,  6, 14))
    p.setBrush(QBrush(bg)); p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(5, 5, 54, 54)

    # ── 3. Outer ring — 4 arcs, gap at each compass point ─────────────────
    arc_c = QColor(ring_col); arc_c.setAlpha(210)
    p.setPen(QPen(arc_c, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    rect_o = QRectF(cx - R, cy - R, 2*R, 2*R)
    gap = 14   # degrees to skip around each compass point
    for base in (0, 90, 180, 270):
        p.drawArc(rect_o, int((base + gap // 2) * 16), int((90 - gap) * 16))

    # ── 4. Compass tick marks (N / E / S / W) ─────────────────────────────
    tick_c = QColor(ring_col); tick_c.setAlpha(235)
    p.setPen(QPen(tick_c, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for deg in (0, 90, 180, 270):
        a = math.radians(deg - 90)
        p.drawLine(
            QPointF(cx + math.cos(a) * (R - 3), cy + math.sin(a) * (R - 3)),
            QPointF(cx + math.cos(a) * (R + 3), cy + math.sin(a) * (R + 3)),
        )

    # ── 5. Processing scan sweep ───────────────────────────────────────────
    if state == "processing":
        swp = QConicalGradient(cx, cy, sweep_deg)
        s0 = QColor(ring_col); s0.setAlpha(115)
        s1 = QColor(ring_col); s1.setAlpha( 20)
        swp.setColorAt(0.00, s0)
        swp.setColorAt(0.13, s1)
        swp.setColorAt(0.14, QColor(0, 0, 0, 0))
        swp.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(swp)); p.setPen(Qt.PenStyle.NoPen)
        r2 = R - 3
        p.drawEllipse(QRectF(cx - r2, cy - r2, 2*r2, 2*r2))

    # ── 6. Inner ring ──────────────────────────────────────────────────────
    in_c = QColor(ring_col); in_c.setAlpha(42)
    p.setPen(QPen(in_c, 0.8)); p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(cx - Ri, cy - Ri, 2*Ri, 2*Ri))

    # ── 7. "A" glyph — violet→cyan gradient ───────────────────────────────
    apex = QPointF(cx,      cy - 12)
    bl   = QPointF(cx -  9, cy +  10)
    br   = QPointF(cx +  9, cy +  10)
    cl   = QPointF(cx -  5, cy +   1)
    cr   = QPointF(cx +  5, cy +   1)

    path = QPainterPath()
    path.moveTo(bl);  path.lineTo(apex); path.lineTo(br)
    path.moveTo(cl);  path.lineTo(cr)

    grd = QLinearGradient(bl.x(), bl.y(), apex.x(), apex.y())
    grd.setColorAt(0.0, VIOLET)
    grd.setColorAt(1.0, CYAN)
    p.setPen(QPen(QBrush(grd), 2.4, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)

    # ── 8. Apex jewel glow ─────────────────────────────────────────────────
    for r, a in ((5.5, 70), (3.5, 150), (2.0, 240)):
        jr = QRadialGradient(cx, apex.y(), r)
        jr.setColorAt(0.0, QColor(210, 245, 255, a))
        jr.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(jr)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - r, apex.y() - r, 2*r, 2*r))

    # ── 9. Base node dots (leg endpoints) ─────────────────────────────────
    for nx, ny in ((bl.x(), bl.y()), (br.x(), br.y())):
        nr = QRadialGradient(nx, ny, 3.5)
        nc = QColor(VIOLET); nc.setAlpha(200)
        nr.setColorAt(0.0, nc)
        nr.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(nr)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(nx - 2.5, ny - 2.5, 5.0, 5.0))

    p.end()
    return QIcon(px)


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _render_logo_mark(size: int = 34) -> QPixmap:
    """Small ARIA hexagonal logo-mark for the menu header."""
    S = size; cx = cy = S // 2
    px = QPixmap(S, S)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    r = S // 2 - 1
    pts = [
        QPointF(cx + r * math.cos(math.radians(30 + 60*i)),
                cy + r * math.sin(math.radians(30 + 60*i)))
        for i in range(6)
    ]
    hex_path = QPainterPath()
    hex_path.moveTo(pts[0])
    for pt in pts[1:]: hex_path.lineTo(pt)
    hex_path.closeSubpath()

    hx_bg = QRadialGradient(cx - 2, cy - 2, r)
    hx_bg.setColorAt(0.0, QColor(14, 22, 44))
    hx_bg.setColorAt(1.0, QColor( 5,  7, 16))
    p.setBrush(QBrush(hx_bg))
    p.setPen(QPen(QColor(0, 229, 255, 80), 1.2))
    p.drawPath(hex_path)

    sc = S / 34
    apex = QPointF(cx,            cy -  8 * sc)
    bl_  = QPointF(cx -  6 * sc, cy +  7 * sc)
    br_  = QPointF(cx +  6 * sc, cy +  7 * sc)
    cl_  = QPointF(cx -  3 * sc, cy +  0.5*sc)
    cr_  = QPointF(cx +  3 * sc, cy +  0.5*sc)

    ap = QPainterPath()
    ap.moveTo(bl_); ap.lineTo(apex); ap.lineTo(br_)
    ap.moveTo(cl_); ap.lineTo(cr_)

    g = QLinearGradient(bl_.x(), bl_.y(), apex.x(), apex.y())
    g.setColorAt(0.0, VIOLET)
    g.setColorAt(1.0, CYAN)
    p.setPen(QPen(QBrush(g), 1.6 * sc, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(ap)
    p.end()
    return px


def _badge_style(color: str) -> str:
    return (
        f"color: {color};"
        f"border: 1px solid {color}55;"
        "border-radius: 3px;"
        "padding: 1px 6px;"
        "font-family: 'Consolas', monospace;"
        "font-size: 8px;"
        "letter-spacing: 1px;"
        f"background: {color}15;"
    )


def _section_action(menu: QMenu, label: str) -> QAction:
    a = QAction(f"  {label}", menu)
    a.setEnabled(False)
    return a


# ══════════════════════════════════════════════════════════════════════════════
#  STATUS HEADER WIDGET
# ══════════════════════════════════════════════════════════════════════════════

class TrayStatusHeader(QWidget):
    """
    Custom widget pinned at the top of the context menu.

    Layout
    ──────
    ┌─ hex logo ─┬─ A R I A  ─────────────────────────────────┐
    │            │  NEURAL ASSISTANT                           │
    ├────────────┴──── ●  ACTIVE · IDLE  ── [NET ●] [3 DEV] ──┤
    └─────────────── gradient separator ────────────────────────┘
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(92)
        self.setMinimumWidth(260)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(4)

        # ── Row 1: Logo mark + ARIA name ────────────────────────────────────
        r1 = QHBoxLayout(); r1.setSpacing(10)

        self._logo_lbl = QLabel()
        self._logo_lbl.setFixedSize(34, 34)
        self._logo_lbl.setPixmap(_render_logo_mark(34))

        name_col = QVBoxLayout(); name_col.setSpacing(0)
        title = QLabel("A R I A")
        title.setStyleSheet(
            "color: #00e5ff;"
            "font-family: 'Consolas', monospace;"
            "font-size: 15px; font-weight: bold;"
            "letter-spacing: 5px; background: transparent;"
        )
        sub = QLabel("NEURAL ASSISTANT")
        sub.setStyleSheet(
            "color: rgba(139,92,246,170);"
            "font-family: 'Consolas', monospace;"
            "font-size: 7px; letter-spacing: 2.5px; background: transparent;"
        )
        name_col.addWidget(title)
        name_col.addWidget(sub)

        r1.addWidget(self._logo_lbl)
        r1.addLayout(name_col)
        r1.addStretch()
        root.addLayout(r1)

        # ── Row 2: Status dot + network / device badges ─────────────────────
        r2 = QHBoxLayout(); r2.setSpacing(6)

        self._status_lbl = QLabel("●  ACTIVE  ·  IDLE")
        self._status_lbl.setStyleSheet(
            "color: #10b981; font-family: 'Consolas', monospace;"
            "font-size: 9px; letter-spacing: 1.5px; background: transparent;"
        )

        self._net_badge = QLabel("NET ●")
        self._net_badge.setStyleSheet(_badge_style("#10b981"))

        self._dev_badge = QLabel("0 DEV")
        self._dev_badge.setStyleSheet(_badge_style("#00e5ff"))

        r2.addWidget(self._status_lbl)
        r2.addStretch()
        r2.addWidget(self._net_badge)
        r2.addWidget(self._dev_badge)
        root.addLayout(r2)

    # ── Public setters ────────────────────────────────────────────────────────

    def set_status(self, text: str, color: str = "#10b981") -> None:
        self._status_lbl.setText(f"●  {text}")
        self._status_lbl.setStyleSheet(
            f"color: {color}; font-family: 'Consolas', monospace;"
            "font-size: 9px; letter-spacing: 1.5px; background: transparent;"
        )

    def set_network(self, online: bool) -> None:
        if online:
            self._net_badge.setText("NET ●")
            self._net_badge.setStyleSheet(_badge_style("#10b981"))
        else:
            self._net_badge.setText("NET ○")
            self._net_badge.setStyleSheet(_badge_style("#ef4444"))

    def set_devices(self, count: int) -> None:
        self._dev_badge.setText(f"{count} DEV")

    # ── Custom paint ──────────────────────────────────────────────────────────

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Dark background
        p.setBrush(QBrush(QColor(7, 10, 22)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(self.rect())

        # Subtle circuit-grid overlay
        p.setPen(QPen(QColor(0, 229, 255, 7), 1))
        for x in range(0, W, 20):
            p.drawLine(x, 0, x, H)
        for y in range(0, H, 20):
            p.drawLine(0, y, W, y)

        # HUD corner brackets — top-left
        bc = QColor(0, 229, 255, 65)
        p.setPen(QPen(bc, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        p.drawLine(4,  4, 16,  4)
        p.drawLine(4,  4,  4, 16)
        # top-right
        p.drawLine(W - 4,  4, W - 16,  4)
        p.drawLine(W - 4,  4, W -  4, 16)

        # Bottom separator — cyan→violet gradient line
        sep = QLinearGradient(0, H - 1, W, H - 1)
        sep.setColorAt(0.0, QColor(  0, 229, 255,  0))
        sep.setColorAt(0.3, QColor(  0, 229, 255, 55))
        sep.setColorAt(0.7, QColor(139,  92, 246, 40))
        sep.setColorAt(1.0, QColor(  0, 229, 255,  0))
        p.setBrush(QBrush(sep)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(0, H - 1, W, 1)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN TRAY CLASS
# ══════════════════════════════════════════════════════════════════════════════

class AriaTrayIcon(QSystemTrayIcon):
    """
    ARIA system tray icon with animated state machine and rich context menu.

    Signals
    ───────
    voice_toggled(bool)     voice recognition enabled / disabled
    gesture_toggled(bool)   gesture mode on / off
    open_scheduler()        open scheduler view
    open_settings()         open settings window
    open_chat()             open chat panel
    show_overlay()          toggle HUD overlay
    quit_requested()        request application exit

    New public methods (v2)
    ────────────────────────
    set_network_status(online: bool)   update the NET badge
    set_device_count(count: int)       update the DEV badge
    """

    voice_toggled   = pyqtSignal(bool)
    gesture_toggled = pyqtSignal(bool)
    open_scheduler  = pyqtSignal()
    open_settings   = pyqtSignal()
    open_chat       = pyqtSignal()
    show_overlay    = pyqtSignal()
    quit_requested  = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._voice_enabled   = True
        self._gesture_enabled = False
        self._state           = "idle"
        self._proc_frame      = 0
        self._listen_phase    = False

        # Pre-render icon banks
        self._icon_idle  = _draw_icon("idle")
        self._icon_muted = _draw_icon("muted")
        self._icon_proc  = [
            _draw_icon("processing", i * 360.0 / _PROC_N)
            for i in range(_PROC_N)
        ]
        # Listening: alternate between listening-bright and idle-dim for pulse
        self._icon_listen = [_draw_icon("listening"), _draw_icon("idle")]

        self.setIcon(self._icon_idle)
        self.setToolTip("ARIA  ·  Active")

        self._build_menu()

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._anim_tick)
        self.activated.connect(self._on_activated)

    # ── Menu construction ──────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        menu = QMenu()
        menu.setStyleSheet(MENU_STYLE)
        menu.setMinimumWidth(260)

        # Status header
        self._header = TrayStatusHeader()
        ha = QWidgetAction(menu)
        ha.setDefaultWidget(self._header)
        menu.addAction(ha)

        # ── INPUT ─────────────────────────────────────────────────────────────
        menu.addAction(_section_action(menu, "INPUT"))

        self._voice_act = QAction("⬤  Voice Recognition", menu)
        self._voice_act.setCheckable(True)
        self._voice_act.setChecked(self._voice_enabled)
        self._voice_act.triggered.connect(self._on_voice_toggle)
        menu.addAction(self._voice_act)

        self._gest_act = QAction("◈  Gesture Mode", menu)
        self._gest_act.setCheckable(True)
        self._gest_act.setChecked(self._gesture_enabled)
        self._gest_act.triggered.connect(self._on_gesture_toggle)
        menu.addAction(self._gest_act)

        menu.addSeparator()

        # ── INTERFACE ─────────────────────────────────────────────────────────
        menu.addAction(_section_action(menu, "INTERFACE"))

        ov = QAction("▣  Toggle Overlay", menu)
        ov.triggered.connect(self.show_overlay.emit)
        menu.addAction(ov)

        ch = QAction("◫  Open Chat Panel", menu)
        ch.triggered.connect(self.open_chat.emit)
        menu.addAction(ch)

        menu.addSeparator()

        # ── TOOLS ─────────────────────────────────────────────────────────────
        menu.addAction(_section_action(menu, "TOOLS"))

        sc = QAction("◷  Scheduler", menu)
        sc.triggered.connect(self.open_scheduler.emit)
        menu.addAction(sc)

        st = QAction("◬  Settings", menu)
        st.triggered.connect(self.open_settings.emit)
        menu.addAction(st)

        menu.addSeparator()

        # ── QUIT ──────────────────────────────────────────────────────────────
        qt = QAction("⏻  Quit ARIA", menu)
        qt.triggered.connect(self.quit_requested.emit)
        menu.addAction(qt)

        self.setContextMenu(menu)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_state(self, state: str) -> None:
        """
        Update icon, tooltip, and header status.
        state: 'idle' | 'listening' | 'processing' | 'muted'
        """
        _, label, color = _STATE.get(state, (MUTED, "UNKNOWN", "#64748b"))
        self._state = state
        self._header.set_status(label, color)
        self.setToolTip(f"ARIA  ·  {label}")
        self._proc_frame   = 0
        self._listen_phase = False

        if state == "processing":
            self._anim.start(80)       # ~12 fps smooth sweep
        elif state == "listening":
            self._anim.start(550)      # slow breathing pulse
        else:
            self._anim.stop()
            self.setIcon(self._icon_idle if state == "idle" else self._icon_muted)

    def set_network_status(self, online: bool) -> None:
        """Update the NET badge in the status header."""
        self._header.set_network(online)

    def set_device_count(self, count: int) -> None:
        """Update the connected-devices badge in the status header."""
        self._header.set_devices(count)

    def set_voice_enabled(self, enabled: bool) -> None:
        """Sync voice toggle state without emitting voice_toggled."""
        self._voice_enabled = bool(enabled)
        try:
            self._voice_act.blockSignals(True)
            self._voice_act.setChecked(self._voice_enabled)
        finally:
            self._voice_act.blockSignals(False)
        self.set_state("idle" if self._voice_enabled else "muted")

    def set_gesture_enabled(self, enabled: bool) -> None:
        """Sync gesture toggle state without emitting gesture_toggled."""
        self._gesture_enabled = bool(enabled)
        try:
            self._gest_act.blockSignals(True)
            self._gest_act.setChecked(self._gesture_enabled)
        finally:
            self._gest_act.blockSignals(False)

    def show_notification(self, title: str, message: str,
                          duration_ms: int = 3500) -> None:
        """Show a system balloon notification."""
        self.showMessage(title, message,
                         QSystemTrayIcon.MessageIcon.Information,
                         duration_ms)

    # ── Private slots ─────────────────────────────────────────────────────────

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_chat.emit()

    def _on_voice_toggle(self, checked: bool) -> None:
        self._voice_enabled = checked
        self.voice_toggled.emit(checked)
        self.set_state("idle" if checked else "muted")

    def _on_gesture_toggle(self, checked: bool) -> None:
        self._gesture_enabled = checked
        self.gesture_toggled.emit(checked)

    def _anim_tick(self) -> None:
        if self._state == "processing":
            self._proc_frame = (self._proc_frame + 1) % _PROC_N
            self.setIcon(self._icon_proc[self._proc_frame])
        elif self._state == "listening":
            self._listen_phase = not self._listen_phase
            self.setIcon(self._icon_listen[int(self._listen_phase)])

