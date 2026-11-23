"""远程模式判定与毛玻璃暂停的回归测试；不安装系统钩子、不截取真实桌面。"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtWidgets

from wisland.core.config import Config
from wisland.services.remote import INJECT_HOLD, LOCAL_TAKEOVER, RemoteWatch, decide
from wisland.ui.glass import Backdrop

NEG_INF = float("-inf")


def d(mode="auto", rdp=False, proc=False, inj=NEG_INF, local=NEG_INF, now=1000.0):
    return decide(mode, rdp=rdp, remote_proc=proc, last_injected=inj, last_local=local, now=now)


class DecideTests(unittest.TestCase):
    def test_manual_modes_override_signals(self):
        self.assertTrue(d("on"))
        self.assertFalse(d("off", rdp=True, proc=True, inj=999.0))

    def test_rdp_session_needs_no_other_signal(self):
        self.assertTrue(d(rdp=True))

    def test_auto_needs_remote_process_and_injected_input(self):
        self.assertFalse(d())
        self.assertFalse(d(proc=True))
        self.assertFalse(d(inj=999.0))
        self.assertTrue(d(proc=True, inj=999.0))

    def test_injected_input_expires_after_hold(self):
        self.assertTrue(d(proc=True, inj=1000.0 - INJECT_HOLD))
        self.assertFalse(d(proc=True, inj=1000.0 - INJECT_HOLD - 0.1))

    def test_local_mouse_takeover_exits_remote_mode(self):
        # 远端一秒前还在注入，本地鼠标还不足以判定接管。
        self.assertTrue(d(proc=True, inj=999.0, local=999.5))
        self.assertTrue(d(proc=True, inj=1000.0 - LOCAL_TAKEOVER + 0.1, local=999.9))
        # 本地物理输入独占超过阈值 → 回到本地使用。
        self.assertFalse(d(proc=True, inj=990.0, local=999.0))
        # 本地输入比注入输入旧，说明仍是远端在操作。
        self.assertTrue(d(proc=True, inj=999.0, local=990.0))


class WatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = patch("wisland.core.config.CONFIG_PATH", Path(folder.name) / "config.json")
        self.addCleanup(patcher.stop)
        patcher.start()
        self.config = Config()
        patcher = patch("wisland.services.remote.cfg", self.config)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.watch = RemoteWatch()
        self.addCleanup(self.watch.stop)
        self.events = []
        self.watch.changed.connect(self.events.append)

    def test_unknown_mode_falls_back_to_auto(self):
        self.config.set("island.remote_mode", "随便写的")
        self.assertEqual(self.watch.mode(), "auto")

    def test_manual_mode_emits_once_and_skips_hook(self):
        with patch.object(self.watch, "_install_hook") as install:
            self.watch.set_mode("on")
            self.watch.set_mode("on")
            install.assert_not_called()
        self.assertTrue(self.watch.active)
        self.assertEqual(self.events, [True])
        self.watch.set_mode("off")
        self.assertFalse(self.watch.active)
        self.assertEqual(self.events, [True, False])

    def test_auto_installs_hook_and_follows_rdp(self):
        with patch.object(self.watch, "_install_hook") as install, \
                patch("wisland.services.remote.is_rdp_session", return_value=True):
            self.watch.set_mode("auto")
            install.assert_called_once_with()
            self.assertTrue(self.watch.active)
        with patch("wisland.services.remote.is_rdp_session", return_value=False):
            self.watch._poll()
        self.assertFalse(self.watch.active)
        self.assertEqual(self.events, [True, False])

    def test_auto_combines_process_list_and_injected_input(self):
        now = time.monotonic()
        with patch.object(self.watch, "_install_hook"), \
                patch("wisland.services.remote.is_rdp_session", return_value=False), \
                patch("wisland.services.remote.remote_process_running", return_value=True) as procs:
            self.watch.set_mode("auto")
            self.assertFalse(self.watch.active)
            procs.assert_not_called()          # 没有注入输入时不枚举进程
            self.watch._last_injected = now - 10
            self.watch._poll()
            self.assertTrue(self.watch.active)
            self.watch._last_local = now - 1
            self.watch._poll()
            self.assertFalse(self.watch.active)
        self.assertEqual(self.events, [True, False])


class BackdropPauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_retarget_keeps_texture_and_luminance(self):
        backdrop = Backdrop()
        image = QtGui.QImage(8, 4, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtGui.QColor(30, 60, 90))
        backdrop.state = (image, QtCore.QRect(0, 0, 120, 60), 0.4)
        self.assertFalse(backdrop.retarget(QtCore.QRect(0, 0, 120, 60)))
        self.assertTrue(backdrop.retarget(QtCore.QRect(16, 0, 240, 120)))
        tiny, rect, lum = backdrop.state
        self.assertIs(tiny, image)
        self.assertEqual(rect, QtCore.QRect(16, 0, 240, 120))
        self.assertEqual(lum, 0.4)
        self.assertTrue(backdrop.covers(QtCore.QRect(20, 10, 100, 50)))
        self.assertFalse(Backdrop().retarget(QtCore.QRect(0, 0, 10, 10)))

    def test_adopt_freezes_blend_then_retarget_follows(self):
        backdrop = Backdrop()
        image = QtGui.QImage(8, 4, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtGui.QColor(10, 20, 30))
        source = (image, QtCore.QRect(0, 0, 120, 60), 0.2)
        target = (image, QtCore.QRect(0, 0, 240, 120), 0.8)
        frozen = Backdrop.blend_states(source, target, 0.5)
        backdrop.set_paused(True)
        backdrop.adopt(frozen)
        self.assertIs(backdrop.state, frozen)
        self.assertTrue(backdrop.retarget(QtCore.QRect(16, 16, 240, 120)))
        self.assertIs(backdrop.tiny, frozen[0])
        self.assertEqual(backdrop.rect, QtCore.QRect(16, 16, 240, 120))

    def test_paused_worker_stops_capturing_and_resumes(self):
        backdrop = Backdrop()
        calls = []
        backdrop._capture = lambda *args: calls.append(time.monotonic())
        screen = QtWidgets.QApplication.primaryScreen()
        rect = QtCore.QRect(0, 0, 240, 120)
        backdrop.request(screen, rect, 0.02, sample_rect=rect)
        backdrop.start()
        self.addCleanup(backdrop.stop)
        try:
            self.assertTrue(self.wait_for(lambda: len(calls) >= 2), "暂停前应当持续取背景")
            backdrop.set_paused(True)
            self.assertTrue(backdrop.paused)
            time.sleep(0.15)               # 让可能已在途的那一次取背景结束
            marked = len(calls)
            time.sleep(0.3)
            self.assertEqual(len(calls), marked, "暂停期间不能再取背景（否则会临时排除自己）")
            backdrop.set_paused(False)
            self.assertFalse(backdrop.paused)
            self.assertTrue(self.wait_for(lambda: len(calls) > marked), "恢复后应立即补一张")
        finally:
            backdrop.stop()
            backdrop._thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
