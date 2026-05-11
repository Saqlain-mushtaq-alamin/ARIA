"""
ui/chat_window.py — ARIA Floating Chat Panel
Conversation history, active task list, and proactive suggestions.
Fully resizable, draggable, hide-when-not-needed.
"""

import sys
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QLineEdit, QFrame, QTextEdit, QSizeGrip,
    QGraphicsDropShadowEffect, QSpacerItem, QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect,
    pyqtSignal, QSize, QPoint, pyqtProperty
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QLinearGradient, QRadialGradient,
    QFont, QTextCursor, QIcon, QPixmap, QPainterPath
)


# ── Theme ──────────────────────────────────────────────────────────────────────
T = {
    "bg":        "#080b14",
    "surface":   "#0d1120",
    "surface2":  "#111827",
    "border":    "rgba(0,229,255,50)",
    "accent":    "#00e5ff",
    "accent2":   "#8b5cf6",
    "success":   "#10b981",
    "warning":   "#f59e0b",
    "danger":    "#ef4444",
    "text":      "#e2e8f0",
    "text_muted":"#64748b",
    "text_dim":  "#334155",
    "user_bg":   "rgba(0,229,255,12)",
    "aria_bg":   "rgba(139,92,246,10)",
}

GLOBAL_STYLE = f"""
* {{
    font-family: 'Segoe UI', 'Consolas', sans-serif;
    color: {T['text']};
}}
QWidget#ChatWindow {{
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: {T['surface']};
    width: 5px;
    border-radius: 3px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(0,229,255,40);
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(0,229,255,80);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QLineEdit {{
    background: rgba(13,17,32,200);
    border: 1px solid rgba(0,229,255,60);
    border-radius: 8px;
    color: {T['text']};
    padding: 9px 14px;
    font-size: 12px;
    selection-background-color: rgba(0,229,255,50);
}}
QLineEdit:focus {{
    border: 1px solid rgba(0,229,255,180);
    background: rgba(0,229,255,6);
}}
QPushButton {{
    background: transparent;
    border: none;
    color: {T['text_muted']};
}}
QPushButton:hover {{ color: {T['accent']}; }}
"""


# ── Message bubble ─────────────────────────────────────────────────────────────

class MessageBubble(QFrame):
    """A single chat message rendered as a styled card."""

    def __init__(self, role: str, text: str, timestamp: str = "", parent=None):
        super().__init__(parent)
        self.role = role   # 'user' | 'aria' | 'system'
        self.setObjectName("bubble")
        self._build(text, timestamp)

    def _build(self, text: str, ts: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # Header: role + timestamp
        header = QHBoxLayout()
        role_label = QLabel(self._role_tag())
        role_label.setStyleSheet(f"font-size:9px; letter-spacing:2px; color:{self._role_color()}; font-family:Consolas;")

        ts_label = QLabel(ts)
        ts_label.setStyleSheet(f"font-size:9px; color:{T['text_dim']}; font-family:Consolas;")

        header.addWidget(role_label)
        header.addStretch()
        header.addWidget(ts_label)
        layout.addLayout(header)

        # Body text
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(f"font-size:12px; color:{T['text']}; line-height:1.5;")
        layout.addWidget(body)

    def _role_tag(self):
        return {"user": "YOU", "aria": "◈ ARIA", "system": "SYSTEM"}.get(self.role, self.role.upper())

    def _role_color(self):
        return {"user": T['accent'], "aria": T['accent2'], "system": T['text_muted']}.get(self.role, T['text_muted'])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg_color = {
            "user":   QColor(0, 229, 255, 14),
            "aria":   QColor(139, 92, 246, 12),
            "system": QColor(30, 45, 69, 60),
        }.get(self.role, QColor(30, 45, 69, 60))

        border_color = {
            "user":   QColor(0, 229, 255, 50),
            "aria":   QColor(139, 92, 246, 50),
            "system": QColor(30, 45, 69, 120),
        }.get(self.role, QColor(30, 45, 69, 120))

        r = self.rect().adjusted(0, 0, -1, -1)
        p.setBrush(QBrush(bg_color))
        p.setPen(QPen(border_color, 1))
        p.drawRoundedRect(r, 8, 8)

        # Left edge accent
        accent = QColor(0, 229, 255, 100) if self.role == "user" else QColor(139, 92, 246, 80)
        p.setBrush(QBrush(accent))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 8, 2, self.height() - 16, 1, 1)


# ── Task chip ─────────────────────────────────────────────────────────────────

class TaskChip(QFrame):
    """Compact task item shown in the side panel."""

    completed = pyqtSignal(str)

    def __init__(self, task_id: str, text: str, priority: str = "normal", done: bool = False, parent=None):
        super().__init__(parent)
        self.task_id  = task_id
        self._done    = done
        self._priority = priority
        self.setFixedHeight(34)
        self._build(text)

    def _build(self, text: str):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(8)

        # Priority pip
        self._pip = QLabel("◆")
        pip_color = {"high": "#ef4444", "normal": "#00e5ff", "low": "#64748b"}.get(self._priority, "#64748b")
        self._pip.setStyleSheet(f"font-size:7px; color:{pip_color};")

        # Text
        self._text_label = QLabel(text)
        self._text_label.setStyleSheet(
            f"font-size:11px; color:{'#334155' if self._done else T['text']};"
            + ("text-decoration:line-through;" if self._done else "")
        )

        # Check button
        self._check = QPushButton("✓")
        self._check.setFixedSize(20, 20)
        check_style = (
            "background: rgba(16,185,129,20); border:1px solid #10b981; border-radius:4px; color:#10b981; font-size:10px;"
            if self._done else
            "background: rgba(30,45,69,80); border:1px solid rgba(30,45,69,180); border-radius:4px; color:#334155; font-size:10px;"
        )
        self._check.setStyleSheet(check_style)
        self._check.clicked.connect(lambda: self.completed.emit(self.task_id))

        lay.addWidget(self._pip)
        lay.addWidget(self._text_label, 1)
        lay.addWidget(self._check)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(13, 17, 32, 120)))
        p.setPen(QPen(QColor(30, 45, 69, 100), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 1, -1, -2), 6, 6)


# ── Suggestion card ───────────────────────────────────────────────────────────

class SuggestionCard(QPushButton):
    """Proactive AI suggestion chip."""

    def __init__(self, text: str, icon: str = "◈", parent=None):
        super().__init__(parent)
        self.setText(f"  {icon}  {text}")
        self.setStyleSheet(f"""
            QPushButton {{
                background: rgba(139,92,246,10);
                border: 1px solid rgba(139,92,246,60);
                border-radius: 8px;
                color: #c4b5fd;
                font-size: 11px;
                text-align: left;
                padding: 8px 12px;
                font-family: 'Segoe UI', sans-serif;
            }}
            QPushButton:hover {{
                background: rgba(139,92,246,20);
                border-color: rgba(139,92,246,120);
                color: #e2e8f0;
            }}
        """)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


# ── Section title ─────────────────────────────────────────────────────────────

def _section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"font-size:9px; letter-spacing:3px; color:{T['text_muted']};"
        "font-family:Consolas; padding: 4px 0 2px 0;"
    )
    return lbl


# ── Main chat window ──────────────────────────────────────────────────────────

class AriaChatWindow(QWidget):
    """
    Floating, resizable chat panel.

    Signals
    -------
    message_sent(str)           — user sent a message
    task_completed(str)         — task_id marked complete
    suggestion_clicked(str)     — a suggestion card was clicked
    """

    message_sent      = pyqtSignal(str)
    task_completed    = pyqtSignal(str)
    suggestion_clicked = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("ChatWindow")
        self._drag_pos = None
        self._task_chips: dict[str, TaskChip] = {}
        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.timeout.connect(self._hide_typing)

        self._init_window()
        self._build_ui()
        self.setStyleSheet(GLOBAL_STYLE)

        # Opening system message
        self.add_message("aria", "Hello! I'm ARIA. How can I assist you today?")

    # ── Window init ───────────────────────────────────────────────────────────

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(640, 560)
        screen = QApplication.primaryScreen().geometry()
        self.move(
            screen.width() // 2 - 320,
            screen.height() // 2 - 280
        )

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top title bar ──────────────────────────────────────────────────────
        title_bar = self._make_title_bar()
        root.addWidget(title_bar)

        # ── Content area (chat + sidebar) ─────────────────────────────────────
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        # Chat column
        chat_col = QVBoxLayout()
        chat_col.setContentsMargins(14, 8, 8, 12)
        chat_col.setSpacing(8)

        self._messages_scroll = QScrollArea()
        self._messages_scroll.setWidgetResizable(True)
        self._messages_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._messages_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._messages_container = QWidget()
        self._messages_layout = QVBoxLayout(self._messages_container)
        self._messages_layout.setContentsMargins(0, 4, 0, 4)
        self._messages_layout.setSpacing(8)
        self._messages_layout.addStretch()
        self._messages_scroll.setWidget(self._messages_container)

        # Typing indicator
        self._typing_label = QLabel("◈ ARIA is thinking…")
        self._typing_label.setStyleSheet(
            f"color:{T['accent2']}; font-size:10px; font-style:italic; padding:4px 0;"
            "font-family:Consolas;"
        )
        self._typing_label.hide()

        # Input row
        input_row = self._make_input_row()

        chat_col.addWidget(self._messages_scroll, 1)
        chat_col.addWidget(self._typing_label)
        chat_col.addLayout(input_row)

        # Sidebar
        self._sidebar = self._make_sidebar()

        content.addLayout(chat_col, 3)
        content.addWidget(self._make_vsep())
        content.addWidget(self._sidebar, 1)

        root.addLayout(content, 1)

        # Bottom resize grip
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip = QSizeGrip(self)
        grip.setStyleSheet("background:transparent;")
        grip_row.addWidget(grip)
        root.addLayout(grip_row)

    def _make_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(8)

        icon_lbl = QLabel("◈")
        icon_lbl.setStyleSheet("color:#00e5ff; font-size:14px;")
        fx = QGraphicsDropShadowEffect()
        fx.setBlurRadius(14); fx.setOffset(0, 0); fx.setColor(QColor(0, 229, 255))
        icon_lbl.setGraphicsEffect(fx)

        title = QLabel("ARIA  CHAT")
        title.setStyleSheet(
            "color:#e2e8f0; font-size:12px; font-weight:600; letter-spacing:4px; font-family:Consolas;"
        )

        self._session_label = QLabel(f"SESSION {datetime.now().strftime('%H%M')}")
        self._session_label.setStyleSheet(
            "color:#334155; font-size:9px; letter-spacing:2px; font-family:Consolas;"
        )

        min_btn  = self._icon_btn("▁", "Minimise")
        hide_btn = self._icon_btn("▣", "Hide panel")
        hide_btn.clicked.connect(self.hide)
        close_btn = self._icon_btn("✕", "Close")
        close_btn.clicked.connect(self.close)

        lay.addWidget(icon_lbl)
        lay.addWidget(title)
        lay.addWidget(self._session_label)
        lay.addStretch()
        lay.addWidget(min_btn)
        lay.addWidget(hide_btn)
        lay.addWidget(close_btn)
        return bar

    def _make_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a message or command…")
        self._input.setFixedHeight(38)
        self._input.returnPressed.connect(self._on_send)

        send_btn = QPushButton("↵ SEND")
        send_btn.setFixedSize(80, 38)
        send_btn.setStyleSheet(
            "QPushButton {"
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 rgba(0,229,255,30), stop:1 rgba(139,92,246,30));"
            "border: 1px solid rgba(0,229,255,100);"
            "border-radius: 8px; color:#00e5ff;"
            "font-size:10px; letter-spacing:1px; font-family:Consolas;"
            "}"
            "QPushButton:hover { background: rgba(0,229,255,50); }"
        )
        send_btn.clicked.connect(self._on_send)

        row.addWidget(self._input, 1)
        row.addWidget(send_btn)
        return row

    def _make_sidebar(self) -> QWidget:
        side = QWidget()
        side.setMinimumWidth(170)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(8, 10, 14, 10)
        lay.setSpacing(10)

        # ── Tasks ──────────────────────────────────────────────────────────────
        lay.addWidget(_section_title("ACTIVE TASKS"))

        self._tasks_container = QWidget()
        self._tasks_layout    = QVBoxLayout(self._tasks_container)
        self._tasks_layout.setContentsMargins(0, 0, 0, 0)
        self._tasks_layout.setSpacing(4)
        lay.addWidget(self._tasks_container)

        # ── Suggestions ────────────────────────────────────────────────────────
        lay.addSpacing(6)
        lay.addWidget(_section_title("SUGGESTIONS"))

        self._suggestions_layout = QVBoxLayout()
        self._suggestions_layout.setSpacing(5)
        lay.addLayout(self._suggestions_layout)

        lay.addStretch()
        return side

    def _make_vsep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: rgba(0,229,255,20);")
        return sep

    @staticmethod
    def _icon_btn(text: str, tip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(24, 24)
        btn.setToolTip(tip)
        btn.setStyleSheet(
            "QPushButton { color:#475569; font-size:11px; border:none; background:transparent; }"
            "QPushButton:hover { color:#00e5ff; }"
        )
        return btn

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = self.rect().adjusted(1, 1, -1, -1)

        # Main body
        p.setBrush(QBrush(QColor(8, 11, 20, 235)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 12, 12)

        # Border
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor(0, 229, 255, 80))
        grad.setColorAt(0.5, QColor(139, 92, 246, 50))
        grad.setColorAt(1.0, QColor(0, 229, 255, 30))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(grad), 1.2))
        p.drawRoundedRect(r, 12, 12)

        # Title bar accent
        bar_grad = QLinearGradient(0, 0, self.width(), 0)
        bar_grad.setColorAt(0, QColor(0, 229, 255, 60))
        bar_grad.setColorAt(0.5, QColor(139, 92, 246, 50))
        bar_grad.setColorAt(1, QColor(0, 229, 255, 20))
        p.setBrush(QBrush(bar_grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(12, 43, self.width() - 24, 1, 0, 0)

    # ── Dragging ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 44:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _):
        self._drag_pos = None

    # ── Public API ────────────────────────────────────────────────────────────

    def add_message(self, role: str, text: str):
        """
        Append a message bubble.
        role: 'user' | 'aria' | 'system'
        """
        # If a response arrives, hide any "thinking" indicator.
        if role == "aria":
            try:
                self._typing_timer.stop()
            except Exception:
                pass
            self._typing_label.hide()

        ts = datetime.now().strftime("%H:%M")
        bubble = MessageBubble(role, text, ts)

        # Telegram/WhatsApp style: user on right, ARIA on left.
        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(0)

        # Keep bubbles readable (don't span full width).
        bubble.setMaximumWidth(460)
        try:
            bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        except Exception:
            pass

        if role == "user":
            row_lay.addStretch(1)
            row_lay.addWidget(bubble, 0)
        else:
            row_lay.addWidget(bubble, 0)
            row_lay.addStretch(1)

        # Insert before the trailing stretch
        idx = self._messages_layout.count() - 1
        self._messages_layout.insertWidget(idx, row)
        # Auto-scroll to bottom
        QTimer.singleShot(50, lambda: self._messages_scroll.verticalScrollBar().setValue(
            self._messages_scroll.verticalScrollBar().maximum()
        ))

    def show_typing(self, duration_ms: int = 3000):
        """Show 'ARIA is thinking…' indicator.

        If duration_ms <= 0, keep it visible until a response arrives.
        """
        self._typing_label.show()
        if duration_ms and duration_ms > 0:
            self._typing_timer.start(duration_ms)
        else:
            self._typing_timer.stop()

    def _hide_typing(self):
        self._typing_label.hide()

    def add_task(self, task_id: str, text: str, priority: str = "normal", done: bool = False):
        """Add or update a task chip in the sidebar."""
        chip = TaskChip(task_id, text, priority, done)
        chip.completed.connect(self._on_task_complete)
        self._task_chips[task_id] = chip
        self._tasks_layout.addWidget(chip)

    def clear_tasks(self):
        """Remove all task chips from the sidebar."""
        for chip in list(self._task_chips.values()):
            try:
                self._tasks_layout.removeWidget(chip)
            except Exception:
                pass
            chip.deleteLater()
        self._task_chips.clear()

    def set_tasks(self, tasks: list[tuple[str, str, str, bool]]):
        """Replace sidebar task chips.

        tasks: list of (task_id, text, priority, done)
        """
        self.clear_tasks()
        for task_id, text, priority, done in tasks:
            self.add_task(task_id, text, priority=priority, done=done)

    def add_suggestion(self, text: str, icon: str = "◈"):
        """Add a proactive suggestion card."""
        card = SuggestionCard(text, icon)
        card.clicked.connect(lambda: self.suggestion_clicked.emit(text))
        self._suggestions_layout.addWidget(card)

    def clear_suggestions(self):
        while self._suggestions_layout.count():
            item = self._suggestions_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ── Private slots ─────────────────────────────────────────────────────────

    def _on_send(self):
        text = self._input.text().strip()
        if text:
            self.add_message("user", text)
            self.message_sent.emit(text)
            self._input.clear()
            # Keep thinking indicator on until runtime adds ARIA reply.
            self.show_typing(0)

    def _on_task_complete(self, task_id: str):
        self.task_completed.emit(task_id)


# ── Demo ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = AriaChatWindow()
    w.add_task("t1", "Draft project proposal",     priority="high")
    w.add_task("t2", "Review meeting notes",       priority="normal")
    w.add_task("t3", "Send status update email",   priority="normal", done=True)
    w.add_suggestion("Schedule tomorrow's standup", "◷")
    w.add_suggestion("Summarise unread emails",     "◫")
    w.add_suggestion("Set focus mode for 1 hour",   "◎")
    w.show()
    sys.exit(app.exec())