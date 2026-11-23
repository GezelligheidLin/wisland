# -*- coding: utf-8 -*-
"""主页（正在播放 + 快捷操作 + 自定义启动器）与消息页（QQ / 微信 / 钉钉 / 邮件提醒）。"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import cfg, expand
from ...services.notify import notification_summary
from ..anim import Spring, clamp
from ..theme import KIND_COLORS, G, W, icon_font, theme, ui_font
from ..widgets import (Card, Hoverable, IconButton, Label, PillButton, ScrollArea, Themed, fmt_time, hbox,
                       painter, rounded_pixmap, vbox)
from .base import Page

Qt = QtCore.Qt


# ======================================================================== 播放卡片
class Progress(QtWidgets.QWidget, Themed):
    """进度条：悬停出现圆点与时间预览，点击 / 拖拽会平滑滑到目标位置。"""

    seek = QtCore.Signal(float)
    BAR_Y = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setMouseTracking(True)
        self.pos_ = 0.0
        self.dur = 0.0
        self.stamp = 0.0
        self.playing = False
        self.hover = 0.0
        self.fill = Spring(0.0, 320.0, 1.0, 0.0006)   # 显示用的进度（0..1），弹簧跟随
        self._drag = False
        self._drag_frac = 0.0
        self._hover_frac: float | None = None
        self._pending: float | None = None            # 拖拽 / 点击后等待播放器回报的新位置
        self._pending_until = 0.0
        self._t = QtCore.QTimer(self, interval=16, timeout=self._tick)
        self._last = time.perf_counter()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hook_theme()

    # --- 状态 ---
    def set_state(self, m: dict):
        now = time.monotonic()
        pos = float(m.get("position") or 0)
        dur = float(m.get("duration") or 0)
        stamp = float(m.get("stamp") or now)
        playing = bool(m.get("playing"))
        pending = self._pending is not None
        if pending and dur > 0:
            tgt = self._pending * dur
            # 播放器还没追上拖拽的位置时，继续显示目标位置，避免进度条先弹回去再跳回来
            if now < self._pending_until and abs(pos - tgt) > max(1.5, dur * 0.01):
                pos = tgt
                stamp = now
            else:
                self._pending = None
                pending = False
        if dur <= 0:
            self._pending = None
            self.fill.snap(0.0)
        elif not pending and not self._drag:
            jump = self.dur <= 0 or abs(dur - self.dur) > 1.0 or abs(pos - self.pos_) > 8.0
            if jump:                                  # 换歌 / 大跳转：直接落到新位置
                self.fill.snap(clamp(pos / dur, 0, 1))
        self.pos_ = pos
        self.dur = dur
        self.stamp = stamp
        self.playing = playing
        self._ensure_tick()
        self.update()

    def current(self) -> float:
        if self._drag:
            return self._drag_frac * self.dur
        if self._pending is not None:
            return self._pending * self.dur           # 等待播放器确认新位置时，不显示旧进度
        p = self.pos_ + ((time.monotonic() - self.stamp) if self.playing else 0)
        return clamp(p, 0, self.dur) if self.dur else p

    def _frac(self) -> float:
        if self.dur <= 0:
            return 0.0
        if self._drag:
            return self._drag_frac
        if self._pending is not None:
            return self._pending
        return clamp(self.current() / self.dur, 0, 1)

    def _ensure_tick(self):
        if not self.isVisible():
            return
        if not self._t.isActive():
            self._last = time.perf_counter()
            self._t.start()

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, max(0.001, now - self._last))
        self._last = now
        self.fill.to(self._frac(), 720.0 if self._drag else 320.0, 1.0)
        self.fill.step(dt)
        self.update()
        if self.fill.rest and not self.playing and not self._drag:
            self._t.stop()

    # --- 交互 ---
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.dur <= 0 or not self.isEnabled():
            return
        self._drag = True
        self._hover_frac = clamp(e.position().x() / max(1, self.width()), 0, 1)
        self._drag_frac = self._hover_frac
        self._ensure_tick()
        self.update()

    def mouseMoveEvent(self, e):
        if self.dur <= 0 or not self.isEnabled():
            return
        frac = clamp(e.position().x() / max(1, self.width()), 0, 1)
        self._hover_frac = frac
        if self._drag:
            self._drag_frac = frac
            self._ensure_tick()
        self.update()

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        self._drag = False
        frac = self._drag_frac
        self._pending = frac
        self._pending_until = time.monotonic() + 1.6
        self.seek.emit(frac * self.dur)
        self._ensure_tick()
        self.update()

    def leaveEvent(self, e):
        if not self._drag:
            self._hover_frac = None
            self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._last = time.perf_counter()
        self._t.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._t.stop()
        self._hover_frac = None

    # --- 绘制 ---
    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect())
        y = self.BAR_Y
        th = 4 + 3 * self.hover + (1.5 if self._drag else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.track())
        p.drawRoundedRect(QtCore.QRectF(0, y - th / 2, r.width(), th), th / 2, th / 2)

        frac = clamp(self.fill.x, 0, 1)
        if self.dur > 0:
            p.setBrush(theme.text(0.85 + 0.15 * self.hover))
            p.drawRoundedRect(QtCore.QRectF(0, y - th / 2, max(th, r.width() * frac), th), th / 2, th / 2)

        # 悬停 / 拖拽：目标位置的预览刻度与圆点
        if self.dur > 0 and self._hover_frac is not None:
            hx = r.width() * self._hover_frac
            p.setBrush(theme.text(0.3))
            p.drawRoundedRect(QtCore.QRectF(hx - 0.75, y - th / 2 - 3, 1.5, th + 6), 0.75, 0.75)
            p.setBrush(theme.text(0.45))
            p.drawEllipse(QtCore.QPointF(hx, y), 2.6, 2.6)

        # 拖拽时拇指跟随动画后的位置
        if self.dur > 0 and self._drag:
            cx = r.width() * frac
            rad = 3.4 + 1.6 * self.hover + 1.2
            p.setBrush(theme.text(0.14))
            p.drawEllipse(QtCore.QPointF(cx, y), rad + 3.0, rad + 3.0)
            p.setBrush(theme.text(0.92))
            p.drawEllipse(QtCore.QPointF(cx, y), rad, rad)

        p.setFont(ui_font(10.5, num=True))
        p.setPen(theme.text3())
        p.drawText(QtCore.QRectF(0, 10, 60, 12), Qt.AlignmentFlag.AlignLeft, fmt_time(self.current()))
        if self.dur > 0:
            preview = self._hover_frac is not None and not self._drag
            right = self._hover_frac * self.dur if preview else self.dur
            p.setPen(theme.text2() if preview else theme.text3())
            p.drawText(QtCore.QRectF(r.width() - 60, 10, 60, 12), Qt.AlignmentFlag.AlignRight, fmt_time(right))


class CoverView(QtWidgets.QWidget):
    """封面：悬停时轻微放大上浮，并在身后透出封面主色的柔光。"""
    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self.size_ = size
        self.pm: QtGui.QPixmap | None = None
        self.glow = QtGui.QColor(255, 255, 255, 0)
        self.h = 0.0
        self.setFixedSize(size + 12, size + 12)
        theme.changed.connect(self.update)

    def set_pixmap(self, pm, glow: QtGui.QColor | None):
        self.pm = pm
        self.glow = glow or QtGui.QColor(255, 255, 255, 0)
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        c = QtCore.QRectF(self.rect()).center()
        s = 1.0 + 0.07 * self.h
        r = QtCore.QRectF(0, 0, self.size_ * s, self.size_ * s)
        r.moveCenter(c - QtCore.QPointF(0, 2.5 * self.h))
        if self.h > 0.01 and self.glow.alpha():
            g = QtGui.QRadialGradient(r.center() + QtCore.QPointF(0, 6), r.width() * 0.75)
            col = QtGui.QColor(self.glow)
            col.setAlpha(round(150 * self.h))
            g.setColorAt(0, col)
            col2 = QtGui.QColor(self.glow)
            col2.setAlpha(0)
            g.setColorAt(1, col2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(r.adjusted(-8, -4, 8, 14))
        if self.pm is not None:
            p.drawPixmap(r.toRect(), self.pm)
            return
        # 空封面：按当前主题实时画占位块，主题切换后不会残留旧配色
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.text(0.055))
        p.drawRoundedRect(r, r.width() * 0.225, r.width() * 0.225)
        p.setFont(icon_font(r.width() * 0.6))
        p.setPen(theme.text(0.42))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, G.MUSIC)


class MediaCard(Card):
    """正在播放：左边大封面，中间曲目信息，右侧控制键，进度条整行铺开。"""

    def __init__(self, ctx, parent=None):
        super().__init__(parent, 18, alpha=0.9)
        self.ctx = ctx
        self.cover_px: QtGui.QPixmap | None = None
        self.cover = CoverView(64)
        self._hov = Spring(0, 260, 0.6, 0.004)
        self._ht = QtCore.QTimer(self, interval=16, timeout=self._hover_tick)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.title = Label("未在播放", 17, W.DemiBold)
        self.artist = Label("", 12.5, level=2)
        self.app = Label("", 11.5, level=3)
        self.prog = Progress()
        self.prev = IconButton(G.PREV, 34, 15, "上一首")
        self.play = IconButton(G.PLAY, 40, 18, "播放 / 暂停", filled=True)
        self.next = IconButton(G.NEXT, 34, 15, "下一首")
        self.open_btn = PillButton("打开网易云", G.MUSIC, height=28)
        self.prev.clicked.connect(lambda: ctx.media.prev())
        self.next.clicked.connect(lambda: ctx.media.next())
        self.play.clicked.connect(lambda: ctx.media.play_pause())
        self.prog.seek.connect(lambda s: ctx.media.seek(s))
        self.open_btn.clicked.connect(self._open_player)
        self.open_btn.hide()
        self.artist.hide()
        self.app.hide()
        info = vbox(None, self.title, self.artist, self.app, None, spacing=3)
        ctrls = hbox(self.prev, self.play, self.next, spacing=10)
        top = hbox(self.cover, (info, 1), ctrls, spacing=14, margins=(4, 0, 8, 0))
        self.setLayout(vbox(top, self.prog, spacing=2, margins=(10, 8, 12, 8)))
        self._set_cover(None)

    # --- 悬停动画 ---
    def enterEvent(self, e):
        self._hov.to(1.0, 260, 0.6)
        self._ht.start()

    def leaveEvent(self, e):
        self._hov.to(0.0, 220, 0.8)
        self._ht.start()

    def _hover_tick(self):
        self._hov.step(0.016)
        h = max(0.0, self._hov.x)
        self.cover.h = h
        self.cover.update()
        self.prog.hover = h
        self.prog.update()
        self.update()
        if self._hov.rest:
            self._ht.stop()

    def _open_player(self):
        items = cfg.get("launcher") or []
        it = next((i for i in items if "cloudmusic" in str(i.get("path", "")).lower()), items[0] if items else None)
        if it:
            self.ctx.windows.open_or_focus(expand(it["path"]), it.get("exe"))

    def _set_cover(self, img):
        px = rounded_pixmap(img, 64, 15) if img is not None else None
        glow = None
        if img is not None:
            q = img if isinstance(img, QtGui.QImage) else img.toImage()
            glow = q.scaled(1, 1, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation).pixelColor(0, 0)
            h, s_, v, _ = glow.getHsv()
            glow = QtGui.QColor.fromHsv(max(0, h), min(255, int(s_ * 1.4) + 40), max(170, v))
        self.cover.set_pixmap(px, glow)

    def set_cover(self, img):
        self._set_cover(img)

    def set_media(self, m: dict):
        has = bool(m.get("has_session") and m.get("title"))
        title = (m.get("title") or "").strip()
        artist = (m.get("artist") or "").strip()
        app = (m.get("app_name") or "").strip()
        self.title.setText(title or "未在播放")
        self.artist.setText(artist)
        self.artist.setVisible(bool(artist))
        if has:
            self.app.setText(app)
            self.app.setVisible(bool(app) and app != artist)
        else:
            self.app.setText("在播放器中播放后自动显示")
            self.app.setVisible(True)
        self.play.set_glyph(G.PAUSE if m.get("playing") else G.PLAY)
        self.prog.set_state(m)
        self.prog.setVisible(True)
        self.prog.setEnabled(has and (m.get("duration") or 0) > 0)
        # 按钮始终可用：没有媒体会话（播放器没接入系统媒体控件）时会改发键盘媒体键


# ======================================================================== 快捷操作 / 启动器
class Action(Hoverable):
    """快捷操作：图标 + 文字横向排列，四个等分一行。"""
    def __init__(self, glyph: str, title: str, parent=None):
        super().__init__(parent)
        self.glyph = glyph
        self.title = title
        self.on = False
        self.setFixedHeight(46)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setToolTip(title)

    def set_on(self, on: bool, glyph: str | None = None, title: str | None = None):
        self.on = on
        if glyph:
            self.glyph = glyph
        if title:
            self.title = title
            self.setToolTip(title)
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        sc = self.press_scale()
        p.translate(r.center())
        p.scale(sc, sc)
        p.translate(-r.center())
        if self._hover.x > 0.01:
            c = theme.hover()
            c.setAlphaF(c.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r, 14, 14)
        f = ui_font(12.5, W.DemiBold if self.on else W.Medium)
        fm = QtGui.QFontMetrics(f)
        tw = fm.horizontalAdvance(self.title)
        iw, gap = 18.0, 7.0
        x0 = r.center().x() - (iw + gap + tw) / 2
        p.setFont(icon_font(16))
        p.setPen(theme.on_color() if self.on else theme.text(0.9))
        p.drawText(QtCore.QRectF(x0, r.y(), iw, r.height()), Qt.AlignmentFlag.AlignCenter, self.glyph)
        p.setFont(f)
        p.setPen(theme.on_color() if self.on else theme.text2())
        p.drawText(QtCore.QRectF(x0 + iw + gap, r.y(), tw + 4, r.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)


class HomePage(Page):
    key, glyph, title, height = "home", G.HOME, "主页", 172

    def __init__(self, ctx):
        super().__init__(ctx)
        self.media = MediaCard(ctx)
        self.media.setFixedHeight(116)
        self.a_pin = Action(G.UNPIN, "保持展开")
        self.a_pt = Action(G.POINTER, "可穿透")
        self.a_mute = Action(G.VOLUME, "静音")
        self.a_live = Action(G.LIVE, "实时字幕")
        self.a_mute.clicked.connect(lambda: ctx.audio.toggle_mute())
        self.a_live.clicked.connect(self._toggle_live)
        ctx.live.running_changed.connect(lambda on: self.a_live.set_on(on, None, "聆听中" if on else "实时字幕"))
        acts = hbox((self.a_pin, 1), (self.a_pt, 1), (self.a_mute, 1), (self.a_live, 1), spacing=8)
        self.setLayout(vbox(self.media, acts, spacing=10))

    def _toggle_live(self):
        self.ctx.live.toggle()
        if self.ctx.live.running and self.island:
            self.ctx.pages["trans"].show_live()
            self.island.show_page("trans")

    def set_muted(self, muted: bool):
        self.a_mute.set_on(muted, G.MUTE if muted else G.VOLUME, "已静音" if muted else "静音")


def cfg_path():
    from ...core.config import CONFIG_PATH
    return CONFIG_PATH


# ======================================================================== 消息
class AppTile(Hoverable):
    def __init__(self, kind: str, name: str, glyph: str, parent=None):
        super().__init__(parent)
        self.kind, self.name, self.glyph = kind, name, glyph
        self.count = 0
        self.last = ""
        self.setFixedHeight(84)

    def set_count(self, n: int, last: str = ""):
        self.count, self.last = n, last
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        s = self.press_scale()
        p.translate(r.center())
        p.scale(s, s)
        p.translate(-r.center())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.card(1.0 + 0.6 * self._hover.x))
        p.drawRoundedRect(r, 18, 18)
        col = KIND_COLORS.get(self.kind, KIND_COLORS["other"])
        cr = QtCore.QRectF(r.x() + 12, r.y() + 12, 30, 30)
        p.setBrush(col)
        p.drawEllipse(cr)
        p.setFont(icon_font(14))
        p.setPen(QtGui.QColor("white"))
        p.drawText(cr, Qt.AlignmentFlag.AlignCenter, self.glyph)
        p.setFont(ui_font(13, W.DemiBold))
        p.setPen(theme.text())
        p.drawText(QtCore.QRectF(cr.right() + 8, cr.y(), 120, 30), Qt.AlignmentFlag.AlignVCenter, self.name)
        p.setFont(ui_font(22, W.Bold, num=True))
        p.setPen(col if self.count else theme.text3())
        p.drawText(QtCore.QRectF(r.x() + 14, r.y() + 46, r.width() - 28, 30), Qt.AlignmentFlag.AlignVCenter,
                   str(self.count) if self.count else "0")
        p.setFont(ui_font(11))
        p.setPen(theme.text3())
        p.drawText(QtCore.QRectF(r.x() + 14, r.y() + 46, r.width() - 26, 30),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   self.last or "")


class HistoryList(QtWidgets.QWidget, Themed):
    opened = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self._pressed_row = -1
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hook_theme()

    def set_rows(self, rows):
        self._pressed_row = -1
        self.rows = rows[:30]
        self.setFixedHeight(max(30, len(self.rows) * 30))
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_row = int(event.position().y() // 30)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """点击同一条提醒时打开对应应用；没有会话定位数据时不猜测聊天目标。"""
        row = int(event.position().y() // 30)
        pressed = self._pressed_row
        self._pressed_row = -1
        if event.button() == Qt.MouseButton.LeftButton and row == pressed \
                and self.rect().contains(event.position().toPoint()) and 0 <= row < len(self.rows):
            self.opened.emit(self.rows[row]["kind"])
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, e):
        p = painter(self)
        if not self.rows:
            p.setFont(ui_font(12))
            p.setPen(theme.text3())
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无")
            return
        for i, r in enumerate(self.rows):
            y = i * 30
            col = KIND_COLORS.get(r.get("kind"), KIND_COLORS["other"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QtCore.QPointF(10, y + 15), 4, 4)
            p.setFont(ui_font(12.5, W.Medium))
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(24, y, 80, 30), Qt.AlignmentFlag.AlignVCenter, r.get("name", ""))
            desc = notification_summary(r)
            p.setFont(ui_font(12.5))
            p.setPen(theme.text2())
            p.drawText(QtCore.QRectF(100, y, self.width() - 170, 30), Qt.AlignmentFlag.AlignVCenter,
                       QtGui.QFontMetrics(p.font()).elidedText(desc, Qt.TextElideMode.ElideRight, self.width() - 170))
            p.setFont(ui_font(11.5, num=True))
            p.setPen(theme.text3())
            p.drawText(QtCore.QRectF(self.width() - 64, y, 60, 30), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       time.strftime("%H:%M", time.localtime(r.get("time", 0))))


class MessagesPage(Page):
    key, glyph, title, height = "msg", G.MESSAGE, "消息", 270

    def __init__(self, ctx):
        super().__init__(ctx)
        self.tiles = {
            "qq": AppTile("qq", "QQ", G.CHAT),
            "wechat": AppTile("wechat", "微信", G.MESSAGE),
            "dingtalk": AppTile("dingtalk", "钉钉", G.MESSAGE),
            "mail": AppTile("mail", "邮件", G.MAIL),
        }
        for k, t in self.tiles.items():
            t.clicked.connect(lambda k=k: self._open(k))
        self.hist = HistoryList()
        self.hist.opened.connect(self._open)
        sc = ScrollArea()
        lay = QtWidgets.QVBoxLayout(sc.inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.hist)
        lay.addStretch(1)
        clear = PillButton("全部已读", G.CHECK, height=26)
        clear.clicked.connect(self._clear_all)
        head = hbox(None, clear)
        self.setLayout(vbox(hbox(*self.tiles.values(), spacing=8), head, (sc, 1), spacing=8))

    def _open(self, kind):
        opened = self.ctx.notify.open(kind)
        if opened and self.island:
            self.island.collapse()

    def _clear_all(self):
        for k in set(self.ctx.notify.counts) | {d["kind"] for d in self.ctx.notify.history}:
            self.ctx.notify.clear(k)

    def refresh(self):
        counts = self.ctx.notify.counts
        hist = list(self.ctx.notify.history)
        for k, t in self.tiles.items():
            last = next((h for h in hist if h.get("kind") == k), None)
            t.set_count(counts.get(k, 0), time.strftime("%H:%M", time.localtime(last["time"])) if last else "")
        self.hist.set_rows(hist)

    def on_show(self):
        super().on_show()
        self.refresh()
