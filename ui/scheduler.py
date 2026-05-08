"""
ui/scheduler_view.py — ARIA Visual Daily Scheduler
Timeline view: drag to reschedule, mark complete, add tasks inline.
Unified dark sci-fi / HUD aesthetic matching overlay, tray & chat.
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QLineEdit, QFrame, QSizeGrip,
    QGraphicsDropShadowEffect, QComboBox, QDialog, QDialogButtonBox,
    QTimeEdit, QSizePolicy, QMenu, QAbstractScrollArea
)
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect, QRectF,
    pyqtSignal, QSize, QPoint, QPointF, QTime, QMimeData,
    pyqtProperty, QEvent
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QLinearGradient, QRadialGradient,
    QFont, QIcon, QPixmap, QPainterPath, QCursor, QDrag,
    QFontMetrics, QMouseEvent
)


# ── Theme tokens ───────────────────────────────────────────────────────────────
CLR_BG        = QColor(8,   11,  20)
CLR_SURFACE   = QColor(13,  17,  32)
CLR_SURFACE2  = QColor(17,  22,  40)
CLR_BORDER    = QColor(30,  45,  69)
CLR_ACCENT    = QColor(0,   229, 255)   # electric cyan
CLR_ACCENT2   = QColor(139, 92,  246)   # violet
CLR_SUCCESS   = QColor(16,  185, 129)   # emerald
CLR_WARNING   = QColor(245, 158, 11)    # amber
CLR_DANGER    = QColor(239, 68,  68)    # red
CLR_TEXT      = QColor(226, 232, 240)
CLR_MUTED     = QColor(100, 116, 139)
CLR_DIM       = QColor(51,  65,  85)

PRIORITY_COLORS = {
    "critical": CLR_DANGER,
    "high":     CLR_WARNING,
    "normal":   CLR_ACCENT,
    "low":      CLR_MUTED,
}

CATEGORY_COLORS = {
    "work":     QColor(0,   229, 255),
    "meeting":  QColor(139, 92,  246),
    "personal": QColor(16,  185, 129),
    "break":    QColor(245, 158, 11),
    "focus":    QColor(239, 68,  68),
}

GLOBAL_STYLE = """
* {
    font-family: 'Consolas', 'Courier New', monospace;
    color: #e2e8f0;
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    background: #0d1120; width: 5px; border-radius: 3px;
}
QScrollBar::handle:vertical {
    background: rgba(0,229,255,40); border-radius: 3px; min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #0d1120; height: 5px; border-radius: 3px;
}
QScrollBar::handle:horizontal {
    background: rgba(0,229,255,40); border-radius: 3px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QLineEdit {
    background: rgba(13,17,32,200);
    border: 1px solid rgba(0,229,255,60);
    border-radius: 6px;
    color: #e2e8f0;
    padding: 6px 10px;
    font-size: 11px;
    selection-background-color: rgba(0,229,255,50);
}
QLineEdit:focus { border: 1px solid rgba(0,229,255,180); background: rgba(0,229,255,5); }
QComboBox {
    background: rgba(13,17,32,200);
    border: 1px solid rgba(0,229,255,60);
    border-radius: 6px;
    color: #e2e8f0;
    padding: 5px 10px;
    font-size: 11px;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { color: #00e5ff; }
QComboBox QAbstractItemView {
    background: #0d1120;
    border: 1px solid rgba(0,229,255,60);
    color: #e2e8f0;
    selection-background-color: rgba(0,229,255,15);
}
QPushButton {
    background: transparent;
    border: none;
    color: #64748b;
    font-size: 11px;
}
QPushButton:hover { color: #00e5ff; }
QTimeEdit {
    background: rgba(13,17,32,200);
    border: 1px solid rgba(0,229,255,60);
    border-radius: 6px;
    color: #e2e8f0;
    padding: 5px 8px;
    font-size: 11px;
}
QDialog {
    background: #0d1120;
}
"""


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ScheduledTask:
    id:        str
    title:     str
    start:     QTime          # start time
    duration:  int            # minutes
    category:  str = "work"
    priority:  str = "normal"
    done:      bool = False
    notes:     str = ""

    @property
    def end(self) -> QTime:
        total = self.start.hour() * 60 + self.start.minute() + self.duration
        return QTime(total // 60 % 24, total % 60)

    def end_minutes(self) -> int:
        return self.start_minutes() + self.duration

    def start_minutes(self) -> int:
        return self.start.hour() * 60 + self.start.minute()


# ── Add/Edit task dialog ───────────────────────────────────────────────────────

class TaskDialog(QDialog):
    """Modal dialog for creating or editing a scheduled task."""

    def __init__(self, task: Optional[ScheduledTask] = None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(340)
        self._task = task
        self._build()
        self.setStyleSheet(GLOBAL_STYLE)
        if task:
            self._populate(task)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        # Title bar
        title = QLabel("◈  ADD TASK" if not self._task else "◈  EDIT TASK")
        title.setStyleSheet(
            "color:#00e5ff; font-size:12px; letter-spacing:3px; font-weight:bold;"
        )
        root.addWidget(title)

        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(0,229,255,40);")
        root.addWidget(sep)

        # Task name
        root.addWidget(self._lbl("TASK NAME"))
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Review sprint backlog…")
        root.addWidget(self._name)

        # Time row
        time_row = QHBoxLayout(); time_row.setSpacing(10)
        start_col = QVBoxLayout()
        start_col.addWidget(self._lbl("START"))
        self._start = QTimeEdit()
        self._start.setDisplayFormat("HH:mm")
        self._start.setTime(QTime(9, 0))
        start_col.addWidget(self._start)

        dur_col = QVBoxLayout()
        dur_col.addWidget(self._lbl("DURATION (MIN)"))
        self._dur = QLineEdit("30")
        dur_col.addWidget(self._dur)

        time_row.addLayout(start_col)
        time_row.addLayout(dur_col)
        root.addLayout(time_row)

        # Category + Priority row
        cat_row = QHBoxLayout(); cat_row.setSpacing(10)

        cat_col = QVBoxLayout()
        cat_col.addWidget(self._lbl("CATEGORY"))
        self._cat = QComboBox()
        self._cat.addItems(["work", "meeting", "personal", "break", "focus"])
        cat_col.addWidget(self._cat)

        pri_col = QVBoxLayout()
        pri_col.addWidget(self._lbl("PRIORITY"))
        self._pri = QComboBox()
        self._pri.addItems(["low", "normal", "high", "critical"])
        self._pri.setCurrentText("normal")
        pri_col.addWidget(self._pri)

        cat_row.addLayout(cat_col)
        cat_row.addLayout(pri_col)
        root.addLayout(cat_row)

        # Notes
        root.addWidget(self._lbl("NOTES (optional)"))
        self._notes = QLineEdit()
        self._notes.setPlaceholderText("Brief note…")
        root.addWidget(self._notes)

        # Buttons
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        cancel = QPushButton("CANCEL")
        cancel.setStyleSheet(
            "QPushButton { border:1px solid rgba(30,45,69,180); border-radius:6px;"
            " color:#64748b; padding:7px 18px; }"
            "QPushButton:hover { border-color:rgba(0,229,255,80); color:#00e5ff; }"
        )
        cancel.clicked.connect(self.reject)

        confirm = QPushButton("CONFIRM  ↵")
        confirm.setStyleSheet(
            "QPushButton { background: rgba(0,229,255,15); border:1px solid rgba(0,229,255,120);"
            " border-radius:6px; color:#00e5ff; padding:7px 18px; letter-spacing:1px; }"
            "QPushButton:hover { background: rgba(0,229,255,28); }"
        )
        confirm.clicked.connect(self.accept)

        btn_row.addStretch()
        btn_row.addWidget(cancel)
        btn_row.addWidget(confirm)
        root.addLayout(btn_row)

    @staticmethod
    def _lbl(text: str) -> QLabel:
        l = QLabel(text)
        l.setStyleSheet("font-size:9px; letter-spacing:2px; color:#475569;")
        return l

    def _populate(self, t: ScheduledTask):
        self._name.setText(t.title)
        self._start.setTime(t.start)
        self._dur.setText(str(t.duration))
        self._cat.setCurrentText(t.category)
        self._pri.setCurrentText(t.priority)
        self._notes.setText(t.notes)

    def get_task_data(self) -> dict:
        return {
            "title":    self._name.text().strip() or "Untitled",
            "start":    self._start.time(),
            "duration": max(5, int(self._dur.text() or "30")),
            "category": self._cat.currentText(),
            "priority": self._pri.currentText(),
            "notes":    self._notes.text().strip(),
        }

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setBrush(QBrush(QColor(13, 17, 32, 245)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 12, 12)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor(0, 229, 255, 70))
        g.setColorAt(1, QColor(139, 92, 246, 40))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(g), 1.2))
        p.drawRoundedRect(r, 12, 12)


# ── Timeline canvas ────────────────────────────────────────────────────────────

class TimelineCanvas(QWidget):
    """
    The main scrollable HUD timeline.
    Renders hour grid, time-of-day indicator, and task blocks.
    Supports drag-to-move tasks.
    """

    task_moved     = pyqtSignal(str, QTime)   # id, new_start
    task_clicked   = pyqtSignal(str)           # id
    task_completed = pyqtSignal(str)           # id
    slot_clicked   = pyqtSignal(QTime)         # empty slot → add task here

    # Layout constants
    HOUR_HEIGHT  = 72       # px per hour
    LEFT_MARGIN  = 54       # time label gutter
    RIGHT_PAD    = 16
    MIN_HEIGHT   = HOUR_HEIGHT * 24   # full day

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: list[ScheduledTask] = []
        self._drag_task:   Optional[ScheduledTask] = None
        self._drag_offset: int = 0          # click offset in minutes
        self._drag_time:   Optional[QTime] = None
        self._hover_task:  Optional[str]   = None

        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMinimumWidth(400)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed
        )
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

        # Live clock tick
        self._clock = QTimer(self)
        self._clock.timeout.connect(self.update)
        self._clock.start(30_000)   # refresh every 30 s

    # ── Data ──────────────────────────────────────────────────────────────────

    def set_tasks(self, tasks: list[ScheduledTask]):
        self._tasks = tasks
        self.update()

    def add_task(self, task: ScheduledTask):
        self._tasks.append(task)
        self.update()

    def remove_task(self, task_id: str):
        self._tasks = [t for t in self._tasks if t.id != task_id]
        self.update()

    def update_task(self, task_id: str, **kwargs):
        for t in self._tasks:
            if t.id == task_id:
                for k, v in kwargs.items():
                    setattr(t, k, v)
        self.update()

    # ── Geometry helpers ──────────────────────────────────────────────────────

    def _y_for_minutes(self, minutes: int) -> int:
        return int(minutes * self.HOUR_HEIGHT / 60)

    def _minutes_for_y(self, y: int) -> int:
        raw = int(y * 60 / self.HOUR_HEIGHT)
        return max(0, min(raw - (raw % 5), 23 * 60 + 55))   # snap to 5-min

    def _task_rect(self, task: ScheduledTask) -> QRect:
        x      = self.LEFT_MARGIN + 8
        y      = self._y_for_minutes(task.start_minutes())
        width  = self.width() - x - self.RIGHT_PAD
        height = max(self._y_for_minutes(task.duration), 26)
        return QRect(x, y, width, height)

    def _task_at(self, point: QPoint) -> Optional[ScheduledTask]:
        for task in reversed(self._tasks):
            if self._task_rect(task).contains(point):
                return task
        return None

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        self._draw_background(p)
        self._draw_grid(p)
        self._draw_tasks(p)
        self._draw_now_line(p)
        if self._drag_task and self._drag_time:
            self._draw_drag_ghost(p)

    def _draw_background(self, p: QPainter):
        p.fillRect(self.rect(), CLR_SURFACE)

        # Subtle column tint for work hours (9-18)
        work_y1 = self._y_for_minutes(9 * 60)
        work_y2 = self._y_for_minutes(18 * 60)
        tint = QColor(0, 229, 255, 5)
        p.fillRect(self.LEFT_MARGIN, work_y1,
                   self.width() - self.LEFT_MARGIN, work_y2 - work_y1, tint)

    def _draw_grid(self, p: QPainter):
        font_hour = QFont("Consolas", 8)
        font_half = QFont("Consolas", 7)
        p.setFont(font_hour)

        for hour in range(25):
            y = self._y_for_minutes(hour * 60)
            if y > self.height():
                break

            # Major hour line
            pen = QPen(QColor(30, 45, 69, 180), 1)
            p.setPen(pen)
            p.drawLine(self.LEFT_MARGIN, y, self.width() - self.RIGHT_PAD, y)

            # Hour label
            if hour < 24:
                label = f"{hour:02d}:00"
                p.setPen(QPen(CLR_DIM))
                p.setFont(font_hour)
                p.drawText(4, y + 12, label)

            # Half-hour dotted line
            if hour < 24:
                yh = self._y_for_minutes(hour * 60 + 30)
                pen_half = QPen(QColor(30, 45, 69, 80), 1, Qt.PenStyle.DotLine)
                p.setPen(pen_half)
                p.drawLine(self.LEFT_MARGIN, yh,
                           self.width() - self.RIGHT_PAD, yh)
                p.setPen(QPen(QColor(30, 45, 69, 100)))
                p.setFont(font_half)
                p.drawText(10, yh + 10, "·30")

        # Left gutter separator
        g = QLinearGradient(self.LEFT_MARGIN, 0, self.LEFT_MARGIN + 1, 0)
        g.setColorAt(0, QColor(0, 229, 255, 50))
        g.setColorAt(1, QColor(0, 229, 255, 0))
        p.fillRect(self.LEFT_MARGIN - 1, 0, 2, self.height(), QBrush(g))

    def _draw_tasks(self, p: QPainter):
        for task in self._tasks:
            if task is self._drag_task:
                continue   # drawn separately as ghost
            rect = self._task_rect(task)
            self._paint_task_block(p, task, rect, alpha_mul=1.0)

    def _paint_task_block(self, p: QPainter, task: ScheduledTask,
                          rect: QRect, alpha_mul: float = 1.0):
        is_hover   = (self._hover_task == task.id)
        cat_color  = CATEGORY_COLORS.get(task.category, CLR_ACCENT)
        pri_color  = PRIORITY_COLORS.get(task.priority, CLR_ACCENT)

        a = int(255 * alpha_mul)

        # Background
        bg = QColor(cat_color)
        bg.setAlpha(int(22 * alpha_mul))
        p.setBrush(QBrush(bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        # Hover highlight
        if is_hover and not task.done:
            hi = QColor(cat_color); hi.setAlpha(14)
            p.setBrush(QBrush(hi))
            p.drawRoundedRect(rect, 6, 6)

        # Done overlay
        if task.done:
            done_fill = QColor(16, 185, 129, 12)
            p.setBrush(QBrush(done_fill))
            p.drawRoundedRect(rect, 6, 6)

        # Border
        border = QColor(cat_color); border.setAlpha(int(90 * alpha_mul))
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        # Left accent stripe
        stripe = QColor(pri_color); stripe.setAlpha(int(200 * alpha_mul))
        p.setBrush(QBrush(stripe))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect.x(), rect.y() + 4, 3, rect.height() - 8, 2, 2)

        # Task title
        title_color = QColor(CLR_MUTED if task.done else CLR_TEXT)
        title_color.setAlpha(a)
        p.setPen(QPen(title_color))
        font = QFont("Consolas", 10)
        if task.done:
            font.setStrikeOut(True)
        p.setFont(font)

        text_rect = rect.adjusted(10, 6, -32, 0)
        fm = QFontMetrics(font)
        title_text = fm.elidedText(task.title, Qt.TextElideMode.ElideRight, text_rect.width())
        p.drawText(text_rect, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, title_text)

        # Time label
        if rect.height() > 30:
            time_str = f"{task.start.toString('HH:mm')} – {task.end.toString('HH:mm')}"
            time_color = QColor(cat_color); time_color.setAlpha(int(160 * alpha_mul))
            p.setPen(QPen(time_color))
            p.setFont(QFont("Consolas", 8))
            p.drawText(rect.adjusted(10, 20, -32, -4),
                       Qt.AlignmentFlag.AlignTop, time_str)

        # Category badge
        badge_rect = QRect(rect.right() - 52, rect.y() + 5, 46, 14)
        badge_bg = QColor(cat_color); badge_bg.setAlpha(int(40 * alpha_mul))
        p.setBrush(QBrush(badge_bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(badge_rect, 3, 3)
        badge_color = QColor(cat_color); badge_color.setAlpha(int(200 * alpha_mul))
        p.setPen(QPen(badge_color))
        p.setFont(QFont("Consolas", 7))
        p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, task.category.upper())

        # Done check mark
        if task.done:
            p.setPen(QPen(CLR_SUCCESS, 1.5))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(rect.adjusted(0, 0, -8, 0),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "✓")

    def _draw_now_line(self, p: QPainter):
        now     = QTime.currentTime()
        y       = self._y_for_minutes(now.hour() * 60 + now.minute())
        label   = now.toString("HH:mm")

        # Glowing line
        for width, alpha in [(6, 20), (3, 50), (1, 220)]:
            pen = QPen(QColor(0, 229, 255, alpha), width)
            p.setPen(pen)
            p.drawLine(self.LEFT_MARGIN, y, self.width() - self.RIGHT_PAD, y)

        # Leading dot
        p.setBrush(QBrush(CLR_ACCENT))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(self.LEFT_MARGIN - 4, y - 4, 8, 8)

        # Time badge
        badge = QRect(2, y - 9, 46, 18)
        p.setBrush(QBrush(QColor(0, 229, 255, 25)))
        p.drawRoundedRect(badge, 4, 4)
        p.setPen(QPen(CLR_ACCENT))
        p.setFont(QFont("Consolas", 8))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_drag_ghost(self, p: QPainter):
        if not self._drag_task or not self._drag_time:
            return
        ghost = ScheduledTask(
            id=self._drag_task.id,
            title=self._drag_task.title,
            start=self._drag_time,
            duration=self._drag_task.duration,
            category=self._drag_task.category,
            priority=self._drag_task.priority,
        )
        rect = self._task_rect(ghost)
        self._paint_task_block(p, ghost, rect, alpha_mul=0.55)

        # Snap indicator
        p.setPen(QPen(CLR_ACCENT, 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 6, 6)

    # ── Mouse events ──────────────────────────────────────────────────────────

    def mousePressEvent(self, e: QMouseEvent):
        task = self._task_at(e.pos())
        if e.button() == Qt.MouseButton.LeftButton:
            if task:
                # Check if click is on the right edge (complete button zone)
                trect = self._task_rect(task)
                if e.pos().x() > trect.right() - 28:
                    task.done = not task.done
                    self.task_completed.emit(task.id)
                    self.update()
                    return
                # Start drag
                self._drag_task   = task
                click_y           = e.pos().y()
                task_y            = self._y_for_minutes(task.start_minutes())
                self._drag_offset = self._minutes_for_y(click_y - task_y)
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            else:
                # Empty slot clicked → emit add signal
                snapped_min = self._minutes_for_y(e.pos().y())
                self.slot_clicked.emit(QTime(snapped_min // 60, snapped_min % 60))

        elif e.button() == Qt.MouseButton.RightButton and task:
            self.task_clicked.emit(task.id)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_task:
            raw_y       = e.pos().y()
            raw_min     = self._minutes_for_y(raw_y) - self._drag_offset
            snapped_min = max(0, raw_min - (raw_min % 5))
            self._drag_time = QTime(snapped_min // 60 % 24, snapped_min % 60)
            self.update()
        else:
            task = self._task_at(e.pos())
            new_hover = task.id if task else None
            if new_hover != self._hover_task:
                self._hover_task = new_hover
                self.setCursor(
                    QCursor(Qt.CursorShape.OpenHandCursor)
                    if task else QCursor(Qt.CursorShape.ArrowCursor)
                )
                self.update()

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._drag_task and self._drag_time:
            self._drag_task.start = self._drag_time
            self.task_moved.emit(self._drag_task.id, self._drag_time)
        self._drag_task  = None
        self._drag_time  = None
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()


# ── Stat strip ────────────────────────────────────────────────────────────────

class StatStrip(QWidget):
    """Top summary bar: total tasks, done count, completion %, time remaining."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(0)

        self._stats: list[tuple[str, str]] = [
            ("TOTAL", "0"),
            ("DONE", "0"),
            ("PROGRESS", "0%"),
            ("REMAINING", "0 min"),
        ]
        self._labels: list[tuple[QLabel, QLabel]] = []

        for i, (k, v) in enumerate(self._stats):
            col = QVBoxLayout()
            col.setSpacing(2)
            col.setAlignment(Qt.AlignmentFlag.AlignCenter)

            k_lbl = QLabel(k)
            k_lbl.setStyleSheet(
                "font-size:8px; letter-spacing:2px; color:#334155; font-family:Consolas;"
            )
            k_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            v_lbl = QLabel(v)
            v_lbl.setStyleSheet(
                "font-size:16px; font-weight:bold; color:#e2e8f0; font-family:Consolas;"
            )
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            col.addWidget(k_lbl)
            col.addWidget(v_lbl)
            self._labels.append((k_lbl, v_lbl))

            lay.addLayout(col, 1)

            if i < len(self._stats) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setFixedWidth(1)
                sep.setStyleSheet("color: rgba(30,45,69,180);")
                lay.addWidget(sep)

    def update_stats(self, tasks: list[ScheduledTask]):
        total    = len(tasks)
        done     = sum(1 for t in tasks if t.done)
        pct      = int(done / total * 100) if total else 0
        rem_min  = sum(t.duration for t in tasks if not t.done)
        rem_str  = f"{rem_min // 60}h {rem_min % 60}m" if rem_min >= 60 else f"{rem_min} min"

        values = [str(total), str(done), f"{pct}%", rem_str]
        colors = ["#e2e8f0", "#10b981", "#00e5ff", "#8b5cf6"]

        for (_, v_lbl), val, col in zip(self._labels, values, colors):
            v_lbl.setText(val)
            v_lbl.setStyleSheet(
                f"font-size:16px; font-weight:bold; color:{col}; font-family:Consolas;"
            )

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(13, 17, 32))
        p.setBrush(QBrush(QColor(30, 45, 69, 80)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(0, self.height() - 1, self.width(), 1)


# ── Main scheduler window ──────────────────────────────────────────────────────

class AriaSchedulerView(QWidget):
    """
    ARIA daily scheduler — full window.

    Signals
    -------
    task_added(ScheduledTask)
    task_updated(str, dict)   — id + changed fields
    task_deleted(str)
    task_completed(str)       — id toggled done
    """

    task_added     = pyqtSignal(object)
    task_updated   = pyqtSignal(str, dict)
    task_deleted   = pyqtSignal(str)
    task_completed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._drag_pos = None
        self._tasks: dict[str, ScheduledTask] = {}
        self._task_counter = 0

        self._init_window()
        self._build_ui()
        self.setStyleSheet(GLOBAL_STYLE)
        self._load_demo_tasks()

    # ── Window init ───────────────────────────────────────────────────────────

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(720, 640)
        screen = QApplication.primaryScreen().geometry()
        self.move(
            screen.width() // 2 - 360,
            screen.height() // 2 - 320
        )

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        root.addWidget(self._make_title_bar())

        # Stat strip
        self._stat_strip = StatStrip()
        root.addWidget(self._stat_strip)

        # Divider
        div = QFrame(); div.setFixedHeight(1)
        div.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 transparent, stop:0.3 rgba(0,229,255,60),"
            "stop:0.7 rgba(139,92,246,40), stop:1 transparent);"
        )
        root.addWidget(div)

        # Toolbar
        root.addWidget(self._make_toolbar())

        # Timeline scroll
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._canvas = TimelineCanvas()
        self._canvas.task_moved.connect(self._on_task_moved)
        self._canvas.task_clicked.connect(self._on_task_right_clicked)
        self._canvas.task_completed.connect(self._on_task_completed)
        self._canvas.slot_clicked.connect(self._on_slot_clicked)

        self._scroll.setWidget(self._canvas)
        root.addWidget(self._scroll, 1)

        # Resize grip
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 6, 4)
        grip_row.addStretch()
        grip = QSizeGrip(self)
        grip.setStyleSheet("background:transparent;")
        grip_row.addWidget(grip)
        root.addLayout(grip_row)

        # Scroll to current time on open
        QTimer.singleShot(100, self._scroll_to_now)

    def _make_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 12, 0)
        lay.setSpacing(10)

        icon = QLabel("◷")
        icon.setStyleSheet("color:#00e5ff; font-size:16px;")
        fx = QGraphicsDropShadowEffect()
        fx.setBlurRadius(16); fx.setOffset(0, 0); fx.setColor(QColor(0, 229, 255))
        icon.setGraphicsEffect(fx)

        title = QLabel("ARIA  SCHEDULER")
        title.setStyleSheet(
            "font-size:12px; font-weight:bold; letter-spacing:4px; color:#e2e8f0;"
        )

        self._date_label = QLabel(datetime.now().strftime("%A, %d %B %Y").upper())
        self._date_label.setStyleSheet(
            "font-size:9px; letter-spacing:2px; color:#334155;"
        )

        hide_btn = QPushButton("▣")
        hide_btn.setToolTip("Hide"); hide_btn.clicked.connect(self.hide)
        hide_btn.setFixedSize(24, 24)

        close_btn = QPushButton("✕")
        close_btn.setToolTip("Close"); close_btn.clicked.connect(self.close)
        close_btn.setFixedSize(24, 24)

        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addWidget(self._date_label)
        lay.addStretch()
        lay.addWidget(hide_btn)
        lay.addWidget(close_btn)
        return bar

    def _make_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(40)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(8)

        add_btn = QPushButton("＋  ADD TASK")
        add_btn.setFixedHeight(28)
        add_btn.setStyleSheet(
            "QPushButton { background: rgba(0,229,255,12); border:1px solid rgba(0,229,255,100);"
            " border-radius:6px; color:#00e5ff; font-size:10px; letter-spacing:1px; padding:0 12px; }"
            "QPushButton:hover { background:rgba(0,229,255,24); }"
        )
        add_btn.clicked.connect(lambda: self._open_add_dialog())

        today_btn = QPushButton("⊙  NOW")
        today_btn.setFixedHeight(28)
        today_btn.setStyleSheet(
            "QPushButton { border:1px solid rgba(30,45,69,180); border-radius:6px;"
            " color:#64748b; font-size:10px; letter-spacing:1px; padding:0 12px; }"
            "QPushButton:hover { border-color:rgba(0,229,255,80); color:#00e5ff; }"
        )
        today_btn.clicked.connect(self._scroll_to_now)

        # Category filter legend
        lay.addWidget(add_btn)
        lay.addWidget(today_btn)
        lay.addStretch()

        for cat, color in CATEGORY_COLORS.items():
            dot = QLabel(f"◆ {cat}")
            dot.setStyleSheet(
                f"font-size:9px; color: rgba({color.red()},{color.green()},{color.blue()},180);"
                "letter-spacing:1px;"
            )
            lay.addWidget(dot)

        return bar

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)

        p.setBrush(QBrush(QColor(8, 11, 20, 240)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 12, 12)

        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor(0, 229, 255, 70))
        g.setColorAt(0.5, QColor(139, 92, 246, 40))
        g.setColorAt(1, QColor(0, 229, 255, 25))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(g), 1.2))
        p.drawRoundedRect(r, 12, 12)

        # Top accent bar
        tb = QLinearGradient(20, 0, self.width() - 20, 0)
        tb.setColorAt(0, QColor(0, 229, 255, 0))
        tb.setColorAt(0.3, QColor(0, 229, 255, 100))
        tb.setColorAt(0.7, QColor(139, 92, 246, 80))
        tb.setColorAt(1, QColor(0, 229, 255, 0))
        p.setBrush(QBrush(tb)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(20, 0, self.width() - 40, 2, 1, 1)

    # ── Dragging window ───────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 44:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _):
        self._drag_pos = None

    # ── Task management ───────────────────────────────────────────────────────

    def _new_id(self) -> str:
        self._task_counter += 1
        return f"task_{self._task_counter:04d}"

    def add_task(self, task: ScheduledTask):
        self._tasks[task.id] = task
        self._canvas.add_task(task)
        self._refresh_stats()
        self.task_added.emit(task)

    def _refresh_stats(self):
        self._stat_strip.update_stats(list(self._tasks.values()))

    def _scroll_to_now(self):
        now     = QTime.currentTime()
        y       = self._canvas._y_for_minutes(now.hour() * 60 + now.minute())
        bar     = self._scroll.verticalScrollBar()
        target  = max(0, y - self._scroll.height() // 3)
        bar.setValue(target)

    # ── Dialog helpers ────────────────────────────────────────────────────────

    def _open_add_dialog(self, prefill_time: Optional[QTime] = None):
        dlg = TaskDialog(parent=self)
        if prefill_time:
            dlg._start.setTime(prefill_time)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_task_data()
            task = ScheduledTask(id=self._new_id(), **data)
            self.add_task(task)

    def _open_edit_dialog(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        dlg = TaskDialog(task=task, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_task_data()
            for k, v in data.items():
                setattr(task, k, v)
            self._canvas.update()
            self._refresh_stats()
            self.task_updated.emit(task_id, data)

    # ── Canvas slots ──────────────────────────────────────────────────────────

    def _on_task_moved(self, task_id: str, new_start: QTime):
        if task_id in self._tasks:
            self._tasks[task_id].start = new_start
            self.task_updated.emit(task_id, {"start": new_start})

    def _on_task_right_clicked(self, task_id: str):
        """Show context menu on right-click."""
        task = self._tasks.get(task_id)
        if not task:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#0d1120; border:1px solid rgba(0,229,255,50);
                    border-radius:8px; padding:4px 0; font-family:Consolas; font-size:11px; }
            QMenu::item { padding:6px 16px; color:#e2e8f0; }
            QMenu::item:selected { background:rgba(0,229,255,12); color:#00e5ff;
                                   border-left:2px solid #00e5ff; }
            QMenu::separator { height:1px; background:rgba(0,229,255,20); margin:3px 10px; }
        """)

        done_label = "◎  Mark Incomplete" if task.done else "✓  Mark Complete"
        menu.addAction(done_label,  lambda: self._toggle_done(task_id))
        menu.addAction("✎  Edit Task",   lambda: self._open_edit_dialog(task_id))
        menu.addSeparator()
        menu.addAction("✕  Delete Task", lambda: self._delete_task(task_id))

        menu.exec(QCursor.pos())

    def _on_task_completed(self, task_id: str):
        self._refresh_stats()
        self.task_completed.emit(task_id)

    def _on_slot_clicked(self, time: QTime):
        self._open_add_dialog(prefill_time=time)

    def _toggle_done(self, task_id: str):
        task = self._tasks.get(task_id)
        if task:
            task.done = not task.done
            self._canvas.update()
            self._refresh_stats()
            self.task_completed.emit(task_id)

    def _delete_task(self, task_id: str):
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._canvas.remove_task(task_id)
            self._refresh_stats()
            self.task_deleted.emit(task_id)

    # ── Demo data ─────────────────────────────────────────────────────────────

    def _load_demo_tasks(self):
        demo = [
            ScheduledTask("demo_1", "Morning Briefing",        QTime( 8, 30),  30, "meeting",  "high"),
            ScheduledTask("demo_2", "Deep Work — Sprint Dev",  QTime( 9,  0), 120, "focus",    "critical"),
            ScheduledTask("demo_3", "Email & Comms",           QTime(11, 15),  30, "work",     "normal"),
            ScheduledTask("demo_4", "Lunch Break",             QTime(12,  0),  60, "break",    "low"),
            ScheduledTask("demo_5", "Code Review Session",     QTime(13,  0),  90, "work",     "high"),
            ScheduledTask("demo_6", "1-on-1 with Manager",    QTime(14, 30),  30, "meeting",  "high"),
            ScheduledTask("demo_7", "Feature Planning",        QTime(15,  0),  60, "work",     "normal"),
            ScheduledTask("demo_8", "Gym / Exercise",          QTime(17, 30),  60, "personal", "normal"),
            ScheduledTask("demo_9", "Documentation",           QTime(19,  0),  45, "work",     "low"),
        ]
        demo[0].done = True   # Morning briefing already done
        demo[3].done = True   # Lunch already done
        for t in demo:
            self._tasks[t.id] = t
        self._canvas.set_tasks(list(self._tasks.values()))
        self._refresh_stats()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = AriaSchedulerView()
    w.show()
    sys.exit(app.exec())