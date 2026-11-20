"""独立待办窗口：快速收集、按时间查看并处理提醒。"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from html import escape

from PySide6 import QtCore, QtGui, QtWidgets

from ..services.todo import NOTES_LIMIT, TITLE_LIMIT, Todo, TodoService, daily_time_label
from .reminder_picker import ReminderDateTimePicker
from .theme import G, ui_font
from .widgets import ChoiceBox, glyph_pixmap

Qt = QtCore.Qt
REMINDER_PRESETS = (("10 分钟后", 600), ("1 小时后", 3600), ("明天 09:00", "tomorrow"))
CUSTOM_REMINDER = "custom"
DAILY_REMINDER = "daily"
DAILY_CHOICE = "每天定时…"
SECTIONS = {
    "today": ("今天", "今天到期、已逾期或有提醒待处理的事项。", "今天没有需要处理的待办。"),
    "upcoming": ("接下来", "查看安排在未来的事项。", "没有安排在未来的待办。"),
    "inbox": ("未排期", "先记下来，等有空再决定何时处理。", "没有未排期的待办。"),
    "completed": ("已完成", "查看已完成的事项，也可以取消勾选恢复。", "还没有完成的待办。"),
}


def _when(value: int | None) -> str:
    return datetime.fromtimestamp(value).strftime("%m-%d %H:%M") if value is not None else "—"


def _preset_time(preset: int | str | None, now: int) -> int | None:
    if isinstance(preset, int):
        return now + preset
    if preset == "tomorrow":
        tomorrow = QtCore.QDateTime.fromSecsSinceEpoch(now).date().addDays(1)
        return QtCore.QDateTime(tomorrow, QtCore.QTime(9, 0)).toSecsSinceEpoch()
    return None


class _ReminderDialog(QtWidgets.QDialog):
    """安排一次性提醒、稍后提醒或每日固定时刻。"""

    def __init__(self, parent, *, snooze: bool = False, initial: int | None = None,
                 daily: bool = False, recurring_snooze: bool = False):
        super().__init__(parent, objectName="reminderDialog")
        self._daily = daily
        self.setWindowTitle("每日提醒" if daily else "稍后提醒" if snooze else "自定义提醒时间")
        self.setFixedWidth(376)
        self.timestamp = None
        self.daily_minute = None
        self.setFont(ui_font(14))
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(16, 15, 16, 14)
        box.setSpacing(10)
        heading = QtWidgets.QVBoxLayout()
        heading.setSpacing(3)
        name = "每天定时" if daily else "稍后提醒" if snooze else "安排提醒"
        heading.addWidget(QtWidgets.QLabel(name, objectName="reminderHeading"))
        if daily:
            subtitle = "每天提醒一次，完成后停止。\n如果今天已过设定时间，从明天开始。"
        elif recurring_snooze:
            subtitle = "只推迟这次提醒。每天的固定提醒时间不变。"
        elif snooze:
            subtitle = "为这次提醒重新选时间，不影响截止日期。"
        else:
            subtitle = "选择日期与时间，按电脑本地时间提醒。"
        heading.addWidget(QtWidgets.QLabel(subtitle, objectName="reminderMuted", wordWrap=True))
        box.addLayout(heading)
        self.mode = ChoiceBox()
        self.mode.setAccessibleName("稍后提醒时间")
        if snooze:
            for label, value in REMINDER_PRESETS:
                self.mode.addItem(label, value)
            self.mode.addItem("自定义时间…", CUSTOM_REMINDER)
        box.addWidget(self.mode)
        self.mode.setVisible(snooze)
        now = int(time.time())
        today = QtCore.QDateTime.fromSecsSinceEpoch(now).date()
        stamp = initial if not daily and initial is not None and initial > now else now + 600
        start = QtCore.QDateTime.fromSecsSinceEpoch(stamp // 60 * 60)
        # 随对话框一次性显示，避免把尚未加入布局的选择器先显示成临时窗口。
        self.picker = ReminderDateTimePicker(start, today, parent=self, daily=daily)
        if daily:
            hour, minute = divmod(initial if initial is not None else 9 * 60, 60)
            self.picker.time.setTime(QtCore.QTime(hour, minute))
        box.addWidget(self.picker)
        if snooze:
            self.picker.hide()
        self.mode.currentIndexChanged.connect(self._toggle_custom)
        self.error = QtWidgets.QLabel(objectName="reminderError", wordWrap=True)
        self.error.hide()
        box.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        ok.setText("确定")
        ok.setObjectName("reminderConfirm")
        ok.setDefault(True)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer = QtWidgets.QFrame(objectName="reminderFooter")
        footer_layout = QtWidgets.QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 10, 0, 0)
        footer_layout.addWidget(buttons)
        box.addWidget(footer)
        hints = QtGui.QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(self._apply_style)
        self._apply_style(hints.colorScheme())
        (self.mode if snooze else self.picker).setFocus()

    def _toggle_custom(self):
        custom = self.mode.currentData() == CUSTOM_REMINDER
        self.picker.setVisible(custom)
        self.error.hide()
        self.adjustSize()
        if custom:
            self.picker.setFocus()

    def _apply_style(self, scheme):
        dark = scheme == Qt.ColorScheme.Dark
        bg, card, text, muted, faint, line, hover, accent, ink, error = (
            ("#191a1d", "#22242a", "#f1f3f7", "#b0b6c4", "#777e8d", "#383c46", "#323645",
             "#87b7ff", "#111b2a", "#ffaaa2") if dark else
            ("#fdfdfd", "#ffffff", "#202633", "#5c6679", "#8b93a2", "#e4e8ef", "#edf2fa",
             "#1762d2", "#ffffff", "#aa342c")
        )
        self.picker.calendar.set_colors(text, muted, faint, accent, ink)
        self.setStyleSheet(f"""
            QDialog#reminderDialog {{ background:{bg}; }}
            QLabel {{ color:{text}; }}
            QLabel#reminderHeading {{ font-size:21px; font-weight:600; }}
            QLabel#reminderMuted {{ color:{muted}; font-size:12px; }}
            QLabel#reminderClockLabel {{ font-weight:600; }}
            QLabel#reminderError {{ color:{error}; font-size:12px; }}
            QFrame#reminderCalendarCard, QFrame#reminderClockCard {{
                background:{card}; border:1px solid {line}; border-radius:12px; }}
            QCalendarWidget#reminderCalendar QAbstractItemView {{
                background:{card}; alternate-background-color:{card}; color:{muted};
                selection-background-color:transparent; selection-color:{text}; border:0; outline:0; }}
            QPushButton#reminderCalNav {{ color:{text}; background:transparent; border:1px solid transparent;
                border-radius:7px; min-height:27px; padding:0 3px; }}
            QDateEdit#reminderMonth {{ color:{text}; background:transparent; font-weight:600; min-height:27px; }}
            QPushButton#reminderCalNav:hover {{ background:{hover}; }}
            QPushButton#reminderCalNav:disabled {{ color:{faint}; }}
            QPushButton#reminderCalNav:focus {{ border-color:{accent}; }}
            QFrame#reminderClockField {{ background:{hover}; border:1px solid {line}; border-radius:8px; }}
            QTimeEdit#reminderClock {{ color:{text}; background:transparent; min-height:34px;
                font-size:19px; font-weight:600; }}
            QDateEdit#reminderMonth QLineEdit, QTimeEdit#reminderClock QLineEdit {{
                color:{text}; background:transparent; border:0; padding:0; min-height:0;
                selection-background-color:{accent}; selection-color:{ink}; }}
            QFrame#reminderFooter {{ border-top:1px solid {line}; }}
            QDialogButtonBox QPushButton {{ color:{text}; background:{card}; border:1px solid {line};
                border-radius:8px; min-height:30px; min-width:54px; padding:0 9px; }}
            QDialogButtonBox QPushButton:hover {{ background:{hover}; }}
            QDialogButtonBox QPushButton:focus {{ border-color:{accent}; }}
            QPushButton#reminderConfirm {{ color:{ink}; background:{accent}; border:1px solid {accent};
                border-radius:8px; min-height:30px; padding:0 9px; }}
            QPushButton#reminderConfirm:hover {{ border-color:{text}; }}
            QPushButton#reminderConfirm:focus {{ border-color:{text}; }}
        """)

    def accept(self):
        if self._daily:
            clock = self.picker.time.time()
            self.daily_minute = clock.hour() * 60 + clock.minute()
            super().accept()
            return
        now = int(time.time())
        mode = self.mode.currentData()
        if mode is None or mode == CUSTOM_REMINDER:
            chosen = self.picker.date_time()
            stamp = chosen.toSecsSinceEpoch() if chosen.isValid() else None
        else:
            stamp = _preset_time(mode, now)
        if stamp is None or stamp <= now:
            self.error.setText("提醒时间必须晚于现在，请重新选择。")
            self.error.show()
            self.picker.setFocus()
            return
        self.timestamp = stamp
        super().accept()


class _Editor(QtWidgets.QDialog):
    def __init__(self, todo: Todo, parent, save):
        super().__init__(parent)
        self.setWindowTitle("编辑待办")
        self.setMinimumWidth(410)
        self.todo = todo
        self._save = save
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(20, 18, 20, 16)
        box.setSpacing(12)
        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        box.addLayout(form)
        self.title = QtWidgets.QLineEdit(todo.title)
        self.title.setMaxLength(TITLE_LIMIT)
        form.addRow("标题", self.title)
        self.notes = QtWidgets.QPlainTextEdit(todo.notes)
        self.notes.setPlaceholderText("可留空")
        self.notes.setFixedHeight(96)
        form.addRow("备注", self.notes)
        self.due_on, self.due_at = self._date_row(form, "截止时间", todo.due_at, 3600)
        self.reminder_mode = ChoiceBox()
        self.reminder_mode.setAccessibleName("提醒方式")
        self.reminder_mode.addItems(["不提醒", "仅一次", "每天定时"])
        form.addRow("提醒", self.reminder_mode)
        once = todo.remind_at if todo.daily_minute is None else None
        start = once if once is not None else (int(time.time()) + 600) // 60 * 60
        self.remind_at = QtWidgets.QDateTimeEdit(QtCore.QDateTime.fromSecsSinceEpoch(start))
        self.remind_at.setAccessibleName("一次性提醒日期和时间")
        self.remind_at.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.remind_at.setCalendarPopup(True)
        form.addRow("日期和时间", self.remind_at)
        hour, minute = divmod(todo.daily_minute if todo.daily_minute is not None else 9 * 60, 60)
        self.daily_at = QtWidgets.QTimeEdit(QtCore.QTime(hour, minute))
        self.daily_at.setAccessibleName("每日提醒时刻")
        self.daily_at.setDisplayFormat("HH:mm")
        form.addRow("每天 · 本地时间", self.daily_at)

        def update_reminder_fields(index):
            form.setRowVisible(self.remind_at, index == 1)
            form.setRowVisible(self.daily_at, index == 2)

        self.reminder_mode.setCurrentIndex(2 if todo.daily_minute is not None else 1 if once is not None else 0)
        self.reminder_mode.currentIndexChanged.connect(update_reminder_fields)
        update_reminder_fields(self.reminder_mode.currentIndex())
        if todo.completed_at is not None:
            self.reminder_mode.setEnabled(False)
            self.remind_at.setEnabled(False)
            self.daily_at.setEnabled(False)
        self.error = QtWidgets.QLabel(wordWrap=True)
        box.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save |
                                            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        box.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.title.selectAll()

    @staticmethod
    def _date_row(form, label, timestamp, offset):
        controls = QtWidgets.QHBoxLayout()
        option = QtWidgets.QCheckBox("设置")
        option.setAccessibleName(label)
        option.setChecked(timestamp is not None)
        date = QtWidgets.QDateTimeEdit(QtCore.QDateTime.fromSecsSinceEpoch(
            timestamp if timestamp is not None else (int(time.time()) + offset) // 60 * 60))
        date.setDisplayFormat("yyyy-MM-dd HH:mm")
        date.setCalendarPopup(True)
        date.setEnabled(option.isChecked())
        option.toggled.connect(date.setEnabled)
        controls.addWidget(option)
        controls.addWidget(date, 1)
        form.addRow(label, controls)
        return option, date

    def accept(self):
        due = self.due_at.dateTime().toSecsSinceEpoch() if self.due_on.isChecked() else None
        index = self.reminder_mode.currentIndex()
        remind = self.remind_at.dateTime().toSecsSinceEpoch() if index == 1 else None
        clock = self.daily_at.time()
        daily_minute = clock.hour() * 60 + clock.minute() if index == 2 else None
        if not self.title.text().strip():
            self.error.setText("请输入待办标题。")
        elif len(self.notes.toPlainText()) > NOTES_LIMIT:
            self.error.setText(f"备注最多 {NOTES_LIMIT} 个字符。")
        elif remind is not None and remind != self.todo.remind_at and remind <= int(time.time()):
            self.error.setText("新的提醒时间必须晚于现在。")
        else:
            if self._save(self.title.text(), self.notes.toPlainText(), due, remind, daily_minute=daily_minute):
                super().accept()
            else:
                self.error.setText("无法保存修改。请检查本地存储后重试。")


class TodoWindow(QtWidgets.QWidget):
    """可复用窗口；记录、完成和提醒处理只通过本地待办服务。"""

    def __init__(self, service: TodoService):
        super().__init__(objectName="todoWindow")
        self.service = service
        self._section = "today"
        self._todos: dict[int, Todo] = {}
        self._deleted = None
        self.setWindowTitle("Wisland 待办事项")
        self.setMinimumSize(690, 460)
        self.resize(890, 570)
        self.setFont(ui_font(14))
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        side = QtWidgets.QFrame(objectName="todoSidebar")
        side.setFixedWidth(180)
        outer.addWidget(side)
        sidebar = QtWidgets.QVBoxLayout(side)
        sidebar.setContentsMargins(12, 18, 12, 16)
        sidebar.setSpacing(5)
        sidebar.addWidget(QtWidgets.QLabel("待办事项", objectName="sidebarTitle"))
        sidebar.addSpacing(18)
        group = QtWidgets.QButtonGroup(self)
        self._nav = {}
        for key, (label, _, _) in SECTIONS.items():
            button = QtWidgets.QPushButton(label, checkable=True, objectName="todoNav")
            group.addButton(button)
            sidebar.addWidget(button)
            button.clicked.connect(lambda _checked, section=key: self._select(section))
            self._nav[key] = button
        sidebar.addStretch()
        sidebar.addWidget(QtWidgets.QLabel("仅保存在此设备", objectName="secondary"))
        sidebar.addWidget(QtWidgets.QLabel("Wisland 运行时提醒", objectName="secondary"))

        main = QtWidgets.QVBoxLayout()
        main.setContentsMargins(28, 22, 28, 16)
        main.setSpacing(12)
        outer.addLayout(main, 1)
        self._heading = QtWidgets.QLabel(objectName="todoHeading")
        self._summary = QtWidgets.QLabel(objectName="secondary")
        main.addWidget(self._heading)
        main.addWidget(self._summary)
        composer = QtWidgets.QHBoxLayout()
        main.addLayout(composer)
        self._title = QtWidgets.QLineEdit(placeholderText="输入待办，回车添加")
        self._title.setAccessibleName("新待办标题")
        self._title.setMaxLength(TITLE_LIMIT)
        self._quick_remind = ChoiceBox()
        self._quick_remind.setAccessibleName("提醒时间")
        self._quick_remind.addItem("不提醒")
        for label, value in REMINDER_PRESETS:
            self._quick_remind.addItem(label, value)
        self._quick_remind.addItem("自定义时间…", CUSTOM_REMINDER)
        self._quick_remind.addItem(DAILY_CHOICE, DAILY_REMINDER)
        self._custom_remind: int | None = None
        self._daily_remind: int | None = None
        self._last_remind_index = 0
        self._quick_remind.activated.connect(self._choose_reminder)
        self._add = QtWidgets.QPushButton("添加", objectName="todoAdd")
        self._add.setEnabled(False)
        composer.addWidget(self._title, 1)
        composer.addWidget(self._quick_remind)
        composer.addWidget(self._add)
        self._title.textChanged.connect(lambda text: self._add.setEnabled(bool(text.strip())))
        self._title.returnPressed.connect(self._create)
        self._add.clicked.connect(self._create)

        panel = QtWidgets.QFrame(objectName="todoCard")
        main.addWidget(panel, 1)
        stack = QtWidgets.QStackedLayout(panel)
        stack.setContentsMargins(1, 1, 1, 1)
        self._stack = stack
        self._empty = QtWidgets.QLabel(alignment=Qt.AlignmentFlag.AlignCenter, objectName="secondary", wordWrap=True)
        stack.addWidget(self._empty)
        self._tree = QtWidgets.QTreeWidget(rootIsDecorated=False, uniformRowHeights=True, allColumnsShowFocus=True)
        self._tree.setAccessibleName("待办清单")
        self._tree.setHeaderLabels(["事项", "截止", "提醒"])
        self._tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._tree.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setMinimumSectionSize(82)
        self._tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            self._tree.header().setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        stack.addWidget(self._tree)
        self._tree.currentItemChanged.connect(self._update_actions)
        self._tree.itemDoubleClicked.connect(self._edit)
        self._tree.itemChanged.connect(self._on_checked)

        actions = QtWidgets.QHBoxLayout()
        main.addLayout(actions)
        self._edit_btn = self._button(actions, "编辑", self._edit)
        self._snooze = self._button(actions, "稍后提醒…", self._snooze_reminder)
        self._ack = self._button(actions, "知道了", self._acknowledge_reminder)
        actions.addStretch()
        self._delete_btn = self._button(actions, "删除", self._delete)
        feedback = QtWidgets.QHBoxLayout()
        main.addLayout(feedback)
        self._status = QtWidgets.QLabel(objectName="secondary")
        feedback.addWidget(self._status, 1)
        self._undo = self._button(feedback, "撤销删除", self._undo_delete)
        self._undo.hide()
        self._undo_timer = QtCore.QTimer(self, singleShot=True, interval=8000, timeout=self._expire_undo)
        self._refresh_timer = QtCore.QTimer(self, interval=15000, timeout=self.refresh)
        self._refresh_timer.start()
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.New, self, activated=self._title.setFocus)
        self.service.changed.connect(self.refresh, Qt.ConnectionType.QueuedConnection)
        hints = QtGui.QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(self._apply_style)
        self._apply_style(hints.colorScheme())
        self._select("today")

    def present(self, screen, quick: bool = False, todo_id: int | None = None):
        """首次在入口所在屏幕居中；先准备清单，再显示窗口。"""
        self.ensurePolished()
        if not self.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide) and screen is not None:
            bounds = screen.availableGeometry()
            self.resize(min(bounds.width() - 32, self.width()), min(bounds.height() - 60, self.height()))
            self.move(bounds.center() - self.rect().center())
        self._refresh_view(todo_id)
        if todo_id is not None and todo_id not in self._todos:
            self._status.setText("此事项已不存在，或无法读取。")
        elif quick and todo_id is None:
            self._title.setFocus()
        # 首次显示和重新打开都只呈现完整内容，避免先出现空白或上次的清单。
        self.layout().activate()
        if self.isMinimized():
            self.showNormal()
        elif not self.isVisible():
            self.show()
        if not self.isActiveWindow():
            self.raise_()
            self.activateWindow()
        selected = self._selected()
        if todo_id is not None and selected and selected.id == todo_id:
            self._edit()

    @staticmethod
    def _button(layout, label, callback):
        button = QtWidgets.QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _select(self, section):
        self._section = section
        self._nav[section].setChecked(True)
        self.refresh()

    def refresh(self):
        if self.isVisible() and not self.isMinimized():
            self._refresh_view()

    def _refresh_view(self, todo_id: int | None = None):
        """用同一份数据定位目标事项并填充窗口，也可在窗口隐藏时准备首帧。"""
        selected = self._selected()
        now = int(time.time())
        try:
            todos = self.service.store.list()
        except (OSError, sqlite3.Error):
            todos = []
            read_failed = True
        else:
            read_failed = False
        self._todos = {todo.id: todo for todo in todos}
        target = self._todos.get(todo_id) if todo_id is not None else None
        if target is not None:
            selected = target
            self._section = target.section(now)
            self._nav[self._section].setChecked(True)
        title, summary, empty = SECTIONS[self._section]
        self._heading.setText(title)
        self._summary.setText(summary)
        if read_failed:
            empty = "无法读取本地待办。请检查存储目录权限后重试。"
        visible = [todo for todo in todos if todo.section(now) == self._section]
        visible.sort(key=lambda todo: self._sort_key(todo, now))
        pending = sum(todo.pending for todo in todos)
        self._nav["today"].setText(f"今天  ·  {pending}" if pending else "今天")
        bar = self._tree.verticalScrollBar()
        scroll = bar.value()
        with QtCore.QSignalBlocker(self._tree):
            self._tree.clear()
            selection = None
            for todo in visible:
                overdue = todo.completed_at is None and todo.due_at is not None and todo.due_at <= now
                deadline = ("逾期 · " if overdue else "") + _when(todo.due_at)
                reminder = "待处理" if todo.pending else _when(todo.remind_at)
                if todo.daily_minute is not None:
                    label = daily_time_label(todo.daily_minute)
                    reminder = f"待处理 · {label}" if todo.pending else label
                    if todo.completed_at is not None:
                        reminder = f"已停止 · {label}"
                    elif not todo.pending and todo.remind_at is not None and todo.remind_at == todo.next_reminder_at():
                        reminder = f"稍后 {_when(todo.remind_at)}"
                item = QtWidgets.QTreeWidgetItem(self._tree, [todo.title, deadline, reminder])
                if todo.daily_minute is not None:
                    item.setToolTip(2, daily_time_label(todo.daily_minute))
                item.setData(0, Qt.ItemDataRole.UserRole, todo.id)
                item.setToolTip(0, f"<qt>{escape(todo.title)}</qt>")
                if overdue:
                    item.setForeground(1, self._overdue_color)
                if todo.pending:
                    item.setForeground(2, self._pending_color)
                item.setSizeHint(0, QtCore.QSize(0, 39))
                state = Qt.CheckState.Checked if todo.completed_at is not None else Qt.CheckState.Unchecked
                item.setCheckState(0, state)
                if selected and todo.id == selected.id:
                    selection = item
            if selection is not None:
                self._tree.setCurrentItem(selection)
        bar.setValue(scroll)
        self._empty.setText(empty)
        self._stack.setCurrentWidget(self._tree if visible else self._empty)
        if target is not None and selection is not None:
            self._tree.scrollToItem(selection)
        self._update_actions()

    @staticmethod
    def _sort_key(todo: Todo, now: int):
        if todo.completed_at is not None:
            return (0, -todo.completed_at, -todo.id)
        group = 0 if todo.pending else 1 if todo.due_at is not None and todo.due_at <= now else 2
        dates = [date for date in (todo.due_at, todo.next_reminder_at()) if date is not None]
        return (group, min(dates, default=float("inf")), -todo.id)

    def _selected(self):
        item = self._tree.currentItem()
        return self._todos.get(item.data(0, Qt.ItemDataRole.UserRole)) if item else None

    def _update_actions(self):
        todo = self._selected()
        self._edit_btn.setEnabled(todo is not None)
        self._delete_btn.setEnabled(todo is not None)
        self._snooze.setEnabled(bool(todo and todo.pending))
        self._ack.setEnabled(bool(todo and todo.pending))

    def _write(self, operation, *args, **kwargs) -> bool | int:
        """刷新提醒状态；新增和编辑保留存储返回的事项 ID。"""
        try:
            result = operation(*args, **kwargs)
        except ValueError as exc:
            self._status.setText(str(exc))
            return False
        except (OSError, sqlite3.Error):
            self._status.setText("保存失败。请检查本地存储后重试。")
            return False
        self.service.scan(edited=True)
        return True if result is None else result

    def _pick_reminder(self, *, snooze: bool = False, initial: int | None = None,
                       daily: bool = False, recurring_snooze: bool = False) -> int | None:
        dialog = _ReminderDialog(self, snooze=snooze, initial=initial, daily=daily, recurring_snooze=recurring_snooze)
        try:
            if not dialog.exec():
                return None
            return dialog.daily_minute if daily else dialog.timestamp
        finally:
            dialog.deleteLater()

    def _choose_reminder(self, index: int):
        preset = self._quick_remind.itemData(index)
        if preset == DAILY_REMINDER:
            minute = self._pick_reminder(initial=self._daily_remind, daily=True)
            if minute is None:
                self._quick_remind.setCurrentIndex(self._last_remind_index)
                return
            self._daily_remind = minute
            label = daily_time_label(minute)
            self._quick_remind.setItemText(index, label)
            self._status.setText(f"添加后将{label}提醒，重新选择可修改。")
            self._title.setFocus()
        elif preset == CUSTOM_REMINDER:
            stamp = self._pick_reminder(initial=self._custom_remind)
            if stamp is None:
                self._quick_remind.setCurrentIndex(self._last_remind_index)
                return
            self._custom_remind = stamp
            self._quick_remind.setItemText(index, f"自定义 · {_when(stamp)}")
            self._status.setText(f"添加后将在 {_when(stamp)} 提醒，重新选择该时间可修改。")
            self._title.setFocus()
        else:
            self._status.clear()
        self._last_remind_index = index

    def _create(self):
        title = self._title.text().strip()
        if not title:
            return
        now = int(time.time())
        preset = self._quick_remind.currentData()
        daily_minute = None
        if preset == DAILY_REMINDER:
            remind = None
            daily_minute = self._daily_remind
            if daily_minute is None:
                self._status.setText("请选择每天的提醒时间。")
                self._quick_remind.setFocus()
                return
        elif preset == CUSTOM_REMINDER:
            remind = self._custom_remind
            if remind is None or remind <= now:
                self._status.setText("请选择晚于现在的自定义提醒时间。")
                self._quick_remind.setFocus()
                return
        else:
            remind = _preset_time(preset, now)
        todo_id = self._write(self.service.store.save, title, remind_at=remind, daily_minute=daily_minute)
        if todo_id:
            self._refresh_view(todo_id)
            self._title.clear()
            self._custom_remind = None
            self._daily_remind = None
            self._last_remind_index = 0
            self._quick_remind.setItemText(self._quick_remind.findData(CUSTOM_REMINDER), "自定义时间…")
            self._quick_remind.setItemText(self._quick_remind.findData(DAILY_REMINDER), DAILY_CHOICE)
            self._quick_remind.setCurrentIndex(0)
            self._title.setFocus()
            self._status.setText(f"已添加到「{SECTIONS[self._section][0]}」。")

    def _edit(self):
        todo = self._selected()
        if todo is None:
            return
        editor = _Editor(todo, self, lambda *values, **options: self._write(
            self.service.store.save, *values, todo_id=todo.id, **options))
        if editor.exec():
            self._status.setText("已保存修改。")
        editor.deleteLater()

    def _on_checked(self, item, column):
        todo = self._todos.get(item.data(0, Qt.ItemDataRole.UserRole))
        if column != 0 or todo is None:
            return
        completed = item.checkState(0) == Qt.CheckState.Checked
        if completed == (todo.completed_at is not None):
            return
        if self._write(self.service.store.set_completed, todo.id, completed, int(time.time())):
            self._status.setText("已完成。可在「已完成」取消勾选。" if completed else "已恢复为待办。")
        else:
            with QtCore.QSignalBlocker(self._tree):
                item.setCheckState(0, Qt.CheckState.Unchecked if completed else Qt.CheckState.Checked)

    def _snooze_reminder(self):
        todo = self._selected()
        if todo is None or not todo.pending:
            return
        repeating = todo.daily_minute is not None
        stamp = self._pick_reminder(snooze=True, recurring_snooze=repeating)
        if stamp is not None and self._write(self.service.store.resolve_reminder, todo.id, stamp):
            if repeating:
                self._status.setText("本次提醒已推迟，每日计划不变。")
            else:
                self._status.setText(f"将在 {_when(stamp)} 再次提醒，截止时间不变。")

    def _acknowledge_reminder(self):
        todo = self._selected()
        if todo and todo.pending and self._write(self.service.store.resolve_reminder, todo.id):
            self._status.setText("本次提醒已处理，每日计划不变。" if todo.daily_minute is not None
                                 else "提醒已处理，事项仍待完成。")

    def _delete(self):
        todo = self._selected()
        if todo is None:
            return
        try:
            deleted = self.service.store.delete(todo.id)
        except (OSError, sqlite3.Error):
            self._status.setText("删除失败，事项未移除。")
            return
        self.service.scan(edited=True)
        if deleted is not None:
            self._deleted = deleted
            self._undo_timer.start()
            self._undo.show()
            self._status.setText("已删除待办。")

    def _undo_delete(self):
        if self._deleted and self._write(self.service.store.undo_delete, self._deleted):
            self._expire_undo()
            self._status.setText("已撤销删除。")

    def _expire_undo(self):
        self._deleted = None
        self._undo_timer.stop()
        self._undo.hide()

    def _apply_style(self, scheme):
        dark = scheme == Qt.ColorScheme.Dark
        bg, side, card, line, text, muted, hover, accent = (
            ("#191a1d", "#222328", "#1d1e22", "#373940", "#f2f2f4", "#aeb0ba", "#34363e", "#75b2ff") if dark else
            ("#fdfdfd", "#f6f6f8", "#ffffff", "#e5e6eb", "#22242a", "#626672", "#eceef3", "#1364cc")
        )
        self._overdue_color = QtGui.QColor("#ffa78f" if dark else "#ae431f")
        self._pending_color = QtGui.QColor(accent)
        self.setStyleSheet(f"""
            QWidget#todoWindow {{ background:{bg}; }}
            QFrame#todoSidebar {{ background:{side}; border-right:1px solid {line}; }}
            QFrame#todoCard {{ background:{card}; border:1px solid {line}; border-radius:12px; }}
            QLabel {{ color:{text}; }}
            QLabel#secondary {{ color:{muted}; font-size:13px; }}
            QLabel#sidebarTitle {{ font-size:19px; font-weight:650; padding-left:6px; }}
            QLabel#todoHeading {{ font-size:29px; font-weight:650; }}
            QPushButton, QLineEdit, QComboBox {{ color:{text}; background:{card}; border:1px solid {line};
                border-radius:8px; min-height:30px; padding:0 9px; }}
            QPushButton#todoNav {{ text-align:left; background:transparent; border:1px solid transparent; }}
            QPushButton#todoNav:checked, QPushButton#todoNav:hover, QPushButton:hover {{ background:{hover}; }}
            QPushButton:disabled {{ color:{muted}; background:{bg}; }}
            QPushButton#todoAdd:enabled {{ color:{bg}; background:{accent}; border-color:{accent}; }}
            QPushButton:focus, QComboBox:focus, QLineEdit:focus:focus {{ border-color:{accent}; }}
            QComboBox {{ padding-right:24px; }}
            QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:top right;
                width:24px; border:none; background:transparent; }}
            QComboBox::down-arrow {{ image:none; }}
            QTreeView::indicator:unchecked {{ background:{card}; width:13px; height:13px;
                border:1px solid {muted}; border-radius:4px; }}
            QTreeWidget {{ color:{text}; background:transparent; outline:0; }}
            QTreeWidget::item {{ border-bottom:1px solid {line}; padding:0 4px; }}
            QTreeWidget::item:selected, QTreeWidget::item:hover {{ background:{hover}; color:{text}; }}
            QHeaderView::section {{ color:{muted}; background:{card}; border:0; border-bottom:1px solid {line};
                padding:7px; font-size:12px; }}
        """)
        for key, glyph in (("today", G.TODO), ("upcoming", G.BELL), ("inbox", G.CLIP), ("completed", G.CHECK)):
            self._nav[key].setIcon(QtGui.QIcon(glyph_pixmap(glyph, 16, QtGui.QColor(muted))))
        self.refresh()
