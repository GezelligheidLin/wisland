"""背景渐变中连续收到截图时的回归测试；不截取真实桌面。"""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtWidgets

from wisland.ui.glass import Backdrop
from wisland.ui.island import Island
from wisland.ui.theme import THEME_EASING, THEME_TRANSITION_MS, theme


class Panel:
    _on_backdrop = Island._on_backdrop
    _backdrop_progress = Island._backdrop_progress
    _paint_glass = Island._paint_glass

    def __init__(self, width, height):
        self.body = QtCore.QRectF(0, 0, width, height)
        self._win_origin = QtCore.QPoint()
        self._bd_prev = self._bd_target = None
        self._bd_fade_t0 = 0
        self._bd_fade_duration = THEME_TRANSITION_MS / 1000
        self._bd_fade_from = 0
        self.backdrop = SimpleNamespace(brush_for=Backdrop.brush_for)

    def _kick(self):
        pass

    def receive(self, gray, now, rect=None):
        rect = rect or self.body.toRect()
        image = QtGui.QImage(16, 8, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtGui.QColor(gray, gray, gray))
        self.backdrop.state = image, rect, gray / 255
        self.backdrop.luminance = gray / 255
        with patch('wisland.ui.island.time.perf_counter', return_value=now):
            self._on_backdrop(True)

    def pixel(self, now):
        image = QtGui.QImage(self.body.size().toSize(), QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        path = QtGui.QPainterPath()
        path.addRect(self.body)
        painter = QtGui.QPainter(image)
        with patch('wisland.ui.island.time.perf_counter', return_value=now):
            self._paint_glass(painter, path)
        painter.end()
        return image.pixelColor(image.width() // 2, image.height() // 2).red()


class BackgroundTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        theme.set_mode('dark')
        theme._transition.setCurrentTime(THEME_TRANSITION_MS)

    def tearDown(self):
        theme._transition.stop()

    def test_retarget_keeps_visible_color_and_original_deadline(self):
        for size in ((180, 40), (640, 300)):
            with self.subTest(size=size):
                panel = Panel(*size)
                panel.receive(0, 10)
                panel.receive(255, 11)
                for delta, gray in ((.06, 220), (.12, 160), (.20, 190), (.24, 230)):
                    now = 11 + delta
                    before = panel.pixel(now)
                    panel.receive(gray, now)
                    self.assertLessEqual(abs(panel.pixel(now) - before), 2)
                    self.assertAlmostEqual(panel._bd_fade_t0 + panel._bd_fade_duration, 11.26)
                    # 接续后使用原曲线的尾段，不能重新从几乎静止的缓入阶段开始。
                    start = THEME_EASING.valueForProgress(delta / .26)
                    future = THEME_EASING.valueForProgress((delta + .005) / .26)
                    self.assertAlmostEqual(panel._backdrop_progress(now + .005), (future - start) / (1 - start))
                final = panel.pixel(11.260001)
                self.assertIsNone(panel._bd_prev)
                self.assertEqual(final, panel.pixel(12))

    def test_auto_theme_reversal_starts_from_visible_colors(self):
        for size in ((180, 40), (640, 300)):
            with self.subTest(size=size):
                theme.set_mode('auto')
                panel = Panel(*size)
                panel.receive(0, 10)
                theme._transition.setCurrentTime(THEME_TRANSITION_MS)
                panel.receive(255, 11)
                self.assertEqual(theme.pal_name, 'light')
                theme._transition.pause()
                theme._transition.setCurrentTime(80)
                before = panel.pixel(11.08)
                text_before = theme.text()
                panel.receive(0, 11.08)
                self.assertEqual(theme.pal_name, 'dark')
                self.assertEqual(theme.text(), text_before)
                self.assertLessEqual(abs(panel.pixel(11.08) - before), 2)
                theme._transition.setCurrentTime(THEME_TRANSITION_MS)
                final = panel.pixel(11.340001)
                self.assertEqual(final, panel.pixel(12))

    def test_auto_theme_samples_current_body_inside_wider_background(self):
        screen_rect = QtCore.QRect(-1920, 0, 1920, 1080)
        screen = SimpleNamespace(geometry=lambda: screen_rect, devicePixelRatio=lambda: 1.5)
        capture = QtCore.QRect(-1760, -40, 400, 280)
        bright_body = QtCore.QRect(-1640, 0, 160, 24)
        dark_body = bright_body.translated(0, 24)
        desktop = QtGui.QImage(100, 60, QtGui.QImage.Format.Format_RGB32)
        desktop.fill(QtCore.Qt.GlobalColor.black)
        painter = QtGui.QPainter(desktop)
        painter.fillRect(30, 0, 40, 6, QtCore.Qt.GlobalColor.white)
        painter.end()
        backdrop = Backdrop()
        panel = Panel(160, 24)
        panel.backdrop = backdrop
        backdrop.ready.connect(panel._on_backdrop)
        theme.set_mode("auto")

        with patch("wisland.ui.glass.grab_tiny", return_value=desktop) as grab:
            backdrop.request(screen, capture, .1, sample_rect=bright_body)
            backdrop._capture(*backdrop._want)
            grab.assert_called_once_with(-1680, 0, 600, 360, 100, 60)
            self.assertEqual(backdrop.rect, capture.intersected(screen_rect))
            self.assertAlmostEqual(backdrop.luminance, 1)
            self.assertEqual(theme.pal_name, "light")

            backdrop.request(screen, capture, .1, sample_rect=dark_body)
            backdrop._capture(*backdrop._want)
            self.assertAlmostEqual(backdrop.luminance, 0)
            self.assertEqual(theme.pal_name, "dark")

    def test_blend_preserves_global_coordinates_when_capture_moves(self):
        source = QtGui.QImage(20, 10, QtGui.QImage.Format.Format_RGB32)
        source.fill(QtGui.QColor('black'))
        target = source.copy()
        target.fill(QtGui.QColor('white'))
        a = source, QtCore.QRect(100, 50, 240, 120), 0
        b = target, QtCore.QRect(124, 62, 240, 120), 1
        mixed = Backdrop.blend_states(a, b, .5)
        self.assertEqual(mixed[1], a[1].united(b[1]))
        self.assertAlmostEqual(mixed[2], .5)
        self.assertLessEqual(abs(mixed[0].pixelColor(5, 5).red() - 128), 1)
        self.assertIs(Backdrop.blend_states(a, b, 0), a)
        self.assertIs(Backdrop.blend_states(a, b, 1), b)

    def test_worker_frames_publish_in_order_on_ui_thread(self):
        backdrop = Backdrop()
        original = backdrop.state
        published = []
        backdrop.ready.connect(lambda changed: published.append((backdrop.state, threading.get_ident())))
        frames = [(None, QtCore.QRect(0, 0, 120, 60), lum) for lum in (.2, .8)]

        def capture():
            for frame in frames:
                backdrop._captured.emit(frame, True)

        worker = threading.Thread(target=capture)
        worker.start()
        worker.join()
        self.assertIs(backdrop.state, original)
        self.app.processEvents()
        self.assertEqual([state for state, _ in published], frames)
        self.assertTrue(all(thread_id == threading.get_ident() for _, thread_id in published))


if __name__ == '__main__':
    unittest.main()
