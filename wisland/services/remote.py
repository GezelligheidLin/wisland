# -*- coding: utf-8 -*-
"""远程模式：判断本机是否正被远程控制，供灵动岛暂停毛玻璃实时取背景。

灵动岛每次取背景都要用 SetWindowDisplayAffinity 把自己临时排除约 17ms。本机屏幕不受影响，
但 ToDesk / 向日葵这类软件是持续复制桌面再编码发送的，于是远端画面里灵动岛会周期性消失一下。
远程模式期间不再取背景（保留最后一张纹理），闪烁随之消失。

自动判定用两个信号：
- RDP 会话：GetSystemMetrics(SM_REMOTESESSION)。
- 第三方远控：名单内的远控进程在运行，且最近出现过“注入式”鼠标输入（低级鼠标钩子的
  LLMHF_INJECTED）。远控的鼠标键盘是 SendInput 注入的，本地物理鼠标不带这个标记；
  本地用户重新接管鼠标后很快退出远程模式。

远控软件常驻托盘，只看进程在不在会把平时也误判成远程，所以进程名单只作为注入输入的佐证。
名单外的远控软件、或用了驱动级注入的场景检测不到，可以把远程模式设为“始终开启”。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes as W

from PySide6 import QtCore

from ..core.config import cfg

SM_REMOTESESSION = 0x1000
WH_MOUSE_LL = 14
LLMHF_INJECTED = 0x01
LLMHF_LOWER_IL_INJECTED = 0x02

MODES = ("auto", "on", "off")

# 常见远控 / 远程协助 / VNC 服务端进程名（小写）。
REMOTE_PROCESSES = frozenset({
    "todesk.exe", "todesk_service.exe", "todesk_lite.exe",
    "sunloginclient.exe", "sunloginremote.exe",
    "anydesk.exe", "rustdesk.exe", "teamviewer.exe",
    "parsecd.exe", "quickassist.exe", "remoting_desktop.exe",
    "winvnc.exe", "tvnserver.exe", "vncserver.exe",
})

INJECT_HOLD = 180.0      # 远端只看不操作时，远程模式最多保持这么久（秒）
LOCAL_TAKEOVER = 3.0     # 本地物理输入独占多久后退出远程模式（秒）
PROC_CACHE = 5.0         # 进程名单复查间隔（秒），避免频繁枚举


def decide(mode: str, *, rdp: bool, remote_proc: bool,
           last_injected: float, last_local: float, now: float) -> bool:
    """纯判定：mode 为 auto / on / off；时间戳用 time.monotonic()，未知时为 -inf。"""
    if mode == "on":
        return True
    if mode != "auto":
        return False
    if rdp:
        return True
    if not remote_proc or now - last_injected > INJECT_HOLD:
        return False
    # 本地用户重新动物理鼠标，且远端已经安静了一会儿 → 认为回到了本地使用。
    return not (last_local > last_injected and now - last_injected >= LOCAL_TAKEOVER)


def is_rdp_session() -> bool:
    try:
        return bool(ctypes.windll.user32.GetSystemMetrics(SM_REMOTESESSION))
    except Exception:  # noqa: BLE001
        return False


def remote_process_running() -> bool:
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() in REMOTE_PROCESSES:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


class RemoteWatch(QtCore.QObject):
    """轮询 RDP / 进程名单，鼠标钩子只记录时间戳；状态变化时发 changed。"""

    changed = QtCore.Signal(bool)
    POLL_MS = 1500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._last_injected = float("-inf")
        self._last_local = float("-inf")
        self._proc_at = float("-inf")
        self._proc = False
        self._hook = None
        self._hookproc = None
        self._u = None
        self._timer = QtCore.QTimer(self, interval=self.POLL_MS, timeout=self._poll)

    @property
    def active(self) -> bool:
        return self._active

    def mode(self) -> str:
        value = cfg.get("island.remote_mode", "auto")
        return value if value in MODES else "auto"

    def set_mode(self, mode: str):
        cfg.set("island.remote_mode", mode if mode in MODES else "auto")
        self.apply_mode()

    def apply_mode(self):
        """按配置安装 / 卸载鼠标钩子并立即重新判定；配置重载时也调用。"""
        if self.mode() == "auto":
            self._install_hook()
        else:
            self._remove_hook()
        self._poll()

    def start(self):
        self.apply_mode()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self._remove_hook()

    # ---------------------------------------------------------------- 判定
    def _poll(self):
        mode = self.mode()
        now = time.monotonic()
        rdp = False
        if mode == "auto":
            rdp = is_rdp_session()
            # 只有最近出现过注入输入时才需要查进程名单，平时零开销。
            if now - self._last_injected <= INJECT_HOLD:
                if now - self._proc_at >= PROC_CACHE:
                    self._proc_at = now
                    self._proc = remote_process_running()
            else:
                self._proc = False
        active = decide(mode, rdp=rdp, remote_proc=self._proc,
                        last_injected=self._last_injected, last_local=self._last_local, now=now)
        if active != self._active:
            self._active = active
            self.changed.emit(active)

    # ---------------------------------------------------------------- 鼠标钩子
    def _install_hook(self):
        if self._hook:
            return
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD),
                        ("time", W.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, W.WPARAM, W.LPARAM)
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, W.WPARAM, W.LPARAM]
        u.CallNextHookEx.restype = LRESULT
        u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        k.GetModuleHandleW.restype = W.HMODULE
        watch = self

        def proc(code, wparam, lparam):
            # 回调里只读标志位、记时间戳，绝不做耗时操作，以免拖慢全系统鼠标。
            if code == 0 and lparam:
                flags = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents.flags
                now = time.monotonic()
                if flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED):
                    watch._last_injected = now
                else:
                    watch._last_local = now
            return u.CallNextHookEx(watch._hook, code, wparam, lparam)

        self._u = u
        self._hookproc = HOOKPROC(proc)
        self._hook = u.SetWindowsHookExW(WH_MOUSE_LL, self._hookproc, k.GetModuleHandleW(None), 0)

    def _remove_hook(self):
        if self._hook and self._u:
            try:
                self._u.UnhookWindowsHookEx(self._hook)
            except Exception:  # noqa: BLE001
                pass
        self._hook = None
        self._hookproc = None