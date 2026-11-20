"""Compact inline date and time controls shared by custom reminder and snooze."""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import W, ui_font

Qt = QtCore.Qt


class _Calendar(QtWidgets.QCalendarWidget):
    """Use Qt's keyboard/selection model, with cells painted to match the dialog."""

    def __init__(self, today: QtCore.QDate):
        super().__init__(objectName="reminderCalendar")
        self.today = today
        self.setAccessibleName("提醒日期")
        self.setNavigationBarVisible(False)
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setLocale(QtCore.QLocale("zh_CN"))
        self.setVerticalHeaderFormat(QtWidgets.QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setMinimumDate(today)
        self.setFixedHeight(205)
        self.setFont(ui_font(13))
        self._cell_font = ui_font(13)
        self._selected_font = ui_font(13, W.DemiBold)
        palette = self.palette()
        self.set_colors(*(color.name() for color in (palette.text().color(), palette.mid().color(),
                         palette.placeholderText().color(), palette.highlight().color(), palette.highlightedText().color())))

    def set_colors(self, text: str, muted: str, faint: str, accent: str, ink: str):
        self._text, self._muted, self._faint, self._accent, self._ink = map(
            QtGui.QColor, (text, muted, faint, accent, ink))
        fmt = QtGui.QTextCharFormat()
        fmt.setForeground(self._muted)
        fmt.setFont(ui_font(11, W.Medium))
        self.setHeaderTextFormat(fmt)
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.setWeekdayTextFormat(day, fmt)
        self.updateCells()

    def paintCell(self, painter, rect, day):
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        active = day == self.selectedDate()
        today = day == self.today
        edge = min(30, rect.width() - 4, rect.height() - 3)
        pill = QtCore.QRectF(0, 0, edge, edge)
        pill.moveCenter(QtCore.QRectF(rect).center())
        if active or today:
            painter.setPen(Qt.PenStyle.NoPen if active else QtGui.QPen(self._accent, 1))
            painter.setBrush(self._accent if active else Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(pill, 8, 8)
        subdued = day < self.minimumDate() or day > self.maximumDate() or day.month() != self.monthShown()
        painter.setPen(self._ink if active else self._faint if subdued else self._accent if today else self._text)
        painter.setFont(self._selected_font if active else self._cell_font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(day.day()))
        painter.restore()


class ReminderDateTimePicker(QtWidgets.QWidget):
    """Calendar and minute-precision time field using the computer's local clock."""

    def __init__(self, initial: QtCore.QDateTime, today: QtCore.QDate, *, parent: QtWidgets.QWidget,
                 daily: bool = False):
        super().__init__(parent, objectName="reminderPicker")
        self._today = today
        self._daily = daily
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.calendar = _Calendar(today)
        self.calendar.setSelectedDate(initial.date())
        card = QtWidgets.QFrame(objectName="reminderCalendarCard")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 7)
        card_layout.setSpacing(4)
        nav = QtWidgets.QHBoxLayout()
        nav.setSpacing(6)
        self.month = QtWidgets.QDateEdit(objectName="reminderMonth")
        self.month.setAccessibleName("日历年月")
        self.month.setToolTip("可输入年份或月份，快速跳转")
        self.month.setFrame(False)
        self.month.setDisplayFormat("yyyy 年 MM 月")
        self.month.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.month.setKeyboardTracking(False)
        self.month.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.month.setDateRange(today.addDays(1 - today.day()), self.calendar.maximumDate())
        self.month.editingFinished.connect(self._show_input_month)
        self.prev = self._nav_button("‹", "上个月", self.calendar.showPreviousMonth)
        self.next = self._nav_button("›", "下个月", self.calendar.showNextMonth)
        go_today = self._nav_button("今天", "选择今天", self._select_today)
        nav.addWidget(self.prev)
        nav.addWidget(self.month, 1)
        nav.addWidget(self.next)
        nav.addSpacing(3)
        nav.addWidget(go_today)
        card_layout.addLayout(nav)
        card_layout.addWidget(self.calendar)
        layout.addWidget(card)
        card.setHidden(daily)

        clock_card = QtWidgets.QFrame(objectName="reminderClockCard")
        clock = QtWidgets.QHBoxLayout(clock_card)
        clock.setContentsMargins(12, 8, 10, 8)
        clock.setSpacing(8)
        summary = QtWidgets.QVBoxLayout()
        summary.setSpacing(2)
        summary.addWidget(QtWidgets.QLabel("提醒时间", objectName="reminderClockLabel"))
        self._chosen_date = QtWidgets.QLabel(objectName="reminderMuted")
        summary.addWidget(self._chosen_date)
        clock.addLayout(summary, 1)
        self.time = QtWidgets.QTimeEdit(initial.time(), objectName="reminderClock")
        self.time.setAccessibleName("提醒时间（小时和分钟）")
        self.time.setToolTip("可直接输入小时和分钟，也可用方向键调整")
        self.time.setFrame(False)
        self.time.setDisplayFormat("HH:mm")
        self.time.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 用外层画边框，避免 Qt 给无箭头的日期控件套边框时压缩输入区。
        time_field = QtWidgets.QFrame(objectName="reminderClockField")
        time_field.setFixedWidth(110)
        time_layout = QtWidgets.QHBoxLayout(time_field)
        time_layout.setContentsMargins(4, 1, 4, 1)
        time_layout.addWidget(self.time)
        clock.addWidget(time_field)
        layout.addWidget(clock_card)
        self.setFocusProxy(self.time)
        self.calendar.currentPageChanged.connect(self._update_page)
        self.calendar.selectionChanged.connect(self._update_summary)
        self._update_page(self.calendar.yearShown(), self.calendar.monthShown())
        self._update_summary()

    @staticmethod
    def _nav_button(label, name, action):
        button = QtWidgets.QPushButton(label, objectName="reminderCalNav")
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        if label != "今天":
            button.setFixedWidth(32)
            button.setFont(ui_font(19))
        button.clicked.connect(action)
        return button

    def _select_today(self):
        self.calendar.setSelectedDate(self._today)
        self.calendar.showSelectedDate()

    def _show_input_month(self):
        day = self.month.date()
        self.calendar.setCurrentPage(day.year(), day.month())

    def _update_page(self, year: int, month: int):
        first = QtCore.QDate(year, month, 1)
        self.month.setDate(first)
        self.prev.setEnabled(first > self.month.minimumDate())
        last = self.calendar.maximumDate()
        self.next.setEnabled(first < QtCore.QDate(last.year(), last.month(), 1))

    def _update_summary(self):
        if self._daily:
            self._chosen_date.setText("每天 · 电脑本地时间")
            return
        day = self.calendar.selectedDate()
        locale = self.calendar.locale()
        self._chosen_date.setText(locale.toString(day, "yyyy 年 M 月 d 日 · ddd"))

    def date_time(self) -> QtCore.QDateTime:
        clock = self.time.time()
        return QtCore.QDateTime(self.calendar.selectedDate(), QtCore.QTime(clock.hour(), clock.minute()))
