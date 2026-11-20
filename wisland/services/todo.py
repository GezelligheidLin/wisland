"""当前用户的本地待办；任务状态与提醒状态分别持久化。"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path

from PySide6 import QtCore

TITLE_LIMIT = 120
NOTES_LIMIT = 4000
MINUTES_PER_DAY = 24 * 60


def daily_time_label(minute: int) -> str:
    hour, minute = divmod(minute, 60)
    return f"每天 {hour:02d}:{minute:02d}"


def _minute_of_day(value: datetime) -> int:
    return value.hour * 60 + value.minute


def _first_daily_day(minute: int | None, now: int) -> str | None:
    if minute is None:
        return None
    local = datetime.fromtimestamp(now)
    day = local.date() if minute > _minute_of_day(local) else local.date() + timedelta(days=1)
    return day.isoformat()


@dataclass(frozen=True)
class Todo:
    id: int
    title: str
    notes: str
    due_at: int | None
    remind_at: int | None
    pending: bool
    completed_at: int | None
    daily_minute: int | None = None
    daily_next_day: str | None = None

    def next_reminder_at(self) -> int | None:
        """用于清单排序和分类；实际每日触发按本地日期和时刻判断。"""
        if self.daily_minute is None or self.daily_next_day is None:
            return self.remind_at
        hour, minute = divmod(self.daily_minute, 60)
        daily_at = int(datetime.combine(date.fromisoformat(self.daily_next_day), clock_time(hour, minute)).timestamp())
        return daily_at if self.remind_at is None else min(daily_at, self.remind_at)

    def section(self, now: int) -> str:
        if self.completed_at is not None:
            return "completed"
        if self.pending or (self.due_at is not None and self.due_at <= now):
            return "today"
        today = datetime.fromtimestamp(now).date()
        dates = [datetime.fromtimestamp(t).date() for t in (self.due_at, self.next_reminder_at()) if t is not None]
        if today in dates:
            return "today"
        return "upcoming" if dates else "inbox"


class TodoStore:
    def __init__(self, path: Path):
        self.path = path
        self._initialized = False

    @contextmanager
    def _db(self):
        if not self._initialized:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=0.2)
        db.row_factory = sqlite3.Row
        try:
            if not self._initialized:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, notes TEXT NOT NULL,
                    due_at INTEGER, remind_at INTEGER, pending INTEGER NOT NULL DEFAULT 0, completed_at INTEGER,
                    daily_minute INTEGER, daily_next_day TEXT
                )""")
                columns = {row["name"] for row in db.execute("PRAGMA table_info(todos)")}
                if "daily_minute" not in columns:
                    db.execute("ALTER TABLE todos ADD COLUMN daily_minute INTEGER")
                if "daily_next_day" not in columns:
                    db.execute("ALTER TABLE todos ADD COLUMN daily_next_day TEXT")
                db.execute("""CREATE INDEX IF NOT EXISTS todos_reminders ON todos (pending, remind_at)
                    WHERE completed_at IS NULL AND remind_at IS NOT NULL""")
                db.execute("""CREATE INDEX IF NOT EXISTS todos_daily_reminders ON todos (daily_next_day)
                    WHERE completed_at IS NULL AND daily_minute IS NOT NULL""")
                db.commit()
                self._initialized = True
            with db:
                yield db
        finally:
            db.close()

    def list(self) -> list[Todo]:
        with self._db() as db:
            return [Todo(**row) for row in db.execute("SELECT * FROM todos ORDER BY id DESC")]

    def save(self, title: str, notes: str = "", due_at: int | None = None,
             remind_at: int | None = None, todo_id: int | None = None, *,
             daily_minute: int | None = None) -> int:
        """新增或编辑；每日提醒按午夜起第几分钟保存，规则不变时保留待处理状态。"""
        title = title.strip()
        if not title or len(title) > TITLE_LIMIT or len(notes) > NOTES_LIMIT:
            raise ValueError(f"请输入 1–{TITLE_LIMIT} 个字符的标题，备注最多 {NOTES_LIMIT} 个字符。")
        if daily_minute is not None and (type(daily_minute) is not int or not 0 <= daily_minute < MINUTES_PER_DAY
                                         or remind_at is not None):
            raise ValueError("每日提醒需要一个本地时刻，且不能同时设置一次性提醒。")
        first_day = _first_daily_day(daily_minute, int(time.time()))
        with self._db() as db:
            if todo_id is None:
                return db.execute("""INSERT INTO todos (title, notes, due_at, remind_at, daily_minute, daily_next_day)
                    VALUES (?, ?, ?, ?, ?, ?)""", (title, notes, due_at, remind_at, daily_minute, first_day)).lastrowid
            db.execute("""UPDATE todos SET title=:title, notes=:notes, due_at=:due_at,
                pending=CASE WHEN daily_minute IS :daily AND (:daily IS NOT NULL OR remind_at IS :once)
                    THEN pending ELSE 0 END,
                remind_at=CASE WHEN completed_at IS NOT NULL THEN NULL
                    WHEN :daily IS NOT NULL AND daily_minute IS :daily THEN remind_at ELSE :once END,
                daily_next_day=CASE WHEN completed_at IS NOT NULL THEN NULL
                    WHEN daily_minute IS :daily THEN daily_next_day ELSE :first_day END,
                daily_minute=:daily WHERE id=:id""",
                       {"title": title, "notes": notes, "due_at": due_at, "daily": daily_minute,
                        "once": remind_at, "first_day": first_day, "id": todo_id})
            return todo_id

    def set_completed(self, todo_id: int, completed: bool, now: int):
        with self._db() as db:
            if completed:
                db.execute("""UPDATE todos SET completed_at=?, remind_at=NULL, pending=0,
                    daily_next_day=NULL WHERE id=?""", (now, todo_id))
            else:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT daily_minute FROM todos WHERE id=? AND completed_at IS NOT NULL", (todo_id,)).fetchone()
                if row is not None:
                    db.execute("""UPDATE todos SET completed_at=NULL, remind_at=NULL, pending=0,
                        daily_next_day=? WHERE id=?""", (_first_daily_day(row["daily_minute"], now), todo_id))

    def complete_for_preview(self, todo_id: int, now: int) -> Todo | None:
        """完成并返回同一事务中的原状态，供岛内即时撤销。"""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM todos WHERE id=? AND completed_at IS NULL", (todo_id,)).fetchone()
            if row is None:
                return None
            db.execute("""UPDATE todos SET completed_at=?, remind_at=NULL, pending=0,
                daily_next_day=NULL WHERE id=?""", (now, todo_id))
            return Todo(**row)

    def undo_preview_completion(self, todo: Todo, completed_at: int) -> bool:
        """只撤销这次完成；保留待处理标记，不重复发出已经到点的提醒。"""
        with self._db() as db:
            result = db.execute("""UPDATE todos SET completed_at=NULL, remind_at=?, pending=?, daily_next_day=?
                WHERE id=? AND completed_at=? AND title=? AND notes=? AND due_at IS ? AND daily_minute IS ?""",
                                (todo.remind_at, todo.pending, todo.daily_next_day, todo.id, completed_at,
                                 todo.title, todo.notes, todo.due_at, todo.daily_minute))
            return result.rowcount == 1

    def resolve_reminder(self, todo_id: int, remind_at: int | None = None):
        """处理本次提醒；传入新时间代表稍后重提醒，不修改截止时间。"""
        with self._db() as db:
            db.execute("UPDATE todos SET pending=0, remind_at=? WHERE id=? AND pending=1 AND completed_at IS NULL",
                       (remind_at, todo_id))

    def delete(self, todo_id: int) -> Todo | None:
        """删除并返回最新状态，用于撤销（包括可能刚到期的提醒）。"""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
            if row is not None:
                db.execute("DELETE FROM todos WHERE id=?", (todo_id,))
                return Todo(**row)
            return None

    def undo_delete(self, todo: Todo):
        with self._db() as db:
            db.execute("""INSERT INTO todos (id, title, notes, due_at, remind_at, pending, completed_at,
                daily_minute, daily_next_day) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (todo.id, todo.title, todo.notes, todo.due_at, todo.remind_at, todo.pending,
                        todo.completed_at, todo.daily_minute, todo.daily_next_day))

    def collect_due(self, now: int) -> tuple[int, int]:
        """持久化到期状态；每天最多触发一轮固定提醒，漏掉多天时只补最近一轮。"""
        local = datetime.fromtimestamp(now)
        today = local.date()
        yesterday = (today - timedelta(days=1)).isoformat()
        tomorrow = (today + timedelta(days=1)).isoformat()
        date_text = today.isoformat()
        minute = _minute_of_day(local)
        daily_due = """completed_at IS NULL AND daily_minute IS NOT NULL AND
            (daily_next_day <= ? OR (daily_next_day = ? AND daily_minute <= ?))"""
        once_due = "pending=0 AND completed_at IS NULL AND remind_at IS NOT NULL AND remind_at<=?"
        with self._db() as db:
            new = 0
            # 同一事务先推进每日计划。若稍后提醒也到期，只提示一次，新的每日轮次取代旧的稍后时间。
            daily_args = (yesterday, date_text, minute)
            if db.execute(f"SELECT 1 FROM todos WHERE {daily_due} LIMIT 1", daily_args).fetchone():
                new = db.execute(f"""UPDATE todos SET pending=1, remind_at=NULL,
                    daily_next_day=CASE WHEN daily_minute<=? THEN ? ELSE ? END WHERE {daily_due}""",
                                 (minute, tomorrow, date_text, *daily_args)).rowcount
            if db.execute(f"SELECT 1 FROM todos WHERE {once_due} LIMIT 1", (now,)).fetchone():
                new += db.execute(f"UPDATE todos SET pending=1 WHERE {once_due}", (now,)).rowcount
            pending = db.execute("SELECT COUNT(*) FROM todos WHERE pending=1").fetchone()[0]
            return new, pending


class TodoService(QtCore.QObject):
    changed = QtCore.Signal()
    due = QtCore.Signal(int)

    def __init__(self, store: TodoStore):
        super().__init__()
        self.store = store
        self.pending = 0
        self._poll = QtCore.QTimer(self, interval=2000, timeout=self.scan)

    def start(self):
        self.scan()
        self._poll.start()

    def scan(self, edited: bool = False):
        """启动、唤醒后的下一次扫描及手动修改都走同一套去重规则。"""
        try:
            new, pending = self.store.collect_due(int(time.time()))
        except (OSError, sqlite3.Error):
            return  # 手动操作的错误在待办窗口显示；后台失败不打断灵动岛。
        if new or edited or pending != self.pending:
            self.pending = pending
            self.changed.emit()
        if new:
            self.due.emit(new)
