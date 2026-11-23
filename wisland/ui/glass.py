# -*- coding: utf-8 -*-
"""毛玻璃背景。

后台线程用 GDI StretchBlt(HALFTONE) 把灵动岛身后的屏幕区域直接缩成很小的图（灵动岛自身已设为
不进入截图），再做一次平均模糊；主线程绘制时用平滑放大的纹理画刷填充形状 —— 边缘抗锯齿、任意形状。
截屏要等 DWM 回读（10~30ms），所以绝不能放在 UI 线程里。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes as W

from PySide6 import QtCore, QtGui

WDA_EXCLUDEFROMCAPTURE = 0x11
DOWN = 12  # 缩小倍数（越大越糊）
SAMPLE_DOWN = 4  # 收起时本体较窄，保留更多像素供裁切测光

_u32 = ctypes.WinDLL("user32")
_gdi = ctypes.WinDLL("gdi32")
_u32.GetDC.restype = W.HDC
_u32.GetDC.argtypes = [W.HWND]
_u32.ReleaseDC.argtypes = [W.HWND, W.HDC]
_gdi.CreateCompatibleDC.restype = W.HDC
_gdi.CreateCompatibleDC.argtypes = [W.HDC]
_gdi.CreateDIBSection.restype = W.HBITMAP
_gdi.CreateDIBSection.argtypes = [W.HDC, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
_gdi.SelectObject.restype = W.HGDIOBJ
_gdi.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
_gdi.DeleteObject.argtypes = [W.HGDIOBJ]
_gdi.DeleteDC.argtypes = [W.HDC]
_gdi.SetStretchBltMode.argtypes = [W.HDC, ctypes.c_int]
_gdi.SetBrushOrgEx.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
_gdi.StretchBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD]
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
HALFTONE = 4


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                ("biClrImportant", W.DWORD)]


def exclude_from_capture(hwnd: int, on: bool = True) -> bool:
    try:
        return bool(_u32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if on else 0))
    except Exception:  # noqa: BLE001
        return False


def _blur(img: QtGui.QImage) -> QtGui.QImage:
    """3x3 平均：第 k 张以 1/k 不透明度叠加（SourceOver），结果正好是平均值，不会变亮。"""
    out = img.copy()
    p = QtGui.QPainter(out)
    k = 1
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        k += 1
        p.setOpacity(1 / k)
        p.drawImage(dx, dy, img)
    p.end()
    return out


def grab_tiny(px: int, py: int, pw: int, ph: int, tw: int, th: int) -> QtGui.QImage | None:
    """物理像素区域 -> tw x th 的缩略图（HALFTONE 面积平均）。线程安全。"""
    sdc = _u32.GetDC(None)
    if not sdc:
        return None
    mdc = _gdi.CreateCompatibleDC(sdc)
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), tw, -th, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hbmp = _gdi.CreateDIBSection(mdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    img = None
    try:
        if hbmp and bits.value:
            old = _gdi.SelectObject(mdc, hbmp)
            _gdi.SetStretchBltMode(mdc, HALFTONE)
            _gdi.SetBrushOrgEx(mdc, 0, 0, None)
            if _gdi.StretchBlt(mdc, 0, 0, tw, th, sdc, px, py, pw, ph, SRCCOPY):   # 不用 CAPTUREBLT：它会让分层窗口闪烁
                buf = ctypes.string_at(bits.value, tw * th * 4)
                img = QtGui.QImage(buf, tw, th, tw * 4, QtGui.QImage.Format.Format_RGB32).copy()
            _gdi.SelectObject(mdc, old)
    finally:
        if hbmp:
            _gdi.DeleteObject(hbmp)
        _gdi.DeleteDC(mdc)
        _u32.ReleaseDC(None, sdc)
    return img


class Backdrop(QtCore.QObject):
    """后台截取 + 模糊。主线程调用 request() 告知要覆盖的区域，结果通过 ready 信号回到主线程。
    远程模式下 set_paused(True) 暂停截取，纹理改由主线程 adopt / retarget 维护。"""
    ready = QtCore.Signal(bool)          # changed
    _captured = QtCore.Signal(object, bool)

    def __init__(self):
        super().__init__()
        # (模糊小图, 对应的全局逻辑区域, 亮度)；整体替换，主线程读到的总是一致的一组
        self.state: tuple = (None, QtCore.QRect(), 0.5)
        self._sig = None
        self._capture_rect = QtCore.QRect()
        self._captured.connect(self._publish)
        self._lock = threading.Lock()
        self._want: tuple | None = None     # (逻辑截图区域, 物理截图区域, 测光区域)
        self._interval = 0.1
        self._wake = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="wisland-glass", daemon=True)
        # 参与屏幕捕获时：只在自己截取背景的那一瞬间把灵动岛排除，截完立即恢复
        self.hwnd = 0
        self.self_exclude = False
        self._started = False
        self._paused = False

    def start(self):
        if not self._started:
            self._started = True
            self._thread.start()

    @property
    def tiny(self):
        return self.state[0]

    @property
    def rect(self):
        return self.state[1]

    @property
    def luminance(self):
        return self.state[2]

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, on: bool):
        """远程模式：暂停取背景，保留最后一张纹理，也就不再临时把自己排除出截图。
        恢复时立刻补一张，玻璃马上跟上当前画面。"""
        with self._lock:
            changed = self._paused != bool(on)
            self._paused = bool(on)
        if changed and not self._paused:
            self._wake.set()

    def adopt(self, state):
        """暂停期间由主线程接管纹理（例如冻结正在进行的背景渐变），之后的 retarget 基于它。"""
        self.state = state

    def retarget(self, grect: QtCore.QRect) -> bool:
        """暂停期间让已有纹理跟着灵动岛的新区域平移 / 缩放，避免移动或展开时玻璃露底。
        纹理本身很糊，拉伸看不出来；只在暂停时由主线程调用。"""
        tiny, rect, lum = self.state
        if tiny is None or grect == rect:
            return False
        self.state = (tiny, QtCore.QRect(grect), lum)
        return True

    def stop(self):
        self._stop = True
        self._wake.set()

    def request(self, screen: QtGui.QScreen, grect: QtCore.QRect, interval: float,
                urgent: bool = False, *, sample_rect: QtCore.QRect):
        """grect 为毛玻璃纹理范围，sample_rect 为本体测光范围，均使用全局逻辑坐标。"""
        sg = screen.geometry()
        r = grect.intersected(sg)
        if r.width() < 4 or r.height() < 4:
            return
        dpr = screen.devicePixelRatio()
        # Qt6 在 Windows 上保持屏幕原点不缩放：物理 = 原点 + (逻辑 - 原点) * dpr
        px = sg.x() + round((r.x() - sg.x()) * dpr)
        py = sg.y() + round((r.y() - sg.y()) * dpr)
        phys = (px, py, round(r.width() * dpr), round(r.height() * dpr))
        with self._lock:
            new = (r, phys, sample_rect.intersected(r))
            moved = self._want is None or self._want[0] != new[0]
            self._want = new
            self._interval = interval
        if urgent or moved:
            self._wake.set()

    def _run(self):
        last = 0.0
        while not self._stop:
            with self._lock:
                interval = self._interval
                paused = self._paused
            if paused:
                # 远程模式：不取背景，只等恢复 / 退出信号，避免空转占 CPU。
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            self._wake.wait(max(0.0, interval - (time.perf_counter() - last)))
            self._wake.clear()
            if self._stop:
                break
            # 两次截图之间至少隔 1/45s，避免被连续唤醒时占满 CPU
            gap = time.perf_counter() - last
            if gap < 1 / 45:
                time.sleep(1 / 45 - gap)
            with self._lock:
                want = self._want
            if want is None:
                continue
            last = time.perf_counter()
            try:
                self._capture(*want)
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()

    def _capture(self, r: QtCore.QRect, phys, sample_rect: QtCore.QRect):
        tw, th = max(2, r.width() // DOWN), max(2, r.height() // DOWN)
        sw, sh = r.width() // SAMPLE_DOWN, r.height() // SAMPLE_DOWN
        sw, sh = max(tw, sw), max(th, sh)
        toggle = self.self_exclude and self.hwnd
        if toggle:
            _u32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)
        try:
            img = grab_tiny(*phys, sw, sh)
        finally:
            if toggle:
                _u32.SetWindowDisplayAffinity(self.hwnd, 0)
        if img is None:
            return
        mode = QtCore.Qt.TransformationMode.SmoothTransformation
        aspect = QtCore.Qt.AspectRatioMode.IgnoreAspectRatio
        # 在模糊前裁切本体，避免预留的展开区和纹理边距影响明暗判断。
        scale = QtGui.QTransform.fromScale(sw / r.width(), sh / r.height())
        crop = scale.mapRect(QtCore.QRectF(sample_rect.translated(-r.topLeft()))).toRect()
        lum = 0.5  # 没有可测像素时保留当前主题。
        if not crop.isEmpty():
            c = img.copy(crop).scaled(1, 1, aspect, mode).pixelColor(0, 0)
            lum = 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
        img = img.scaled(tw, th, aspect, mode).convertToFormat(QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        img = _blur(_blur(img))
        thumbnail = img.scaled(8, 4, aspect, mode)
        sig = bytes(thumbnail.constBits())
        changed = sig != self._sig or r != self._capture_rect
        self._sig = sig
        self._capture_rect = QtCore.QRect(r)
        self._captured.emit((img, QtCore.QRect(r), lum), changed)

    @QtCore.Slot(object, bool)
    def _publish(self, state, changed: bool):
        """在 UI 线程发布整帧，让亮度判断和绘制始终使用同一张截图。"""
        self.state = state
        self.ready.emit(changed)

    @staticmethod
    def blend_states(source, target, progress: float):
        """冻结当前可见的混合背景，供被新截图打断的渐变连续接续。"""
        if not source or source[0] is None or progress >= 1:
            return target
        if not target or target[0] is None or progress <= 0:
            return source
        rect = source[1].united(target[1])
        width = max(2, (rect.width() + DOWN - 1) // DOWN)
        height = max(2, (rect.height() + DOWN - 1) // DOWN)
        image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(width / rect.width(), height / rect.height())
        painter.translate(-rect.x(), -rect.y())
        origin = QtCore.QPoint()
        painter.fillRect(rect, Backdrop.brush_for(source, origin))
        painter.setOpacity(progress)
        painter.fillRect(rect, Backdrop.brush_for(target, origin))
        painter.end()
        return image, rect, source[2] + (target[2] - source[2]) * progress

    def covers(self, grect: QtCore.QRect) -> bool:
        tiny, rect, _ = self.state
        return tiny is not None and rect.contains(grect)

    @staticmethod
    def brush_for(state, window_origin: QtCore.QPoint) -> QtGui.QBrush | None:
        if not state or state[0] is None:
            return None
        tiny, rect = state[0], state[1]
        b = QtGui.QBrush(tiny)
        tr = QtGui.QTransform()
        tr.translate(rect.x() - window_origin.x(), rect.y() - window_origin.y())
        tr.scale(rect.width() / tiny.width(), rect.height() / tiny.height())
        b.setTransform(tr)
        return b

    def brush(self, window_origin: QtCore.QPoint) -> QtGui.QBrush | None:
        """返回把模糊小图铺到窗口坐标的画刷。window_origin = 窗口左上角的全局坐标。"""
        tiny, rect, _ = self.state
        if tiny is None:
            return None
        b = QtGui.QBrush(tiny)
        tr = QtGui.QTransform()
        tr.translate(rect.x() - window_origin.x(), rect.y() - window_origin.y())
        tr.scale(rect.width() / tiny.width(), rect.height() / tiny.height())
        b.setTransform(tr)
        return b
