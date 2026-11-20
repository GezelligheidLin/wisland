# -*- coding: utf-8 -*-
"""顶部导航：透明顶栏 + 图标组。

- 标签没有背景色和分割线，图标/文字直接落在玻璃上；
- 等宽槽位不随选中项移动；当前项空间足够时显示文字，并加圆角底；
- 长按空白处 -> drag_requested，用来拖动整个灵动岛。
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from .anim import Spring, clamp
from .theme import W, icon_font, theme, ui_font
from .widgets import IconButton, painter

Qt = QtCore.Qt

ICON_W = 20.0
GAP = 6.0
MAX_SLOT = 88.0
SETTINGS_GAP = 12.0
LONG_PRESS_MS = 380


class NavBar(QtWidgets.QWidget):
    changed = QtCore.Signal(str)
    drag_requested = QtCore.Signal(QtCore.QPoint)

    def __init__(self, items: list[tuple[str, str, str]], parent=None, height: int = 36):
        super().__init__(parent)
        self.items = items                       # (key, glyph, label)
        self.index = 0
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.font_ = ui_font(12.5, W.DemiBold)
        self.hover = [Spring(0, 400, 1.0, 0.01) for _ in items]
        self.badges: dict[str, int] = {}
        self._hover_i = -1
        self._press_pos: QtCore.QPoint | None = None
        self._lp = QtCore.QTimer(self, singleShot=True, interval=LONG_PRESS_MS, timeout=self._long_press)
        self._t = QtCore.QTimer(self, interval=16, timeout=self._tick)
        self._last = time.perf_counter()
        theme.changed.connect(self.update)

        # 右侧按钮（不属于图标组）
        self.right_buttons: list[IconButton] = []

    def add_right_button(self, glyph: str, tip: str, cb) -> IconButton:
        b = IconButton(glyph, 30, 13, tip, self)
        b.setAccessibleName(tip)
        b.clicked.connect(cb)
        self.right_buttons.append(b)
        self._place_right()
        return b

    def resizeEvent(self, e):
        self._place_right()

    def _place_right(self):
        x = self.width()
        for b in reversed(self.right_buttons):
            x -= b.width()
            b.move(x, (self.height() - b.height()) // 2)
            x -= 2

    # --- 状态 ---
    def keys(self) -> list[str]:
        return [k for k, _, _ in self.items]

    def set_items(self, items: list[tuple[str, str, str]]):
        """替换可见标签，保留当前选择；调用方须至少提供一个标签。"""
        if not items:
            raise ValueError("导航至少需要一个标签")
        current = self.current()
        self._lp.stop()
        self._press_pos = None
        self._hover_i = -1
        self.unsetCursor()
        self.items = items
        keys = self.keys()
        self.index = keys.index(current) if current in keys else 0
        self.hover = [Spring(0, 400, 1.0, 0.01) for _ in items]
        self.update()

    def set_current(self, key: str, emit: bool = False):
        keys = self.keys()
        if key not in keys:
            return
        i = keys.index(key)
        if i == self.index:
            return
        self.index = i
        self.update()
        if emit:
            self.changed.emit(key)

    def current(self) -> str:
        return self.items[self.index][0]

    def set_badge(self, key: str, n: int):
        if self.badges.get(key, 0) != n:
            self.badges[key] = n
            self.update()

    def _kick(self):
        if not self._t.isActive():
            self._last = time.perf_counter()
            self._t.start()

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        rest = True
        for s in self.hover:
            s.step(dt)
            rest = rest and s.rest
        self.update()
        if rest:
            self._t.stop()

    def _rects(self) -> list[QtCore.QRectF]:
        """均分标签区域；标签较少时使用紧凑的居中组，而不是把图标拉到两端。"""
        n = len(self.items)
        end = min((b.x() - SETTINGS_GAP for b in self.right_buttons), default=self.width() - 2.0)
        available = max(0.0, end - 2.0)
        gap = min(GAP, available / (2 * n))
        width = min(MAX_SLOT, (available - gap * (n - 1)) / n)
        left = 2.0 + (available - width * n - gap * (n - 1)) / 2
        return [QtCore.QRectF(left + i * (width + gap), 2, width, self.height() - 4) for i in range(n)]

    def _shows_label(self, index: int, width: float) -> bool:
        label_width = QtGui.QFontMetrics(self.font_).horizontalAdvance(self.items[index][2])
        return index == self.index and width >= ICON_W + 5 + label_width + 14

    def _hit(self, pos: QtCore.QPointF) -> int:
        for i, r in enumerate(self._rects()):
            if r.contains(pos):
                return i
        return -1

    def event(self, e):
        if e.type() == QtCore.QEvent.Type.ToolTip:
            i = self._hit(e.pos())
            if i >= 0:
                key, _, label = self.items[i]
                count = self.badges.get(key, 0)
                tip = f"{label} · {count} 项提醒待处理" if key == "todo" and count else label
                QtWidgets.QToolTip.showText(e.globalPos(), tip, self, self._rects()[i].toAlignedRect())
            else:
                QtWidgets.QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)

    # --- 交互 ---
    def mouseMoveEvent(self, e):
        i = self._hit(e.position())
        if i != self._hover_i:
            if self._hover_i >= 0:
                self.hover[self._hover_i].to(0)
            if i >= 0:
                self.hover[i].to(1)
            self._hover_i = i
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)
            self._kick()
        if self._press_pos is not None and (e.position().toPoint() - self._press_pos).manhattanLength() > 4:
            # 在空白处按住并移动 -> 立即拖动灵动岛
            self._lp.stop()
            self._press_pos = None
            self.drag_requested.emit(QtGui.QCursor.pos())

    def leaveEvent(self, e):
        if self._hover_i >= 0:
            self.hover[self._hover_i].to(0)
            self._hover_i = -1
            self._kick()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        i = self._hit(e.position())
        if i >= 0:
            self.set_current(self.items[i][0], emit=True)
            self._press_pos = None
        else:
            self._press_pos = e.position().toPoint()
            self._lp.start()

    def mouseReleaseEvent(self, e):
        self._lp.stop()
        self._press_pos = None

    def _long_press(self):
        if self._press_pos is not None:
            self._press_pos = None
            self.drag_requested.emit(QtGui.QCursor.pos())

    def wheelEvent(self, e):
        d = -1 if e.angleDelta().y() > 0 else 1
        self.set_current(self.items[int(clamp(self.index + d, 0, len(self.items) - 1))][0], emit=True)

    # --- 绘制 ---
    def paintEvent(self, e):
        p = painter(self)
        rects = self._rects()
        fm = QtGui.QFontMetrics(self.font_)
        for i, (key, glyph, label) in enumerate(self.items):
            slot = rects[i]
            active = i == self.index
            has_label = self._shows_label(i, slot.width())
            label_width = fm.horizontalAdvance(label) if has_label else 0
            group_width = ICON_W + (5 + label_width if has_label else 0)
            pill_width = min(slot.width(), max(34, group_width + 14))
            r = QtCore.QRectF(slot.center().x() - pill_width / 2, slot.y(), pill_width, slot.height())
            opacity = self.hover[i].x
            if active or opacity > 0.01:
                c = theme.active_pill() if active else theme.hover()
                if not active:
                    c.setAlphaF(c.alphaF() * opacity * 0.8)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            p.save()
            p.setClipRect(slot)
            x = slot.center().x() - group_width / 2
            p.setFont(icon_font(15))
            p.setPen(theme.pill_text() if active else theme.text(0.55 + 0.4 * opacity))
            p.drawText(QtCore.QRectF(x, r.y(), ICON_W, r.height()), Qt.AlignmentFlag.AlignCenter, glyph)
            if has_label:
                p.setFont(self.font_)
                p.drawText(QtCore.QRectF(x + ICON_W + 5, r.y(), label_width + 2, r.height()),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
            if self.badges.get(key, 0):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QtGui.QColor("#ff453a"))
                p.drawEllipse(QtCore.QPointF(x + ICON_W - 1, r.center().y() - 8), 3.0, 3.0)
            p.restore()
