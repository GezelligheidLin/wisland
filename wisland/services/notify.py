# -*- coding: utf-8 -*-
"""消息提醒：QQ / 微信 / 钉钉 / 邮件。

三个来源：
  A. 轮询 Windows 通知数据库 wpndatabase.db（只读），新出现的 toast 按 PrimaryId 前缀映射。
  B. UI 收到 HSHELL_FLASH 时调用 on_flash(hwnd)，按进程名映射（8 秒内同类去重）。
  C. 邮件兜底：每 20 秒检查 Thunderbird 各账户 INBOX mbox 文件是否变大。

QQ / 微信 / 钉钉展示系统通知提供的发送者与摘要，仅保留在内存中；邮件不展示发件人或正文。
"""
from __future__ import annotations

import configparser
import ctypes
import os
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from ctypes import wintypes
from pathlib import Path

from PySide6 import QtCore

from wisland.core.bg import launch, run_bg, ui
from wisland.core.config import cfg, expand

WPN_DB = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db")
POLL_MS = 1500
MAIL_FILE_MS = 20_000
FLASH_DEDUPE_S = 8.0
MAIL_TOAST_GRACE_S = 60.0
HISTORY_MAX = 50

_FILETIME_EPOCH_DIFF = 116444736000000000  # 1601 -> 1970，单位 100ns


def filetime_to_epoch(ft) -> float:
    try:
        ft = int(ft)
    except (TypeError, ValueError):
        return time.time()
    if ft <= 0:
        return time.time()
    return (ft - _FILETIME_EPOCH_DIFF) / 1e7


def toast_texts(payload) -> list[str]:
    """提取通知 XML 的非空文本，解码转义并合并换行；格式无效时返回空列表。"""
    try:
        root = ET.fromstring(payload or "")
        texts = []
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1].lower() == "text":
                text = " ".join("".join(node.itertext()).split())
                if text:
                    texts.append(text[:240])
        return texts
    except (ET.ParseError, TypeError, ValueError):
        return []


def toast_first_text(payload) -> str:
    """返回通知首行（最多 40 字），用于不暴露正文的诊断统计。"""
    texts = toast_texts(payload)
    return texts[0][:40] if texts else ""


def notification_summary(notification: dict) -> str:
    """生成提醒和历史共用的摘要；缺少正文时回退为发送者或通用提示。"""
    if notification.get("kind") == "mail":
        return "新邮件"
    sender = notification.get("sender")
    content = notification.get("content")
    if content:
        return f"{sender}：{content}" if sender else content
    return f"{sender} 发来消息" if sender else "新消息"


# ---------------- hwnd -> exe ----------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetForegroundWindow.restype = wintypes.HWND
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                  ctypes.POINTER(wintypes.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def exe_of_hwnd(hwnd: int) -> str:
    """窗口句柄 -> 小写 exe 文件名（失败返回空串）。"""
    try:
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
        if not pid.value:
            return ""
        h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if h:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return os.path.basename(buf.value).lower()
            finally:
                _kernel32.CloseHandle(h)
        try:
            import psutil
            return psutil.Process(pid.value).name().lower()
        except Exception:  # noqa: BLE001
            return ""
    except Exception:  # noqa: BLE001
        return ""


# ---------------- Thunderbird ----------------

def thunderbird_profile() -> Path | None:
    root = Path(os.path.expandvars(r"%APPDATA%\Thunderbird"))
    ini = root / "profiles.ini"
    if not ini.exists():
        return None
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(ini, encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None
    cand: list[tuple[str, bool]] = []
    for sec in cp.sections():
        if sec.lower().startswith("install") and cp[sec].get("default"):
            cand.append((cp[sec]["default"], True))
    for sec in cp.sections():
        s = cp[sec]
        if sec.lower().startswith("profile") and s.get("path") and s.get("default") == "1":
            cand.append((s["path"], s.get("isrelative", "1") == "1"))
    for sec in cp.sections():
        s = cp[sec]
        if sec.lower().startswith("profile") and s.get("path"):
            cand.append((s["path"], s.get("isrelative", "1") == "1"))
    for path, rel in cand:
        p = (root / path) if rel else Path(path)
        if (p / "ImapMail").is_dir() or (p / "Mail").is_dir():
            return p
    return None


def inbox_files(profile: Path) -> list[Path]:
    out = []
    for sub in ("ImapMail", "Mail"):
        base = profile / sub
        if not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                if f in ("INBOX", "Inbox"):
                    out.append(Path(dirpath) / f)
    return out


# ---------------- service ----------------

class NotifyService(QtCore.QObject):
    notified = QtCore.Signal(dict)        # {kind, name, source:'toast'|'flash'|'file', time, [sender], [content]}
    counts_changed = QtCore.Signal(dict)  # {kind: n}
    open_failed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        ui(lambda: None)  # 让 bg 的 invoker 在主线程创建（否则 on_done 回调发不回来）
        self.counts: dict[str, int] = {}
        self.history: list[dict] = []
        self._last_by_kind: dict[str, float] = {}
        self._last_mail_toast = 0.0
        self._max_id: int | None = None
        self._busy = False
        self._mail_busy = False
        self._lock = threading.Lock()
        self._inbox_state: dict[str, tuple[int, float]] | None = None
        self._profile: Path | None = None
        self._running = False
        self._foreground_kind = ""
        self._foreground_since = 0.0
        self._read_at: dict[str, float] = {}
        self._foreground_timer = QtCore.QTimer(self, interval=500, timeout=self._sync_foreground)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._tick)
        self._mail_timer = QtCore.QTimer(self)
        self._mail_timer.setInterval(MAIL_FILE_MS)
        self._mail_timer.timeout.connect(self._mail_tick)

    # ---------- lifecycle ----------
    def start(self):
        self._running = True
        self._timer.start()
        self._foreground_timer.start()
        self._mail_timer.start()
        self._tick()
        self._mail_tick()

    def stop(self):
        self._running = False
        self._timer.stop()
        self._foreground_timer.stop()
        self._mail_timer.stop()

    # ---------- public ----------
    def clear(self, kind: str):
        """清除该应用的计数和提醒记录，同时忽略延迟到达的旧通知。"""
        self._read_at[kind] = time.time()
        changed = bool(self.counts.get(kind)) or any(d.get("kind") == kind for d in self.history)
        self.counts[kind] = 0
        self.history[:] = [d for d in self.history if d.get("kind") != kind]
        if changed:
            self.counts_changed.emit(dict(self.counts))

    def open(self, kind: str) -> bool:
        """打开或切换应用；成功后清除提醒，失败时保留并给出提示。"""
        processes = cfg.get("notify.flash_processes") or {}
        names = [exe for exe, mapping in processes.items() if mapping.get("kind") == kind]
        path = expand(cfg.get(f"notify.open.{kind}") or "")
        if not path or not os.path.isfile(path):
            import psutil
            for process in psutil.process_iter(["name"]):
                try:
                    if (process.info["name"] or "").lower() in names:
                        path = process.exe()
                        break
                except (psutil.Error, OSError):
                    continue
        path = path or next(iter(names), "")
        try:
            opener = getattr(self, "opener", None)
            result = opener(path) if opener and path else launch(path) if path else False
            success = result is True or result in ("focused", "launched")
        except Exception:
            success = False
        if success:
            self.clear(kind)
        else:
            name = next((m.get("name", kind) for m in processes.values() if m.get("kind") == kind), kind)
            self.open_failed.emit(f"无法打开{name}，请检查应用的显示窗口快捷键或 notify.open.{kind} 路径")
        return success

    def _sync_foreground(self):
        """将目标软件在前台持续停留一秒视为已查看，清除其本地提醒。"""
        exe = exe_of_hwnd(_user32.GetForegroundWindow())
        mapping = (cfg.get("notify.flash_processes") or {}).get(exe) or {}
        kind = mapping.get("kind", "")
        now = time.monotonic()
        if kind != self._foreground_kind:
            self._foreground_kind = kind
            self._foreground_since = now
        if kind and now - self._foreground_since >= 1.0:
            self.clear(kind)

    def on_flash(self, hwnd: int):
        try:
            exe = exe_of_hwnd(int(hwnd))
            if not exe:
                return
            m = (cfg.get("notify.flash_processes") or {}).get(exe)
            if not m:
                return
            kind = m.get("kind", exe)
            now = time.time()
            if now - self._last_by_kind.get(kind, 0) < FLASH_DEDUPE_S:
                return
            self._emit({"kind": kind, "name": m.get("name", kind), "source": "flash", "time": now})
        except Exception:  # noqa: BLE001
            pass

    # ---------- internal ----------
    def _emit(self, d: dict):
        kind = d["kind"]
        if d.get("source") == "toast" and d.get("time", time.time()) <= self._read_at.get(kind, 0):
            return
        self._last_by_kind[kind] = time.time()
        if kind == "mail":
            d.pop("sender", None)
            if d.get("source") == "toast":
                self._last_mail_toast = time.time()
        self.history.insert(0, d)
        del self.history[HISTORY_MAX:]
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.notified.emit(d)
        self.counts_changed.emit(dict(self.counts))

    @staticmethod
    def _map_handler(primary: str, apps: dict):
        # 前缀后必须是结尾或非字母数字（'ThunderbirdToast-990…' 命中，'QQExtension_…' 不命中）
        for prefix, m in apps.items():
            if primary and primary.startswith(prefix):
                rest = primary[len(prefix):]
                if not rest or not rest[0].isalnum():
                    return m
        return None

    def _tick(self):
        if self._busy or not self._running:
            return
        self._busy = True
        run_bg(self._poll_db, on_done=self._on_rows, on_error=lambda e: setattr(self, "_busy", False))

    def _query(self, sql: str, args=()):
        uri = "file:" + WPN_DB.replace("\\", "/") + "?mode=ro"
        last = None
        for _ in range(4):
            try:
                con = sqlite3.connect(uri, uri=True, timeout=1.0)
                try:
                    return con.execute(sql, args).fetchall()
                finally:
                    con.close()
            except sqlite3.OperationalError as e:
                last = e
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    time.sleep(0.15)
                    continue
                raise
        raise last  # type: ignore[misc]

    def _poll_db(self):
        if not os.path.exists(WPN_DB):
            return []
        if self._max_id is None:
            r = self._query("SELECT MAX(Id) FROM Notification")
            self._max_id = int(r[0][0] or 0) if r else 0
            return []
        rows = self._query(
            "SELECT n.Id, h.PrimaryId, n.ArrivalTime, n.Type, n.Payload FROM Notification n "
            "JOIN NotificationHandler h ON h.RecordId = n.HandlerId WHERE n.Id > ? ORDER BY n.Id",
            (self._max_id,))
        if rows:
            self._max_id = max(int(r[0]) for r in rows)
        return rows

    def _on_rows(self, rows):
        self._busy = False
        if not rows:
            return
        apps = cfg.get("notify.toast_apps") or {}
        for _id, primary, arrival, ntype, payload in rows:
            try:
                if str(ntype or "").lower() not in ("toast", ""):
                    continue
                m = self._map_handler(str(primary or ""), apps)
                if not m:
                    continue
                kind = m.get("kind", "other")
                d = {"kind": kind, "name": m.get("name", kind), "source": "toast",
                     "time": filetime_to_epoch(arrival)}
                if kind in ("qq", "wechat", "dingtalk"):
                    texts = toast_texts(payload)
                    if len(texts) > 1:
                        d["sender"] = texts[0][:40]
                        d["content"] = " ".join(texts[1:])[:240]
                    elif texts:
                        d["content"] = texts[0]
                self._emit(d)
            except Exception:  # noqa: BLE001
                continue

    # ---------- recent (debug / test) ----------
    def recent_mapped(self, n: int = 3) -> list[dict]:
        """数据库里最近 n 条已映射通知（阻塞，供测试；不含正文）。"""
        apps = cfg.get("notify.toast_apps") or {}
        rows = self._query(
            "SELECT n.Id, h.PrimaryId, n.ArrivalTime, n.Type, n.Payload FROM Notification n "
            "JOIN NotificationHandler h ON h.RecordId = n.HandlerId ORDER BY n.Id DESC LIMIT 500")
        out = []
        for _id, primary, arrival, ntype, payload in rows:
            m = self._map_handler(str(primary or ""), apps)
            if m and str(ntype or "").lower() == "toast":
                d = {"id": _id, "kind": m.get("kind"), "time": filetime_to_epoch(arrival),
                     "sender_len": len(toast_first_text(payload))}
                out.append(d)
                if len(out) >= n:
                    break
        return out

    # ---------- mail file fallback ----------
    def _mail_tick(self):
        if self._mail_busy or not self._running:
            return
        self._mail_busy = True
        run_bg(self._scan_inbox, on_done=self._on_inbox, on_error=lambda e: setattr(self, "_mail_busy", False))

    def _scan_inbox(self):
        if self._profile is None:
            self._profile = thunderbird_profile()
        if self._profile is None:
            return None
        st = {}
        for p in inbox_files(self._profile):
            try:
                s = p.stat()
                st[str(p)] = (s.st_size, s.st_mtime)
            except OSError:
                continue
        return st

    def _on_inbox(self, st):
        self._mail_busy = False
        if st is None:
            return
        prev = self._inbox_state
        self._inbox_state = st
        if prev is None:
            return
        grew = any(path in prev and size > prev[path][0] for path, (size, _m) in st.items())
        if grew and time.time() - self._last_mail_toast > MAIL_TOAST_GRACE_S:
            m = (cfg.get("notify.toast_apps") or {}).get("ThunderbirdToast") or {}
            self._emit({"kind": "mail", "name": m.get("name", "邮件"), "source": "file", "time": time.time()})
