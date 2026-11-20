"""本地待办闭环、提醒持久化与顶部入口的回归测试。"""
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6 import QtCore, QtTest, QtWidgets

from wisland.services.todo import TodoService, TodoStore
from wisland.ui.nav import NavBar
from wisland.ui.pages.todo import TodoPage
from wisland.ui.theme import G
from wisland.ui.todo import CUSTOM_REMINDER, DAILY_REMINDER, TodoWindow, _Editor, _ReminderDialog


def _pick_date_time(dialog, timestamp):
    dt = QtCore.QDateTime.fromSecsSinceEpoch(timestamp)
    dialog.picker.calendar.setSelectedDate(dt.date())
    dialog.picker.time.setTime(dt.time())


class _TempStore(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "todos.sqlite3"
        self.store = TodoStore(self.path)


class TodoStoreTests(_TempStore):
    def test_missed_alerts_survive_restart_without_duplicate(self):
        self.store.save("例子一", remind_at=100)
        self.store.save("例子二", remind_at=150)
        self.store.save("未来提醒", remind_at=300)
        self.assertEqual(self.store.collect_due(200), (2, 2))
        reopened = TodoStore(self.path)
        self.assertEqual(reopened.collect_due(200), (0, 2))
        self.assertEqual(reopened.collect_due(300), (1, 3))

    def test_snooze_and_ack_leave_deadline_and_task_intact(self):
        key = self.store.save("项目回顾", due_at=400, remind_at=100)
        self.store.collect_due(100)
        self.store.resolve_reminder(key, 160)
        self.assertEqual(self.store.collect_due(159), (0, 0))
        self.assertEqual(self.store.collect_due(160), (1, 1))
        self.store.resolve_reminder(key)
        todo = self.store.list()[0]
        self.assertEqual(todo.due_at, 400)
        self.assertIsNone(todo.completed_at)
        self.assertIsNone(todo.remind_at)
        self.assertEqual(self.store.collect_due(999), (0, 0))

    def test_edit_reminder_complete_and_restore(self):
        key = self.store.save("项目回顾", remind_at=100)
        self.store.collect_due(100)
        self.store.save("写回顾", remind_at=100, todo_id=key)
        self.assertEqual(self.store.collect_due(100), (0, 1))
        self.store.save("写回顾", remind_at=200, todo_id=key)
        self.assertEqual(self.store.collect_due(110), (0, 0))
        self.store.set_completed(key, True, 110)
        self.assertEqual(self.store.list()[0].section(110), "completed")
        self.store.set_completed(key, False, 120)
        self.assertEqual(self.store.list()[0].section(120), "inbox")
        self.assertEqual(self.store.collect_due(210), (0, 0))

    def test_undo_delete_preserves_pending_without_replaying(self):
        key = self.store.save("整理资料", remind_at=100)
        self.store.collect_due(100)
        deleted = self.store.delete(key)
        self.assertEqual(self.store.collect_due(100), (0, 0))
        self.store.undo_delete(deleted)
        self.assertEqual(self.store.list()[0].id, key)
        self.assertEqual(self.store.collect_due(200), (0, 1))

    def test_groups_by_local_day_and_keeps_overdue_in_today(self):
        now = datetime(2026, 11, 20, 12)
        self.store.save("过期", due_at=int((now - timedelta(days=1)).timestamp()))
        self.store.save("今天", remind_at=int((now + timedelta(hours=1)).timestamp()))
        self.store.save("未来", due_at=int((now + timedelta(days=1)).timestamp()))
        self.store.save("暂不安排")
        self.assertEqual({todo.title: todo.section(int(now.timestamp())) for todo in self.store.list()},
                         {"过期": "today", "今天": "today", "未来": "upcoming", "暂不安排": "inbox"})


class DailyReminderStoreTests(_TempStore):
    @staticmethod
    def local(day, hour=9, minute=0):
        return int(datetime(2026, 11, day, hour, minute).timestamp())

    def add_daily(self, now, minute=540, **options):
        with patch("wisland.services.todo.time.time", return_value=now):
            return self.store.save("每日事项", daily_minute=minute, **options)

    def test_daily_fires_once_per_day_even_if_previous_alert_unhandled(self):
        before = self.local(20, 8, 59)
        key = self.add_daily(before, due_at=self.local(26))
        todo = self.store.list()[0]
        self.assertEqual(todo.section(before), "today")
        self.assertEqual(todo.next_reminder_at(), self.local(20))
        self.assertEqual(self.store.collect_due(before), (0, 0))
        self.assertEqual(self.store.collect_due(self.local(20)), (1, 1))
        reopened = TodoStore(self.path)
        self.assertEqual(reopened.collect_due(self.local(20, 10)), (0, 1))
        self.assertEqual(reopened.collect_due(self.local(21)), (1, 1))
        todo = reopened.list()[0]
        self.assertEqual((todo.id, todo.due_at, todo.daily_next_day), (key, self.local(26), "2026-11-22"))

    def test_after_selected_time_starts_tomorrow_and_catchup_is_coalesced(self):
        key = self.add_daily(self.local(20, 10))
        self.assertEqual(self.store.list()[0].daily_next_day, "2026-11-21")
        self.assertEqual(self.store.collect_due(self.local(20, 20)), (0, 0))
        reopened = TodoStore(self.path)
        self.assertEqual(reopened.collect_due(self.local(25, 8)), (1, 1))
        self.assertEqual(reopened.list()[0].next_reminder_at(), self.local(25))
        self.assertEqual(TodoStore(self.path).collect_due(self.local(25, 8, 1)), (0, 1))
        reopened.resolve_reminder(key)
        self.assertEqual(reopened.collect_due(self.local(25)), (1, 1))

    def test_snooze_only_affects_current_occurrence_and_does_not_double_fire(self):
        key = self.add_daily(self.local(20, 8))
        self.store.collect_due(self.local(20))
        later = self.local(20, 9, 10)
        self.store.resolve_reminder(key, later)
        self.assertEqual(self.store.list()[0].next_reminder_at(), later)
        self.assertEqual(self.store.collect_due(later - 1), (0, 0))
        self.assertEqual(self.store.collect_due(later), (1, 1))
        self.store.resolve_reminder(key, self.local(21))
        self.assertEqual(self.store.collect_due(self.local(21)), (1, 1))
        self.assertIsNone(self.store.list()[0].remind_at)
        self.store.resolve_reminder(key)
        self.assertEqual(self.store.collect_due(self.local(22)), (1, 1))

    def test_edit_preserves_same_rule_but_replaces_changed_or_disabled_rules(self):
        key = self.add_daily(self.local(20, 8))
        self.store.collect_due(self.local(20))
        now = self.local(20, 9, 1)
        with patch("wisland.services.todo.time.time", return_value=now):
            self.store.save("修改标题", todo_id=key, daily_minute=540)
            self.assertTrue(self.store.list()[0].pending)
            self.store.save("修改时刻", todo_id=key, daily_minute=600)
            self.assertFalse(self.store.list()[0].pending)
            self.assertEqual(self.store.list()[0].next_reminder_at(), self.local(20, 10))
            self.store.save("改为一次性", todo_id=key, remind_at=self.local(20, 11))
        todo = self.store.list()[0]
        self.assertIsNone(todo.daily_minute)
        self.assertIsNone(todo.daily_next_day)
        self.assertEqual(self.store.collect_due(self.local(20, 10)), (0, 0))
        self.assertEqual(self.store.collect_due(self.local(20, 11)), (1, 1))

    def test_complete_pauses_schedule_undo_preserves_it_and_restore_skips_closed_days(self):
        key = self.add_daily(self.local(20, 8))
        self.store.collect_due(self.local(20))
        now = self.local(20, 9, 2)
        saved = self.store.complete_for_preview(key, now)
        self.assertEqual(self.store.collect_due(now), (0, 0))
        self.assertTrue(self.store.undo_preview_completion(saved, now))
        self.assertEqual(self.store.collect_due(now), (0, 1))
        deleted = self.store.delete(key)
        self.store.undo_delete(deleted)
        self.assertEqual(self.store.list()[0], deleted)
        self.store.set_completed(key, True, now)
        self.assertEqual(self.store.collect_due(self.local(24)), (0, 0))
        self.store.set_completed(key, False, self.local(24, 12))
        self.assertEqual(self.store.list()[0].next_reminder_at(), self.local(25))
        self.assertEqual(self.store.collect_due(self.local(24, 13)), (0, 0))
        self.assertEqual(self.store.collect_due(self.local(25)), (1, 1))

    def test_migrates_legacy_database_without_changing_saved_one_time_reminder(self):
        db = sqlite3.connect(self.path)
        try:
            db.executescript("""CREATE TABLE todos (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                notes TEXT NOT NULL, due_at INTEGER, remind_at INTEGER, pending INTEGER NOT NULL DEFAULT 0,
                completed_at INTEGER);
                INSERT INTO todos (title, notes, due_at, remind_at, pending)
                VALUES ('legacy', 'notes', 500, 100, 1);""")
        finally:
            db.close()
        todo = self.store.list()[0]
        self.assertEqual((todo.title, todo.notes, todo.due_at, todo.remind_at, todo.pending),
                         ("legacy", "notes", 500, 100, True))
        self.assertIsNone(todo.daily_minute)
        self.add_daily(self.local(20, 8))
        self.assertEqual(TodoStore(self.path).collect_due(self.local(20)), (1, 2))


class TodoWindowTests(_TempStore):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        super().setUp()
        self.service = TodoService(self.store)
        self.window = TodoWindow(self.service)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.present(self.app.primaryScreen(), quick=True)
        self.app.processEvents()

    def test_management_window_is_ready_at_first_show_and_reopen(self):
        frames = []

        class ObservedWindow(TodoWindow):
            def showEvent(self, event):
                item = self._tree.topLevelItem(0)
                frames.append((self._heading.text(), self._empty.text(),
                               item.text(0) if item else None, self._stack.currentWidget() == self._tree))
                super().showEvent(event)

        window = ObservedWindow(self.service)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        screen = self.app.primaryScreen()
        window.present(screen)
        self.assertEqual(frames, [("今天", "今天没有需要处理的待办。", None, False)])
        window.close()
        self.store.save("重新打开时显示", due_at=int(time.time()) - 1)
        window.present(screen, quick=True)
        self.assertEqual(frames[-1], ("今天", "今天没有需要处理的待办。", "重新打开时显示", True))
        self.assertIs(window.focusWidget(), window._title)

    def test_quick_entry_complete_and_restore(self):
        self.window._title.setText("写测试")
        QtTest.QTest.keyClick(self.window._title, QtCore.Qt.Key.Key_Return)
        self.app.processEvents()
        tree = self.window._tree
        self.assertEqual(tree.topLevelItem(0).text(0), "写测试")
        self.assertEqual(self.window._title.text(), "")
        tree.topLevelItem(0).setCheckState(0, QtCore.Qt.CheckState.Checked)
        self.app.processEvents()
        self.assertEqual(tree.topLevelItemCount(), 0)
        self.window._nav["completed"].click()
        self.assertEqual(tree.topLevelItem(0).checkState(0), QtCore.Qt.CheckState.Checked)
        tree.topLevelItem(0).setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        self.app.processEvents()
        self.assertIsNone(self.store.list()[0].completed_at)

    def test_pending_actions_and_badge_count(self):
        key = self.store.save("待提醒", remind_at=int(time.time()) - 10)
        due = QtTest.QSignalSpy(self.service.due)
        self.service.scan()
        self.app.processEvents()
        self.assertEqual(due.count(), 1)
        self.assertEqual(self.service.pending, 1)
        tree = self.window._tree
        tree.setCurrentItem(tree.topLevelItem(0))
        self.window._ack.click()
        self.app.processEvents()
        self.assertEqual(self.service.pending, 0)
        self.assertEqual(self.store.list()[0].id, key)
        self.assertIsNone(self.store.list()[0].completed_at)

    def test_custom_reminder_is_picked_and_saved_when_adding(self):
        now = int(time.time())
        chosen = (now + 7200) // 60 * 60
        combo = self.window._quick_remind
        custom = combo.findData(CUSTOM_REMINDER)
        self.window._title.setText("自选提醒时间")
        combo.setCurrentIndex(custom)

        def choose(dialog):
            _pick_date_time(dialog, chosen)
            dialog.accept()
            return dialog.result()

        with patch.object(_ReminderDialog, "exec", choose):
            combo.activated.emit(custom)
        self.assertIn("自定义", combo.currentText())
        self.assertEqual(self.store.list(), [])
        QtTest.QTest.keyClick(self.window._title, QtCore.Qt.Key.Key_Return)
        self.assertEqual(self.store.list()[0].remind_at, chosen)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(combo.itemText(custom), "自定义时间…")

    def test_add_daily_reminder_accepts_midnight_and_updates_list(self):
        combo = self.window._quick_remind
        daily = combo.findData(DAILY_REMINDER)
        combo.setCurrentIndex(daily)

        def choose(dialog):
            self.assertTrue(dialog.picker.calendar.parentWidget().isHidden())
            dialog.picker.time.setTime(QtCore.QTime(0, 0))
            dialog.accept()
            return dialog.result()

        with patch.object(_ReminderDialog, "exec", choose):
            combo.activated.emit(daily)
        self.assertEqual(combo.currentText(), "每天 00:00")
        self.window._title.setText("每日事项")
        self.window._add.click()
        todo = self.store.list()[0]
        self.assertEqual(todo.daily_minute, 0)
        self.assertIsNone(todo.remind_at)
        self.assertEqual(self.window._tree.topLevelItem(0).text(2), "每天 00:00")
        self.assertEqual(combo.currentIndex(), 0)

    def test_edit_can_enable_daily_and_switch_it_off(self):
        key = self.store.save("未排期")
        self.window._select("inbox")
        self.window._tree.setCurrentItem(self.window._tree.topLevelItem(0))

        def set_daily(dialog):
            dialog.reminder_mode.setCurrentIndex(2)
            dialog.daily_at.setTime(QtCore.QTime(14, 30))
            dialog.accept()
            return dialog.result()

        with patch.object(_Editor, "exec", set_daily):
            self.window._edit()
        self.assertEqual(self.store.list()[0].daily_minute, 870)
        self.window._refresh_view(key)

        def clear(dialog):
            self.assertEqual(dialog.daily_at.time(), QtCore.QTime(14, 30))
            dialog.reminder_mode.setCurrentIndex(0)
            dialog.accept()
            return dialog.result()

        with patch.object(_Editor, "exec", clear):
            self.window._edit()
        self.assertIsNone(self.store.list()[0].daily_minute)
        self.assertIsNone(self.store.list()[0].daily_next_day)

    def test_cancel_keeps_preset_and_expired_draft_does_not_create(self):
        combo = self.window._quick_remind
        preset = combo.findData(600)
        combo.setCurrentIndex(preset)
        combo.activated.emit(preset)
        custom = combo.findData(CUSTOM_REMINDER)
        combo.setCurrentIndex(custom)
        self.window._title.setText("保留草稿")
        with patch.object(_ReminderDialog, "exec", return_value=0):
            combo.activated.emit(custom)
        self.assertEqual(combo.currentData(), 600)
        self.assertEqual(self.window._title.text(), "保留草稿")
        chosen = int(time.time()) + 120
        combo.setCurrentIndex(custom)
        with patch.object(self.window, "_pick_reminder", return_value=chosen):
            combo.activated.emit(custom)
        with patch("wisland.ui.todo.time.time", return_value=chosen):
            self.window._add.click()
        self.assertIn("晚于现在", self.window._status.text())
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.window._title.text(), "保留草稿")

    def test_custom_snooze_preserves_deadline_and_realerts_once(self):
        now = int(time.time())
        deadline = now + 1800
        key = self.store.save("稍后处理", due_at=deadline, remind_at=now - 60)
        self.service.scan()
        self.app.processEvents()
        tree = self.window._tree
        tree.setCurrentItem(tree.topLevelItem(0))
        with patch.object(_ReminderDialog, "exec", return_value=0):
            self.window._snooze.click()
        self.assertEqual(self.service.pending, 1)
        chosen = (now + 7200) // 60 * 60

        def choose(dialog):
            self.assertEqual(dialog.mode.currentData(), 600)
            dialog.mode.setCurrentIndex(dialog.mode.findData(CUSTOM_REMINDER))
            _pick_date_time(dialog, chosen)
            dialog.accept()
            return dialog.result()

        with patch.object(_ReminderDialog, "exec", choose):
            self.window._snooze.click()
        todo = self.store.list()[0]
        self.assertEqual((todo.id, todo.remind_at, todo.due_at), (key, chosen, deadline))
        self.assertIsNone(todo.completed_at)
        self.assertEqual(self.service.pending, 0)
        self.assertEqual(TodoStore(self.path).collect_due(chosen), (1, 1))
        self.assertEqual(self.store.collect_due(chosen + 60), (0, 1))

    def test_past_reminder_is_rejected_in_time_picker(self):
        now = int(datetime(2026, 11, 20, 16, 14).timestamp())
        with patch("wisland.ui.todo.time.time", return_value=now):
            dialog = _ReminderDialog(self.window)
            self.addCleanup(dialog.deleteLater)
            _pick_date_time(dialog, now - 180)
            dialog.accept()
        self.assertIsNone(dialog.timestamp)
        self.assertIn("晚于现在", dialog.error.text())
        self.assertEqual(dialog.result(), QtWidgets.QDialog.DialogCode.Rejected)

    def test_inline_calendar_browse_does_not_change_reminder(self):
        now = int(datetime(2026, 11, 20, 16, 14).timestamp())
        with patch("wisland.ui.todo.time.time", return_value=now):
            dialog = _ReminderDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        picker = dialog.picker
        self.assertEqual(picker.calendar.minimumDate(), QtCore.QDate(2026, 11, 20))
        self.assertEqual(picker.calendar.selectedDate(), QtCore.QDate(2026, 11, 20))
        self.assertFalse(picker.prev.isEnabled())
        picker.next.click()
        self.assertEqual(picker.calendar.monthShown(), 12)
        self.assertEqual(picker.calendar.selectedDate(), QtCore.QDate(2026, 11, 20))
        today = next(b for b in picker.findChildren(QtWidgets.QPushButton) if b.accessibleName() == "选择今天")
        today.click()
        self.assertEqual(picker.calendar.monthShown(), 11)
        picker.month.setDate(QtCore.QDate(2027, 3, 1))
        picker.month.editingFinished.emit()
        self.assertEqual((picker.calendar.yearShown(), picker.calendar.monthShown()), (2027, 3))
        self.assertEqual(picker.date_time().time(), QtCore.QTime(16, 24))

    def test_custom_reminder_only_shows_the_dialog_as_a_window(self):
        shown = []

        class WindowShows(QtCore.QObject):
            def eventFilter(self, watched, event):
                if (event.type() == QtCore.QEvent.Type.Show and isinstance(watched, QtWidgets.QWidget)
                        and watched.isWindow() and watched.objectName() in ("reminderDialog", "reminderPicker")):
                    shown.append(watched.objectName())
                return False

        monitor = WindowShows()
        self.app.installEventFilter(monitor)
        try:
            dialog = _ReminderDialog(self.window)
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(dialog.close)
            self.assertEqual(shown, [])
            dialog.show()
            self.app.processEvents()
            self.assertEqual(shown, ["reminderDialog"])
            self.assertTrue(dialog.picker.isVisible())
            self.assertFalse(dialog.picker.isWindow())
        finally:
            self.app.removeEventFilter(monitor)

    def test_reminder_inputs_remain_visible_under_both_color_schemes(self):
        dialog = _ReminderDialog(self.window, snooze=True)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        dialog.show()
        self.assertTrue(dialog.picker.isHidden())
        dialog.mode.setCurrentIndex(dialog.mode.findData(CUSTOM_REMINDER))
        self.assertTrue(dialog.picker.isVisible())
        for scheme in (QtCore.Qt.ColorScheme.Light, QtCore.Qt.ColorScheme.Dark):
            dialog._apply_style(scheme)
            self.app.processEvents()
            for field in (dialog.picker.month, dialog.picker.time):
                editor = field.findChild(QtWidgets.QLineEdit)
                self.assertGreaterEqual(editor.width(), field.fontMetrics().horizontalAdvance(field.text()))

    def test_present_task_selects_its_category_and_edits(self):
        key = self.store.save("未排期例子")
        with patch.object(self.window, "_edit") as edit:
            self.window.present(self.app.primaryScreen(), todo_id=key)
        self.assertEqual(self.window._section, "inbox")
        self.assertEqual(self.window._selected().id, key)
        edit.assert_called_once_with()


class TodoPageTests(_TempStore):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        super().setUp()
        self.service = TodoService(self.store)
        self.opened = []
        self.page = TodoPage(self.service, lambda **options: self.opened.append(options))
        self.page.resize(580, self.page.height)
        self.page.show()
        self.page.on_show()
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.on_hide)
        self.addCleanup(self.page.close)

    def titles(self):
        rows = [self.page._rows.itemAt(i).widget() for i in range(self.page._rows.count())]
        buttons = [row.findChild(QtWidgets.QPushButton, "todoTitle") for row in rows if row is not None]
        return [button for button in buttons if button is not None]

    def test_preview_priorities_limit_and_open_intent(self):
        noon = datetime(2026, 11, 20, 12)
        now = int(noon.timestamp())
        self.store.save("未排期")
        self.store.save("未来", due_at=int((noon + timedelta(days=1)).timestamp()))
        self.store.save("今天", due_at=now + 3600)
        self.store.save("逾期", due_at=now - 3600)
        key = self.store.save("待提醒", remind_at=now - 10)
        self.store.collect_due(now)
        done = self.store.save("完成")
        self.store.set_completed(done, True, now)
        with patch("wisland.ui.pages.todo.time.time", return_value=now):
            self.page.refresh()
        self.app.processEvents()
        self.assertEqual([button.text() for button in self.titles()], ["待提醒", "逾期", "今天", "未来"])
        self.titles()[0].click()
        self.page.findChild(QtWidgets.QPushButton, "todoAdd").click()
        all_button = next(b for b in self.page.findChildren(QtWidgets.QPushButton) if b.text() == "查看全部 →")
        all_button.click()
        self.assertEqual(self.opened, [{"todo_id": key}, {"quick": True}, {}])

    def test_complete_and_undo_keep_pending_without_second_popup(self):
        key = self.store.save("待提醒", remind_at=int(time.time()) - 60)
        due = QtTest.QSignalSpy(self.service.due)
        self.service.scan()
        self.assertEqual(self.service.pending, 1)
        row = self.page._rows.itemAt(0).widget()
        row.findChild(QtWidgets.QPushButton, "todoCheck").click()
        self.assertIsNotNone(self.store.list()[0].completed_at)
        self.assertFalse(self.titles())
        self.assertEqual(self.service.pending, 0)
        self.page._undo_btn.click()
        todo = self.store.list()[0]
        self.assertEqual(todo.id, key)
        self.assertIsNone(todo.completed_at)
        self.assertTrue(todo.pending)
        self.assertEqual(self.service.pending, 1)
        self.assertEqual(due.count(), 1)


class TodoNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_equal_slots_never_shift_or_overlap_settings(self):
        items = [(str(i), G.HOME, label) for i, label in enumerate(
            ("主页", "消息", "股票", "天气", "控制", "剪贴板", "翻译", "待办"))]
        nav = NavBar(items)
        self.addCleanup(nav.deleteLater)
        settings = nav.add_right_button(G.GEAR, "设置", lambda: None)
        for visible in (items, items[::2], items[:1]):
            nav.set_items(visible)
            for width in (420, 586):
                nav.resize(width, 36)
                nav._place_right()
                rects = nav._rects()
                for i, (key, _, _) in enumerate(visible):
                    nav.set_current(key)
                    self.assertEqual(nav._rects(), rects)
                    self.assertEqual(nav._hit(rects[i].center()), i)
                    self.assertAlmostEqual(rects[i].width(), rects[0].width())
                    if i > 1:
                        self.assertAlmostEqual(rects[i].x() - rects[i-1].x(), rects[1].x() - rects[0].x())
                self.assertGreaterEqual(settings.x() - rects[-1].right(), 12)
                self.assertEqual(nav._hit(settings.geometry().center()), -1)
        nav.set_items(items)
        nav.resize(420, 36)
        nav._place_right()
        nav.set_current("5")
        self.assertFalse(nav._shows_label(5, nav._rects()[5].width()))
        nav.set_items(items[4:])
        self.assertTrue(nav._shows_label(1, nav._rects()[1].width()))

    def test_old_visible_tabs_gain_todo_without_resurrecting_hidden_tabs(self):
        from wisland.core.config import Config
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"island": {"visible_tabs": ["clip", "home"]}}), encoding="utf-8")
            with patch("wisland.core.config.CONFIG_PATH", path):
                settings = Config()
                self.assertEqual(settings.get("island.visible_tabs"), ["clip", "home", "todo"])
                settings.set("island.visible_tabs", ["clip", "home"])
                self.assertEqual(Config().get("island.visible_tabs"), ["clip", "home"])
