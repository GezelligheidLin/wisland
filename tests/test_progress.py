"""播放进度条：点击 / 拖拽后平滑滑动，播放器回报旧位置时不闪回。"""
import time
import unittest

from PySide6 import QtCore, QtWidgets, QtTest

from wisland.ui.pages.home import Progress

Qt = QtCore.Qt


class TestProgress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make(self) -> Progress:
        pg = Progress()
        pg.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen)
        pg.resize(400, 22)
        pg.show()
        return pg

    def state(self, pos, dur=100.0, playing=True):
        return {"has_session": True, "position": float(pos), "duration": float(dur),
                "playing": playing, "stamp": time.monotonic()}

    def test_click_seek_glides_and_keeps_pending(self):
        pg = self.make()
        got = []
        pg.seek.connect(got.append)
        pg.set_state(self.state(10))
        self.assertAlmostEqual(pg.fill.x, 0.1, places=2)          # 初次直接落在实际进度

        QtTest.QTest.mouseClick(pg, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                QtCore.QPoint(300, 11))
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0], 75.0, delta=2.0)
        self.assertLess(pg.fill.x, 0.74)                          # 不是瞬间跳过去
        self.assertAlmostEqual(pg.current(), 75.0, delta=2.0)

        # 播放器第一次回报还是旧位置：进度条留在目标处
        pg.set_state(self.state(12))
        self.assertAlmostEqual(pg.current(), 75.0, delta=2.0)
        self.assertIsNotNone(pg._pending)

        # 播放器追上后恢复正常跟随
        pg.set_state(self.state(75, playing=False))
        self.assertIsNone(pg._pending)
        self.assertAlmostEqual(pg.current(), 75.0, delta=0.5)

    def test_drag_preview_uses_cursor_position(self):
        pg = self.make()
        pg.set_state(self.state(10))
        QtTest.QTest.mousePress(pg, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                QtCore.QPoint(100, 11))
        QtTest.QTest.mouseMove(pg, QtCore.QPoint(240, 11))
        self.assertAlmostEqual(pg.current(), 60.0, delta=2.0)      # 60% 位置
        self.assertAlmostEqual(pg._frac(), 0.6, places=2)
        self.assertTrue(pg._drag)
        QtTest.QTest.mouseRelease(pg, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                  QtCore.QPoint(240, 11))
        self.assertFalse(pg._drag)

    def test_no_seek_without_duration(self):
        pg = self.make()
        pg.seek.connect(lambda s: self.fail("不应发出 seek"))
        pg.set_state(self.state(0, dur=0, playing=False))
        QtTest.QTest.mouseClick(pg, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                QtCore.QPoint(200, 11))


if __name__ == "__main__":
    unittest.main()
