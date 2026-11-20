# -*- coding: utf-8 -*-
"""股票（腾讯财经公开行情）与天气（UAPIs 数据，QWeather Icons 图标）。"""
from __future__ import annotations

import datetime as dt

from PySide6 import QtCore, QtGui, QtWidgets

from ...core.config import cfg
from ..anim import clamp
from ..theme import G, W, theme, ui_font
from ..widgets import (Card, Hoverable, IconButton, Label, LineEdit, PillButton, ScrollArea, Segmented,
                       Sparkline, Themed, hbox, painter, vbox)
from .base import Page

Qt = QtCore.Qt
MARKETS = [("a", "A股"), ("hk", "港股"), ("us", "美股")]


def _pct_color(pct):
    if pct is None or abs(pct) < 1e-9:
        return theme.text2()
    return theme.up() if pct > 0 else theme.down()


class StockList(QtWidgets.QWidget, Themed):
    picked = QtCore.Signal(dict)
    menu_requested = QtCore.Signal(dict)
    ROW = 36

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.sel = None
        self.hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hook_theme()

    def set_rows(self, rows):
        self.rows = rows
        self.setFixedHeight(max(40, len(rows) * self.ROW))
        self.update()

    def mouseMoveEvent(self, e):
        i = int(e.position().y() // self.ROW)
        if i != self.hover:
            self.hover = i
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        i = int(e.position().y() // self.ROW)
        if e.button() == Qt.MouseButton.RightButton:
            if 0 <= i < len(self.rows):
                self.menu_requested.emit(self.rows[i])
            return
        if 0 <= i < len(self.rows):
            self.sel = self.rows[i].get("code")
            self.picked.emit(self.rows[i])
            self.update()

    def paintEvent(self, e):
        p = painter(self)
        if not self.rows:
            p.setFont(ui_font(12))
            p.setPen(theme.text3())
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "自选为空，点右上角 + 添加")
            return
        w = self.width()
        for i, s in enumerate(self.rows):
            y = i * self.ROW
            r = QtCore.QRectF(0, y + 1, w, self.ROW - 2)
            if i == self.hover or s.get("code") == self.sel:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.hover() if i == self.hover else theme.card(0.8))
                p.drawRoundedRect(r, 12, 12)
            p.setFont(ui_font(13, W.Medium))
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(12, y, 150, self.ROW), Qt.AlignmentFlag.AlignVCenter, s.get("name") or s.get("code"))
            p.setFont(ui_font(10.5, num=True))
            p.setPen(theme.text3())
            p.drawText(QtCore.QRectF(150, y, 120, self.ROW), Qt.AlignmentFlag.AlignVCenter, s.get("code", ""))
            price, pct = s.get("price"), s.get("pct")
            p.setFont(ui_font(13.5, W.DemiBold, num=True))
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(w - 210, y, 110, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       "--" if price is None else (f"{price:.3f}" if price < 10 else f"{price:.2f}"))
            # 涨跌幅胶囊
            pr = QtCore.QRectF(w - 88, y + 7, 78, self.ROW - 14)
            col = _pct_color(pct)
            bg = QtGui.QColor(col)
            bg.setAlpha(46)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(pr, pr.height() / 2, pr.height() / 2)
            p.setPen(col)
            p.setFont(ui_font(12.5, W.DemiBold, num=True))
            p.drawText(pr, Qt.AlignmentFlag.AlignCenter, "--" if pct is None else f"{pct:+.2f}%")


class StocksPage(Page):
    key, glyph, title, height = "stock", G.STOCK, "股票", 330

    def __init__(self, ctx):
        super().__init__(ctx)
        self.data: dict = {}
        self.market = "a"
        self.seg = Segmented([n for _, n in MARKETS])
        self.seg.setFixedWidth(180)
        self.seg.changed.connect(self._market)
        self.status = Label("", 11.5, level=3, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.add_btn = IconButton(G.ADD, 28, 12, "添加自选（代码 / 名称 / 拼音）")
        self.add_btn.clicked.connect(self._start_add)
        self.add_edit = LineEdit("代码 / 名称 / 拼音，回车搜索")
        self.add_edit.setFixedWidth(220)
        self.add_edit.setFixedHeight(30)
        self.add_edit.hide()
        self.add_edit.focus_wanted.connect(self.want_focus)
        self.add_edit.returnPressed.connect(self._do_search)
        self.add_edit.installEventFilter(self)
        # 分时卡片
        self.detail = Card(radius=16)
        self.d_name = Label("", 13, W.DemiBold)
        self.d_price = Label("", 13, W.DemiBold, num=True, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.spark = Sparkline()
        self.spark.setFixedHeight(56)
        self.detail.setLayout(vbox(hbox(self.d_name, self.d_price), self.spark, spacing=2, margins=(12, 8, 12, 8)))
        self.detail.hide()
        self.list = StockList()
        self.list.picked.connect(self._pick)
        self.list.menu_requested.connect(self._row_menu)
        sc = ScrollArea()
        lay = QtWidgets.QVBoxLayout(sc.inner)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.addWidget(self.list)
        lay.addStretch(1)
        self.err = Label("", 12, level=2, align=Qt.AlignmentFlag.AlignCenter)
        self.err.hide()
        self.setLayout(vbox(hbox(self.seg, None, self.add_edit, self.status, self.add_btn, spacing=6),
                            self.detail, self.err, (sc, 1), spacing=8))

    def _market(self, i):
        self.market = MARKETS[i][0]
        self.detail.hide()
        self.list.sel = None
        self._render()

    def _pick(self, s):
        self.d_name.setText(s.get("name") or s.get("code"))
        pct = s.get("pct")
        self.d_price.setText(f"{s.get('price') or '--'}   {pct:+.2f}%" if pct is not None else "--")
        self.d_price.set_color(_pct_color(pct))
        self.detail.show()
        code = s.get("code")
        prev = s.get("prev_close")
        self.ctx.stocks.minute(code, lambda pts, prev=prev: self._minute(code, pts, prev))

    # ---------- 添加 / 删除自选 ----------
    def _start_add(self):
        self.add_btn.hide()
        self.status.hide()
        self.add_edit.clear()
        self.add_edit.show()
        self.want_focus()
        self.add_edit.setFocus()

    def _end_add(self):
        self.add_edit.hide()
        self.add_btn.show()
        self.status.show()
        if self.island is not None:
            self.island.release_focus()

    def _do_search(self):
        q = self.add_edit.text().strip()
        if not q:
            self._end_add()
            return
        self.ctx.stocks.search(q, self._results)

    def _results(self, items):
        from .system import styled_menu
        self._end_add()
        m = styled_menu(self)
        if not items:
            m.addAction("没有找到匹配的股票").setEnabled(False)
        for it in items[:10]:
            a = m.addAction(f"{it['name']}    {it['code']}")
            a.triggered.connect(lambda _=False, c=it["code"]: self._add(c))
        if self.island:
            self.island.locks.add("menu")
            m.aboutToHide.connect(lambda: self.island.locks.discard("menu"))
        m.popup(QtGui.QCursor.pos())

    def _add(self, code):
        from ...services.stocks import market_of
        self.ctx.stocks.add(code)
        i = [k for k, _ in MARKETS].index(market_of(code))
        self.seg.set_index(i)
        self._market(i)

    def _row_menu(self, row):
        from .system import styled_menu
        m = styled_menu(self)
        m.addAction(f"从自选删除 {row.get('name') or row.get('code')}").triggered.connect(
            lambda: self.ctx.stocks.remove(row.get("code", "")))
        if self.island:
            self.island.locks.add("menu")
            m.aboutToHide.connect(lambda: self.island.locks.discard("menu"))
        m.popup(QtGui.QCursor.pos())

    def eventFilter(self, obj, ev):
        if obj is self.add_edit and ev.type() == QtCore.QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self._end_add()
            return True
        if obj is self.add_edit and ev.type() == QtCore.QEvent.Type.FocusOut and not self.add_edit.text().strip():
            self._end_add()
        return False

    def _minute(self, code, pts, prev):
        if self.list.sel != code:
            return
        col = _pct_color((pts[-1] - prev) if pts and prev else 0)
        self.spark.color = col
        self.spark.set_values(pts, baseline=prev)

    def set_data(self, d: dict):
        self.data = d
        if self.active:
            self._render()

    def _render(self):
        d = self.data or {}
        rows = (d.get("markets") or {}).get(self.market) or []
        st = (d.get("status") or {}).get(self.market, "")
        t = d.get("time")
        ts = dt.datetime.fromtimestamp(t).strftime("%H:%M:%S") if isinstance(t, (int, float)) else (t or "")
        self.status.setText(f"{st}  {ts}".strip())
        if not d.get("ok") and d.get("error"):
            self.err.setText(d["error"])
            self.err.show()
        else:
            self.err.hide()
        self.list.set_rows(rows)

    def on_show(self):
        super().on_show()
        self._render()


# ======================================================================== 天气
class GlyphLabel(QtWidgets.QWidget, Themed):
    def __init__(self, px: int, parent=None):
        super().__init__(parent)
        self.px = px
        self.glyph = ("", "Segoe Fluent Icons")
        self.setFixedSize(px + 10, px + 10)
        self._hook_theme()

    def set_glyph(self, g):
        self.glyph = g
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        f = QtGui.QFont()
        f.setFamilies([self.glyph[1], "Segoe Fluent Icons", "Segoe UI Symbol"])
        f.setPixelSize(self.px)
        p.setFont(f)
        p.setPen(theme.text(0.9))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.glyph[0])


class Hourly(QtWidgets.QWidget, Themed):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.tz = None
        self.setFixedHeight(78)
        self._hook_theme()

    def set_items(self, items):
        self.items = items[:8]
        self.update()

    def paintEvent(self, e):
        from ...services.weather import weather_glyph
        p = painter(self)
        n = max(1, len(self.items))
        cw = self.width() / n
        for i, it in enumerate(self.items):
            x = i * cw
            p.setFont(ui_font(11, num=True))
            p.setPen(theme.text3())
            p.drawText(QtCore.QRectF(x, 0, cw, 16), Qt.AlignmentFlag.AlignCenter,
                       (dt.datetime.fromtimestamp(it.get("time", 0) + self.tz, dt.timezone.utc) if self.tz is not None
                        else dt.datetime.fromtimestamp(it.get("time", 0))).strftime("%H时"))
            g, fam = weather_glyph(it.get("icon"))
            f = QtGui.QFont()
            f.setFamilies([fam, "Segoe Fluent Icons", "Segoe UI Symbol"])
            f.setPixelSize(19)
            p.setFont(f)
            p.setPen(theme.text(0.9))
            p.drawText(QtCore.QRectF(x, 18, cw, 28), Qt.AlignmentFlag.AlignCenter, g)
            p.setFont(ui_font(13, W.DemiBold, num=True))
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(x, 46, cw, 16), Qt.AlignmentFlag.AlignCenter, f"{round(it.get('temp', 0))}°")
            pop = it.get("pop") or 0
            if pop >= 0.2:
                p.setFont(ui_font(10, num=True))
                p.setPen(QtGui.QColor("#4f9cff"))
                p.drawText(QtCore.QRectF(x, 62, cw, 14), Qt.AlignmentFlag.AlignCenter, f"{round(pop * 100)}%")


class Daily(QtWidgets.QWidget, Themed):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self._hook_theme()

    def set_items(self, items):
        self.items = items[:5]
        self.update()

    def paintEvent(self, e):
        from ...services.weather import weather_glyph
        if not self.items:
            return
        p = painter(self)
        lo = min(i["min"] for i in self.items)
        hi = max(i["max"] for i in self.items)
        rh = self.height() / len(self.items)
        wk = "一二三四五六日"
        for k, it in enumerate(self.items):
            y = k * rh
            d = it.get("date")
            try:
                day = dt.date.fromisoformat(str(d))
                name = "今天" if day == dt.date.today() else "周" + wk[day.weekday()]
            except Exception:  # noqa: BLE001
                name = str(d)
            p.setFont(ui_font(12.5, W.Medium))
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(0, y, 40, rh), Qt.AlignmentFlag.AlignVCenter, name)
            g, fam = weather_glyph(it.get("icon"))
            f = QtGui.QFont()
            f.setFamilies([fam, "Segoe Fluent Icons", "Segoe UI Symbol"])
            f.setPixelSize(16)
            p.setFont(f)
            p.drawText(QtCore.QRectF(40, y, 24, rh), Qt.AlignmentFlag.AlignCenter, g)
            if it.get("text"):
                p.setFont(ui_font(11.5))
                p.setPen(theme.text2())
                fm = QtGui.QFontMetrics(p.font())
                text = fm.elidedText(str(it["text"]), Qt.TextElideMode.ElideRight, 48)
                p.drawText(QtCore.QRectF(64, y, 48, rh), Qt.AlignmentFlag.AlignVCenter, text)
            p.setFont(ui_font(12, num=True))
            p.setPen(theme.text2())
            p.drawText(QtCore.QRectF(114, y, 32, rh), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{round(it['min'])}°")
            p.setPen(theme.text())
            p.drawText(QtCore.QRectF(self.width() - 36, y, 32, rh), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       f"{round(it['max'])}°")
            bx, bw = 152, self.width() - 152 - 44
            span = max(1e-6, hi - lo)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.track())
            p.drawRoundedRect(QtCore.QRectF(bx, y + rh / 2 - 2.5, bw, 5), 2.5, 2.5)
            x0 = bx + bw * (it["min"] - lo) / span
            x1 = bx + bw * (it["max"] - lo) / span
            g2 = QtGui.QLinearGradient(bx, 0, bx + bw, 0)
            g2.setColorAt(0, QtGui.QColor("#5ac8fa"))
            g2.setColorAt(1, QtGui.QColor("#ff9f0a"))
            p.setBrush(g2)
            p.drawRoundedRect(QtCore.QRectF(x0, y + rh / 2 - 2.5, max(5, x1 - x0), 5), 2.5, 2.5)


class CityChip(Hoverable):
    """城市胶囊：名称 + 当前温度；选中时半透明白底。"""
    def __init__(self, idx: int, name: str, parent=None):
        super().__init__(parent)
        self.idx, self.name = idx, name
        self.temp = ""
        self.glyph = ("", "Segoe Fluent Icons")
        self.selected = False
        self.default = idx == 0
        self.setFixedHeight(30)
        self._fit()

    def _fit(self):
        fm = QtGui.QFontMetrics(ui_font(12.5, W.DemiBold))
        self.setFixedWidth(fm.horizontalAdvance(self.name) + fm.horizontalAdvance(self.temp or "--°") + 50)

    def set_info(self, temp: str, glyph):
        self.temp, self.glyph = temp, glyph
        self._fit()
        self.update()

    def paintEvent(self, e):
        from ..widgets import painter as _p
        p = _p(self)
        r = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.active_pill())
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        elif self._hover.x > 0.01:
            c = theme.hover()
            c.setAlphaF(c.alphaF() * self._hover.x)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        f = QtGui.QFont()
        f.setFamilies([self.glyph[1], "Segoe Fluent Icons", "Segoe UI Symbol"])
        f.setPixelSize(14)
        p.setFont(f)
        p.setPen(theme.text(0.9))
        p.drawText(QtCore.QRectF(r.x() + 8, r.y(), 18, r.height()), Qt.AlignmentFlag.AlignCenter, self.glyph[0])
        p.setFont(ui_font(12.5, W.DemiBold))
        p.setPen(theme.pill_text() if self.selected else theme.text(0.75))
        fm = QtGui.QFontMetrics(p.font())
        x = r.x() + 30
        p.drawText(QtCore.QRectF(x, r.y(), 200, r.height()), Qt.AlignmentFlag.AlignVCenter, self.name)
        x += fm.horizontalAdvance(self.name) + 6
        p.setFont(ui_font(12, W.Medium, num=True))
        p.setPen(theme.text2())
        p.drawText(QtCore.QRectF(x, r.y(), 60, r.height()), Qt.AlignmentFlag.AlignVCenter, self.temp or "--°")
        if self.default:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.on_color())
            p.drawEllipse(QtCore.QPointF(r.right() - 6, r.y() + 6), 2.5, 2.5)


def _fmt_clock(ts, tz_off):
    if not ts:
        return "--"
    if isinstance(ts, str):
        # Older cached entries stored the city's clock time directly.
        return ts.strip()[:5] if ":" in ts else "--"
    try:
        return dt.datetime.fromtimestamp(int(ts) + int(tz_off or 0), dt.timezone.utc).strftime("%H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return "--"


def _wind_dir(deg):
    if deg is None:
        return ""
    return ["北", "东北", "东", "东南", "南", "西南", "西", "西北"][int(((deg + 22.5) % 360) // 45)] + "风"


_AQI_COLORS = {1: "#34c759", 2: "#f5c518", 3: "#ff9f0a", 4: "#ff453a", 5: "#bf5af2", 6: "#a33b3b"}


def _aqi_color(value, level) -> QtGui.QColor | None:
    try:
        grade = int(level)
    except (TypeError, ValueError):
        grade = 0
    if grade not in _AQI_COLORS:
        try:
            grade = 1 if value <= 50 else 2 if value <= 100 else 3 if value <= 150 else 4 if value <= 200 else 5 if value <= 300 else 6
        except TypeError:
            return None
    color = _AQI_COLORS.get(grade)
    return QtGui.QColor(color) if color else None


def _clock_text(value) -> str:
    """把接口时间（2026-11-20 16:50:00 或 ISO）压缩成 HH:MM。"""
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return ""
    chunk = text.split()[-1] if " " in text else text
    return chunk[:5] if ":" in chunk else ""


def _air_text(d: dict) -> str:
    parts = []
    primary = d.get("aqi_primary")
    if primary:
        parts.append(f"首要 {primary}")
    labels = (("pm25", "PM2.5"), ("pm10", "PM10"), ("o3", "O₃"), ("no2", "NO₂"), ("so2", "SO₂"), ("co", "CO"))
    for key, label in labels:
        value = (d.get("air") or {}).get(key)
        if value is None:
            continue
        parts.append(f"{label} {value:.0f}" if key != "co" else f"CO {value:g}")
    return " · ".join(parts)


class WeatherPage(Page):
    key, glyph, title, height = "weather", G.CLOUD, "天气", 425

    def __init__(self, ctx):
        super().__init__(ctx)
        self.sel = 0
        self.data: list[dict] = []
        self.chips: list[CityChip] = []
        self._editing = None
        self._pending_sel = None
        # 顶部：城市胶囊 + 添加
        self.chip_row = QtWidgets.QHBoxLayout()
        self.chip_row.setSpacing(4)
        self.chip_row.setContentsMargins(0, 0, 0, 0)
        self.add_btn = IconButton(G.ADD, 28, 12, "添加城市")
        self.add_btn.clicked.connect(self._start_add)
        self.add_edit = LineEdit("城市名")
        self.add_edit.setFixedWidth(220)
        self.add_edit.setFixedHeight(30)
        self.add_edit.hide()
        self.add_edit.focus_wanted.connect(self.want_focus)
        self.add_edit.returnPressed.connect(self._do_add)
        self.add_edit.installEventFilter(self)
        refresh = IconButton(G.REFRESH, 28, 12, "重新加载缓存；天气每天最多查询一次")
        refresh.clicked.connect(lambda: ctx.weather.refresh())
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(6)
        head.setContentsMargins(0, 0, 0, 0)
        head.addLayout(self.chip_row)
        head.addWidget(self.add_btn)
        head.addWidget(self.add_edit)
        head.addStretch(1)
        self.msg = Label("", 11.5, level=3)
        head.addWidget(self.msg)
        head.addWidget(refresh)

        # 详情
        self.icon = GlyphLabel(44)
        self.temp = Label("--°", 44, W.Light, num=True)
        self.temp.setFixedHeight(56)
        self.desc = Label("", 14, W.Medium)
        self.city = Label("", 12, level=2)
        self.stamp = Label("", 10.5, level=3)
        self.hourly = Hourly()
        self.daily = Daily()
        self.daily.setFixedWidth(290)
        self.facts = Facts()
        self.alert = Label("", 11, color=QtGui.QColor("#ff9f0a"))
        self.alert.hide()
        self.pollutants = Label("", 10.5, level=3)
        self.pollutants.hide()
        self.life_title = Label("生活指数", 11, level=3)
        self.life_title.hide()
        self.life = LifeGrid()
        self.life.hide()
        self.hero = QtWidgets.QWidget()
        self.hero.setLayout(vbox(hbox(self.icon, self.temp, None, spacing=6),
                                 self.desc, self.city, self.stamp, spacing=2))
        self._hero_h = self.hero.sizeHint().height()
        self.daily.setFixedHeight(self._hero_h)
        self.body = QtWidgets.QWidget()
        self.body.setLayout(vbox(hbox((self.hero, 1), self.daily, spacing=16), self.facts, self.alert,
                                 self.pollutants, self.hourly, self.life_title, self.life, spacing=6))
        self.err = Label("", 13, level=2, align=Qt.AlignmentFlag.AlignCenter, wrap=True)
        self.err.hide()
        self.setLayout(vbox(head, (self.body, 1), self.err, spacing=8))
        ctx.weather.cities_updated.connect(self.set_cities)
        ctx.weather.city_added.connect(self._added)

    # ---------- 城市 ----------
    def set_data(self, d: dict):
        """兼容：第一个城市的数据 / 错误。"""
        if not d.get("ok") and not self.data:
            self.err.setText(d.get("error") or "暂无天气数据")
            self.err.show()
            self.body.hide()

    def set_cities(self, data: list):
        from ...services.weather import weather_glyph
        self.data = data
        names = [d.get("name", "?") for d in data]
        if [c.name for c in self.chips] != names:
            for c in self.chips:
                c.deleteLater()
            self.chips = []
            for i, n in enumerate(names):
                c = CityChip(i, n)
                c.clicked.connect(lambda i=i: self.select(i))
                c.right_clicked.connect(lambda i=i: self._menu(i))
                self.chip_row.addWidget(c)
                self.chips.append(c)
        for c, d in zip(self.chips, data):
            c.default = c.idx == 0
            c.setToolTip(("默认城市 · " if c.default else "") + "右键：修改 / 设为默认 / 删除")
            c.set_info(f"{round(d['temp'])}°" if d.get("temp") is not None else "--°", weather_glyph(d.get("icon")))
        self.sel = min(self.sel, max(0, len(data) - 1))
        self.select(self.sel)

    def select(self, i: int):
        self.sel = i
        for c in self.chips:
            c.selected = c.idx == i
            c.update()
        if not self.data:
            return
        self._show(self.data[i])

    def _show(self, d: dict):
        from ...services.weather import weather_glyph
        if d.get("temp") is None:
            self.err.setText(d.get("error") or "暂无数据")
            self.err.show()
            self.body.hide()
            if self.height != 300:
                self.set_height(300)
            return
        self.err.hide()
        self.body.show()
        self.msg.setText(d.get("error") or "")
        self.icon.set_glyph(weather_glyph(d.get("icon")))
        self.temp.setText(f"{round(d.get('temp', 0))}°")
        self.desc.setText(d.get("desc", ""))
        lo, hi = d.get("temp_min"), d.get("temp_max")
        rng = f"   最低 {round(lo)}° 最高 {round(hi)}°" if lo is not None and hi is not None else ""
        place = d.get("name") or d.get("city") or ""
        district = d.get("district")
        stem = str(district or "").rstrip("区县市")
        if district and stem and district not in place and stem not in place:
            place += f" · {district}"
        self.city.setText(f"{place}{rng}")
        tz = d.get("tz_offset", 0)
        vis = d.get("visibility")
        feels_like = d.get("feels_like")
        wind = d.get("wind_direction") or ""
        if d.get("wind"):
            wind = f"{wind} {d['wind']}".strip()
        elif d.get("wind_speed") is not None:
            wind = f"{wind} {d['wind_speed']:.0f}km/h".strip()
        humidity = d.get("humidity")
        pressure = d.get("pressure")
        uv = d.get("uv")
        today = (d.get("daily") or [{}])[0]
        if isinstance(today, dict) and today.get("uv") is not None:
            uv = today["uv"]
        uv_level = next((item.get("level") for item in (d.get("life_indices") or [])
                         if item.get("key") == "uv"), "")
        if uv is not None and uv_level:
            uv_text = f"{uv:g} {uv_level}"
        elif uv is not None:
            uv_text = f"{uv:g}"
        else:
            uv_text = uv_level or "--"
        aqi = d.get("aqi")
        aqi_text = "--"
        if aqi is not None:
            aqi_text = f"{round(aqi)} {d.get('aqi_category') or ''}".strip()
        precipitation = d.get("precipitation")
        self.facts.set_items([
            ("体感", f"{round(feels_like)}°" if feels_like is not None else "--"),
            ("湿度", f"{humidity}%" if humidity is not None else "--"),
            ("风", wind or "--"),
            ("气压", f"{pressure:.0f} hPa" if isinstance(pressure, (int, float)) else "--"),
            ("能见度", f"{vis / 1000:.0f} km" if vis is not None else "--"),
            ("云量", f"{d['clouds']}%" if d.get("clouds") is not None else "--"),
            ("紫外线", uv_text),
            ("降水", f"{precipitation:g} mm" if precipitation is not None else "--"),
            ("日出", _fmt_clock(d.get("sunrise"), tz)),
            ("日落", _fmt_clock(d.get("sunset"), tz)),
            ("空气质量", aqi_text, _aqi_color(aqi, d.get("aqi_level"))),
            ("更新", _clock_text(d.get("report_time")) or "--"),
        ])
        minutely = d.get("minutely") or ""
        self.stamp.setText(minutely)
        self.stamp.setVisible(bool(minutely))

        alerts = d.get("alerts") or []
        if alerts:
            first = alerts[0]
            title = first.get("title") or "".join(filter(None, (first.get("type"), first.get("level"), "预警")))
            line = f"⚠ {title}"
            when = _clock_text(first.get("publish_time"))
            if first.get("publisher"):
                line += f" · {first['publisher']}"
            if when:
                line += f" · {when} 发布"
            if len(alerts) > 1:
                line += f" · 共 {len(alerts)} 条"
            self.alert.setText(line)
            tips = []
            for item in alerts:
                tip = [item.get("title") or ""]
                if item.get("publish_time"):
                    tip.append(str(item["publish_time"]))
                if item.get("text"):
                    tip.append(str(item["text"]))
                if item.get("guidance"):
                    tip.append(" ".join(str(value) for value in item["guidance"]))
                tips.append("\n".join(part for part in tip if part))
            self.alert.setToolTip("\n\n".join(tips))
            self.alert.show()
        else:
            self.alert.hide()

        air = _air_text(d)
        self.pollutants.setText(air)
        self.pollutants.setVisible(bool(air))
        life = d.get("life_indices") or []
        self.life.set_items(life)
        self.life_title.setVisible(bool(life))
        self.life.setVisible(bool(life))

        self.hourly.tz = tz
        self.hourly.set_items(d.get("hourly") or [])
        self.daily.set_items(d.get("daily") or [])

        # 高度随实际显示的区块伸缩，避免留白或挤压。
        parts = [self._hero_h, self.facts.height()]
        if alerts:
            parts.append(self.alert.sizeHint().height())
        if air:
            parts.append(self.pollutants.sizeHint().height())
        parts.append(self.hourly.height())
        if life:
            parts += [self.life_title.sizeHint().height(), self.life.height()]
        self.set_height(38 + sum(parts) + 6 * (len(parts) - 1))

    def _menu(self, i: int):
        from .system import styled_menu
        m = styled_menu(self)
        m.addAction("修改城市").triggered.connect(lambda: self._edit_city(i))
        if i > 0:
            m.addAction("设为默认").triggered.connect(lambda: self._default(i))
        else:
            m.addAction("默认城市").setEnabled(False)
        a = m.addAction("删除该城市")
        a.setEnabled(len(self.data) > 1)
        a.triggered.connect(lambda: self._remove(i))
        if self.island:
            self.island.locks.add("menu")
            m.aboutToHide.connect(lambda: self.island.locks.discard("menu"))
        m.popup(QtGui.QCursor.pos())

    def _default(self, i):
        self.sel = 0
        self.ctx.weather.make_default(i)

    def _remove(self, i):
        if self.sel >= i and self.sel > 0:
            self.sel -= 1
        self.ctx.weather.remove_city(i)

    def _start_add(self):
        self._editing = None
        self.add_edit.setPlaceholderText("城市名")
        self.add_edit.clear()
        self._open_editor()

    def _edit_city(self, i: int):
        if not 0 <= i < len(self.data):
            return
        self._editing = i
        self.add_edit.setPlaceholderText("修改城市名")
        self.add_edit.setText(self.data[i].get("name") or self.data[i].get("city") or "")
        self._open_editor()

    def _open_editor(self):
        self.add_btn.hide()
        self.add_edit.show()
        self.want_focus()
        self.add_edit.setFocus()
        self.add_edit.selectAll()
        # 点到输入框以外的任何地方就取消（灵动岛内外都算）
        import ctypes
        u32 = ctypes.WinDLL("user32")
        st = {"armed": False}
        if getattr(self, "_add_watch", None) is None:
            self._add_watch = QtCore.QTimer(self, interval=25)

        def tick():
            if not self.add_edit.isVisible():
                self._add_watch.stop()
                return
            down = any(u32.GetAsyncKeyState(k) & 0x8000 for k in (0x01, 0x02))
            if not st["armed"]:
                st["armed"] = not down
                return
            if down:
                r = QtCore.QRect(self.add_edit.mapToGlobal(QtCore.QPoint(0, 0)), self.add_edit.size())
                if not r.contains(QtGui.QCursor.pos()):
                    self._end_add()
        try:
            self._add_watch.timeout.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._add_watch.timeout.connect(tick)
        self._add_watch.start()

    def _end_add(self):
        self.add_edit.hide()
        self.add_btn.show()
        self._editing = None
        if getattr(self, "_add_watch", None) is not None:
            self._add_watch.stop()
        if self.island is not None:
            self.island.release_focus()

    def _do_add(self):
        q = self.add_edit.text().strip()
        if q:
            if self._editing is not None:
                self._pending_sel = self._editing
                self.msg.setText(f"正在修改为 {q}…")
                self.ctx.weather.edit_city(self._editing, q)
            else:
                self._pending_sel = None
                self.msg.setText(f"正在查找 {q}…")
                self.ctx.weather.add_city(q)
        self._end_add()

    def _added(self, ok: bool, text: str):
        self.msg.setText(text)
        if ok:
            # 新增选中末尾；修改保持原位置。
            self.sel = self._pending_sel if self._pending_sel is not None else len(self.data)
        self._pending_sel = None
        QtCore.QTimer.singleShot(4000, lambda: self.msg.setText(""))

    def eventFilter(self, obj, ev):
        if obj is self.add_edit and ev.type() == QtCore.QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self._end_add()
            return True
        if obj is self.add_edit and ev.type() == QtCore.QEvent.Type.FocusOut and not self.add_edit.text().strip():
            self._end_add()
        return False

    def on_show(self):
        super().on_show()
        if self.data:
            self.select(self.sel)


class Facts(QtWidgets.QWidget, Themed):
    """详情网格：三行四列的小标签；第三项可选状态色圆点。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, str, QtGui.QColor | None]] = []
        self.setFixedHeight(72)
        self._hook_theme()

    def set_items(self, items):
        self.items = [(item[0], item[1], item[2] if len(item) > 2 else None) for item in items]
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        cols = 4
        cw = self.width() / cols
        rh = self.height() / 3
        # 每列标签宽度取该列最长标签，值紧跟其后，避免疏密不一。
        label_fm = QtGui.QFontMetrics(ui_font(11))
        widths = [0] * cols
        for i, (k, _v, _dot) in enumerate(self.items[:12]):
            widths[i % cols] = max(widths[i % cols], label_fm.horizontalAdvance(k))
        for i, (k, v, dot) in enumerate(self.items[:12]):
            x, y = (i % cols) * cw, (i // cols) * rh
            lw = widths[i % cols]
            p.setFont(ui_font(11))
            p.setPen(theme.text3())
            p.drawText(QtCore.QRectF(x, y, lw, rh), Qt.AlignmentFlag.AlignVCenter, k)
            tx = x + lw + 8
            if dot is not None:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(dot)
                p.drawEllipse(QtCore.QPointF(tx + 3, y + rh / 2), 3, 3)
                tx += 9
            p.setFont(ui_font(12, W.Medium, num=True))
            p.setPen(theme.text())
            fm = QtGui.QFontMetrics(p.font())
            text = fm.elidedText(str(v), Qt.TextElideMode.ElideRight, int(x + cw - 4 - tx))
            p.drawText(QtCore.QRectF(tx, y, cw - (tx - x) - 4, rh), Qt.AlignmentFlag.AlignVCenter, text)


class LifeGrid(QtWidgets.QWidget, Themed):
    """生活指数：六列网格；悬停查看建议。"""
    COLS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []
        self.setFixedHeight(54)
        self.setMouseTracking(True)
        self._hook_theme()

    def set_items(self, items):
        self.items = list(items or [])[:18]
        rows = max(1, (len(self.items) + self.COLS - 1) // self.COLS)
        self.setFixedHeight(rows * 18)
        self.setToolTip("")
        self.update()

    def paintEvent(self, e):
        if not self.items:
            return
        p = painter(self)
        cw = self.width() / self.COLS
        rh = self.height() / max(1, (len(self.items) + self.COLS - 1) // self.COLS)
        name_fm = QtGui.QFontMetrics(ui_font(10.5))
        for i, item in enumerate(self.items):
            x, y = (i % self.COLS) * cw, (i // self.COLS) * rh
            name = str(item.get("name", ""))
            nw = name_fm.horizontalAdvance(name)
            p.setFont(ui_font(10.5))
            p.setPen(theme.text3())
            p.drawText(QtCore.QRectF(x, y, nw, rh), Qt.AlignmentFlag.AlignVCenter, name)
            p.setFont(ui_font(10.5, W.Medium))
            p.setPen(theme.text2())
            fm = QtGui.QFontMetrics(p.font())
            lx = x + nw + 6
            text = fm.elidedText(str(item.get("level") or ""), Qt.TextElideMode.ElideRight, int(cw - nw - 18))
            p.drawText(QtCore.QRectF(lx, y, cw - nw - 18, rh), Qt.AlignmentFlag.AlignVCenter, text)

    def mouseMoveEvent(self, e):
        if not self.items:
            return
        cw = self.width() / self.COLS
        rows = max(1, (len(self.items) + self.COLS - 1) // self.COLS)
        rh = self.height() / rows
        index = int(e.position().y() // rh) * self.COLS + int(e.position().x() // cw)
        if 0 <= index < len(self.items):
            item = self.items[index]
            tip = f"{item.get('name', '')}：{item.get('level', '')}"
            if item.get("brief"):
                tip += f"（{item['brief']}）"
            if item.get("advice"):
                tip += f"\n{item['advice']}"
            self.setToolTip(tip)
        else:
            self.setToolTip("")

    def leaveEvent(self, e):
        self.setToolTip("")


_ = (cfg, clamp)
