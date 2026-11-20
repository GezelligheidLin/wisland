"""灵动岛内的待办预览：查看与完成留在岛内，编辑交给独立窗口。"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from html import escape

from PySide6 import QtCore, QtGui, QtWidgets

from ...services.todo import Todo, TodoService, daily_time_label
from ..theme import G, W, icon_font, theme, ui_font
from ..widgets import Card, Label, hbox, painter, vbox
from .base import Page

Qt = QtCore.Qt
PREVIEW_LIMIT = 4
UNDO_MS = 6000


def _preview_detail(todo: Todo, now: int) -> tuple[int, int | float, str]:
    """返回显示顺序、组内时间及简短状态；无紧急项时用未来及未排期事项补位。"""
    today = datetime.fromtimestamp(now).date()
    due = datetime.fromtimestamp(todo.due_at) if todo.due_at is not None else None
    if todo.pending:
        return 0, todo.remind_at if todo.remind_at is not None else now, "提醒待处理"
    if due is not None and todo.due_at <= now:
        return 1, todo.due_at, "已逾期"
    if due is not None and due.date() == today:
        return 2, todo.due_at, f"今天 {due:%H:%M} 到期"
    remind = todo.next_reminder_at()
    dates = [value for value in (todo.due_at, remind) if value is not None]
    if dates:
        stamp = min(dates)
        when = datetime.fromtimestamp(stamp)
        if todo.daily_minute is not None and stamp == remind and stamp != todo.due_at:
            label = f"稍后 {when:%m-%d %H:%M}" if stamp == todo.remind_at else daily_time_label(todo.daily_minute)
            return (3 if when.date() == today else 4), stamp, label
        if when.date() == today:
            return 3, stamp, f"今天 {when:%H:%M} 提醒"
        return 4, stamp, f"{when:%m-%d %H:%M} {'到期' if stamp == todo.due_at else '提醒'}"
    return 5, float("inf"), "未排期"


class _TitleButton(QtWidgets.QPushButton):
    def __init__(self, title: str):
        super().__init__(objectName="todoTitle")
        self._title = title
        self.setFont(ui_font(14, W.Medium))
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.setAccessibleName(f"编辑待办：{title}")
        self.setToolTip(f"<qt>编辑待办：{escape(title)}</qt>")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # QPushButton 将 & 当助记键；标题里的 & 应照常显示。
        text = self.fontMetrics().elidedText(" ".join(self._title.split()), Qt.TextElideMode.ElideRight,
                                             max(0, self.width() - 12))
        self.setText(text.replace("&", "&&"))


class _CompleteButton(QtWidgets.QPushButton):
    """保持完整点击区域，空圆不会被误读成待办已完成。"""

    def __init__(self, title: str):
        super().__init__()
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("标记完成")
        self.setAccessibleName(f"完成待办：{title}")

    def paintEvent(self, event):
        p = painter(self)
        ring = QtCore.QRectF(self.rect()).adjusted(7, 7, -7, -7)
        active = self.underMouse() or self.hasFocus()
        p.setPen(QtGui.QPen(theme.accent if active else theme.text2(), 1.2))
        p.drawEllipse(ring)
        if active:
            p.setFont(icon_font(10))
            p.drawText(ring, Qt.AlignmentFlag.AlignCenter, G.CHECK)


class TodoPage(Page):
    key = "todo"
    glyph = G.TODO
    title = "待办"
    height = 262

    def __init__(self, service: TodoService, present):
        super().__init__()
        self.service = service
        self._present = present
        self._undo: tuple[Todo, int] | None = None
        self._snapshot = None
        self._undo_timer = QtCore.QTimer(self, singleShot=True, interval=UNDO_MS, timeout=self._expire_undo)
        self._refresh_timer = QtCore.QTimer(self, interval=30000, timeout=self.refresh)

        self._summary = Label("", 12, level=2)
        add = self._button("＋ 添加", lambda: self._present(quick=True), "todoAdd")
        header = hbox(Label("待办事项", 18, W.DemiBold), self._summary, None, add, spacing=10)

        self._list = Card(radius=16)
        self._rows = vbox(spacing=2, margins=(8, 8, 8, 8))
        self._list.setLayout(self._rows)
        self._status = Label("", 12, level=2)
        self._undo_btn = self._button("撤销", self._undo_last, "todoLink")
        self._undo_btn.hide()
        all_tasks = self._button("查看全部 →", lambda: self._present(), "todoLink")
        footer = hbox(self._status, self._undo_btn, None, all_tasks, spacing=4)
        self.setLayout(vbox(header, (self._list, 1), footer, spacing=8, margins=(2, 2, 2, 2)))
        self.service.changed.connect(self._refresh_if_active)
        theme.changed.connect(self._style)
        self._style()

    @staticmethod
    def _button(text, action, name):
        button = QtWidgets.QPushButton(text, objectName=name)
        button.setFont(ui_font(12.5, W.Medium))
        button.setFixedHeight(30)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(action)
        return button

    def on_show(self):
        super().on_show()
        self.refresh()
        self._refresh_timer.start()

    def on_hide(self):
        super().on_hide()
        self._refresh_timer.stop()

    def _refresh_if_active(self):
        if self.active:
            self.refresh()

    def refresh(self):
        now = int(time.time())
        error = False
        try:
            open_tasks = [todo for todo in self.service.store.list() if todo.completed_at is None]
        except (OSError, sqlite3.Error):
            open_tasks = []
            error = True
        ranked = [(_preview_detail(todo, now), todo) for todo in open_tasks]
        ranked.sort(key=lambda entry: (*entry[0][:2], -entry[1].id))
        preview = tuple((todo, detail[0], detail[2]) for detail, todo in ranked[:PREVIEW_LIMIT])
        pending = sum(todo.pending for todo in open_tasks)
        snapshot = (preview, len(open_tasks), pending, error)
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        while self._rows.count():
            widget = self._rows.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._summary.setText("" if error else f"{len(open_tasks)} 项未完成" + (f" · {pending} 项提醒" if pending else ""))
        if not preview:
            align = Qt.AlignmentFlag.AlignCenter
            self._rows.addStretch()
            self._rows.addWidget(Label("无法读取待办" if error else "没有未完成的待办", 15, W.DemiBold, align=align))
            self._rows.addWidget(Label("请检查本地存储后重试" if error else "点击「添加」记下一件事", 12, level=2, align=align))
            self._rows.addStretch()
            return
        for todo, priority, detail in preview:
            row = QtWidgets.QWidget()
            check = _CompleteButton(todo.title)
            check.setObjectName("todoCheck")
            check.clicked.connect(lambda checked=False, key=todo.id: self._complete(key))
            title = _TitleButton(todo.title)
            title.clicked.connect(lambda checked=False, key=todo.id: self._present(todo_id=key))
            tag = Label(detail, 12, level=2, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        color=theme.accent if priority == 0 else None, weight=W.Medium if priority < 2 else W.Normal)
            row.setLayout(hbox(check, (title, 1), tag, spacing=4, margins=(4, 3, 8, 3)))
            self._rows.addWidget(row)
        self._rows.addStretch()

    def _complete(self, todo_id: int):
        now = int(time.time())
        try:
            previous = self.service.store.complete_for_preview(todo_id, now)
        except (OSError, sqlite3.Error):
            self._status.setText("无法保存，请重试")
            if self._undo is None:
                self._undo_timer.start()
            return
        if previous is None:
            self.refresh()
            return
        self._undo = (previous, now)
        self._status.setText("已完成 1 项")
        self._undo_btn.show()
        self._undo_timer.start()
        self.service.scan(edited=True)
        self.refresh()

    def _undo_last(self):
        if self._undo is None:
            return
        try:
            restored = self.service.store.undo_preview_completion(*self._undo)
        except (OSError, sqlite3.Error):
            self._status.setText("无法撤销，请重试")
            return
        self._expire_undo()
        if not restored:
            self._status.setText("待办已更改，无法撤销")
            self._undo_timer.start()
        self.service.scan(edited=True)
        self.refresh()

    def _expire_undo(self):
        self._undo_timer.stop()
        self._undo = None
        self._undo_btn.hide()
        self._status.setText("")

    def _style(self):
        ink, accent = theme.text().name(), theme.accent.name()
        hover = theme.hover().name(QtGui.QColor.NameFormat.HexArgb)
        self.setStyleSheet(f"""
            QPushButton {{ color: {ink}; border: 1px solid transparent; border-radius: 8px; background: transparent; }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:focus {{ border-color: {accent}; }}
            QPushButton#todoTitle {{ text-align: left; padding: 0 5px; }}
            QPushButton#todoAdd {{ color: {theme.pill_text().name()};
                background: {theme.active_pill().name(QtGui.QColor.NameFormat.HexArgb)}; padding: 0 11px; }}
            QPushButton#todoAdd:hover {{ border-color: {accent}; }}
            QPushButton#todoLink {{ color: {ink}; padding: 0 7px; }}
        """)
        self._snapshot = None
        self._refresh_if_active()
