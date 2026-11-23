# -*- coding: utf-8 -*-
"""主题：浅色 / 深色毛玻璃配色、字体、图标码位。auto 模式按身后画面亮度自动切换。"""
from __future__ import annotations

from PySide6 import QtCore, QtGui

UI_FAMILIES = ["HarmonyOS Sans", "MiSans", "Segoe UI Variable Text", "Microsoft YaHei UI", "Segoe UI"]
NUM_FAMILIES = ["HarmonyOS Sans", "Segoe UI Variable Display", "Segoe UI"]
ICON_FAMILIES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]


class G:
    """Segoe Fluent Icons 码位。"""
    HOME = "\uE80F"
    MESSAGE = "\uE8BD"
    MAIL = "\uE715"
    STOCK = "\uEAFC"
    SUN = "\uE706"
    CLOUD = "\uE753"
    SLIDERS = "\uE9E9"
    WINDOWS = "\uE7C4"
    CPU = "\uE950"
    PULSE = "\uE9D9"
    CLIP = "\uE77F"
    COPY = "\uE8C8"
    TRANSLATE = "\uF2B7"
    VOLUME = "\uE767"
    VOLUME_LOW = "\uE995"
    MUTE = "\uE74F"
    WIFI = "\uE701"
    BT = "\uE702"
    GLOBE = "\uE774"
    SNIP = "\uE924"
    PLAY = "\uE768"
    PAUSE = "\uE769"
    PREV = "\uE892"
    NEXT = "\uE893"
    PIN = "\uE718"
    UNPIN = "\uE77A"
    CLOSE = "\uE711"
    REFRESH = "\uE72C"
    POWER = "\uE7E8"
    SPEED = "\uEC4A"
    BOLT = "\uE945"
    OPEN = "\uE8A7"
    APPS = "\uECA5"
    GEAR = "\uE713"
    IMAGE = "\uE91B"
    CHECK = "\uE73E"
    TODO = "\uE73A"
    BELL = "\uEA8F"
    CHAT = "\uE8F2"
    MUSIC = "\uE8D6"
    MONITOR = "\uE7F4"
    TRASH = "\uE74D"
    UP = "\uE74A"
    DOWN = "\uE74B"
    ADD = "\uE710"
    POINTER = "\uE8B0"
    TOUCH = "\uE7C9"
    EYE = "\uE890"
    EYE_OFF = "\uED1A"
    SEARCH = "\uE721"
    LIVE = "\uE720"   # 麦克风


def ui_font(px: float, weight: QtGui.QFont.Weight = QtGui.QFont.Weight.Normal, num: bool = False) -> QtGui.QFont:
    f = QtGui.QFont()
    f.setFamilies(NUM_FAMILIES + UI_FAMILIES if num else UI_FAMILIES)
    f.setPixelSize(max(1, round(px)))
    f.setWeight(weight)
    f.setHintingPreference(QtGui.QFont.HintingPreference.PreferNoHinting)
    return f


def icon_font(px: float) -> QtGui.QFont:
    f = QtGui.QFont()
    f.setFamilies(ICON_FAMILIES)
    f.setPixelSize(max(1, round(px)))
    return f


W = QtGui.QFont.Weight


# 每个主题一套色板。文字色 text + 次级 / 弱化透明度 t2 / t3，均按各自玻璃底色单独调过对比度。
PALETTES = {
    "dark": {
        "dark": True, "text": (255, 255, 255), "t2": 0.66, "t3": 0.45,
        "tint": (28, 28, 34, 120), "card": (255, 255, 255, 26), "hover": (255, 255, 255, 30),
        "pill": (255, 255, 255, 52), "track": (255, 255, 255, 34), "fill": (255, 255, 255, 235),
        "on": "#3b82f6", "up": "#ff5a52", "down": "#35c98e",
        "rim1": (255, 255, 255, 60), "rim2": (255, 255, 255, 14), "outline": (0, 0, 0, 70),
        "menu": ("rgba(36,36,40,240)", "white", "rgba(255,255,255,40)"),
    },
    # 浅色：玻璃底做得更"实"（不透出身后的深色），文字更深，保证股票 / 天气等小字可读
    "light": {
        "dark": False, "text": (16, 18, 24), "t2": 0.74, "t3": 0.56,
        "tint": (238, 240, 245, 205), "card": (255, 255, 255, 150), "hover": (0, 0, 0, 14),
        "pill": (255, 255, 255, 250), "track": (0, 0, 0, 30), "fill": (255, 255, 255, 255),
        "on": "#2f6fe0", "up": "#d42c25", "down": "#08875a",
        "rim1": (255, 255, 255, 200), "rim2": (255, 255, 255, 60), "outline": (0, 0, 0, 40),
        "menu": ("rgba(248,248,250,245)", "#14161c", "rgba(0,0,0,18)"),
    },
    # 胭脂撞色：墨黑玻璃 + 胭脂粉 FF7CA2 强调色，正文白色
    "rouge": {
        "dark": True, "text": (255, 255, 255), "t2": 0.70, "t3": 0.50,
        "tint": (0, 0, 0, 205), "card": (255, 255, 255, 24), "hover": (255, 255, 255, 34),
        "pill": (255, 124, 162, 235), "pill_fg": "#000000", "track": (255, 255, 255, 36), "fill": "#FF7CA2",
        "on": "#FF7CA2", "on_fg": "#000000", "up": "#FF7CA2", "down": "#5fe0b0",
        "rim1": (255, 124, 162, 170), "rim2": (255, 124, 162, 40), "outline": (0, 0, 0, 140),
        "menu": ("rgba(8,8,10,245)", "white", "rgba(255,124,162,90)"),
    },
    # 晴空渐变：晴空亮蓝 32ABFD → 柔白浅杏 F7EFE1，正文深海军蓝（两端对比度都足够）
    "sky": {
        "dark": False, "text": (12, 32, 56), "t2": 0.78, "t3": 0.60,
        "tint": (50, 171, 253, 215), "tint2": (247, 239, 225, 230),
        "card": (255, 255, 255, 105), "hover": (255, 255, 255, 120),
        "pill": (255, 255, 255, 190), "track": (12, 32, 56, 34), "fill": (255, 255, 255, 250),
        "on": "#1667c9", "up": "#d42c25", "down": "#06805a",
        "rim1": (255, 255, 255, 210), "rim2": (255, 255, 255, 70), "outline": (12, 32, 56, 60),
        "menu": ("rgba(247,239,225,248)", "#0c2038", "rgba(50,171,253,70)"),
    },
}

THEME_NAMES = [("auto", "跟随背景自动"), ("light", "浅色毛玻璃"), ("dark", "深色毛玻璃"),
               ("rouge", "胭脂撞色"), ("sky", "晴空渐变")]

THEME_TRANSITION_MS = 260
THEME_EASING = QtCore.QEasingCurve(QtCore.QEasingCurve.Type.InOutCubic)


class Theme(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, mode: str = "auto"):
        super().__init__()
        self.mode = mode
        self._lum = 0.5
        self.pal_name = None
        self._colors: dict[str, QtGui.QColor] = {}
        self._color_from: dict[str, QtGui.QColor] = {}
        self._color_to: dict[str, QtGui.QColor] = {}
        self._transition = QtCore.QVariantAnimation(self)
        self._transition.setDuration(THEME_TRANSITION_MS)
        self._transition.setStartValue(0.0)
        self._transition.setEndValue(1.0)
        self._transition.setEasingCurve(THEME_EASING)
        self._transition.valueChanged.connect(self._animate_colors)
        self._apply(mode if mode in PALETTES else "dark")

    # --- 自动切换（带滞回，避免在中间亮度来回跳） ---
    def feed_luminance(self, lum: float):
        """同一帧背景采样直接决定主题，用明暗阈值滞回防抖，避免二次渐变。"""
        self._lum = max(0.0, min(1.0, lum))
        if self.mode != "auto":
            return
        elif self.dark and self._lum > 0.64:
            want = False
        elif not self.dark and self._lum < 0.42:
            want = True
        else:
            want = self.dark
        if want == self.dark:
            return
        self._apply("dark" if want else "light")

    def set_mode(self, mode: str):
        self.mode = mode
        pal = mode if mode in PALETTES else ("dark" if self.dark else "light")
        self._apply(pal)

    def _apply(self, pal: str):
        """从当前显示颜色过渡到目标色板，连续换主题时不回跳到旧色板。"""
        if pal == self.pal_name:
            return
        initial = self.pal_name is None
        self._transition.stop()
        self.pal_name = pal
        self.pal = PALETTES[pal]
        self.dark = self.pal["dark"]
        colors = {key: QtGui.QColor(*value) if isinstance(value, tuple) else QtGui.QColor(value)
                  for key, value in self.pal.items() if key not in ("dark", "t2", "t3", "menu")}
        colors["tint2"] = QtGui.QColor(colors.get("tint2", colors["tint"]))
        colors["pill_fg"] = QtGui.QColor(colors.get("pill_fg", colors["text"]))
        colors["on_fg"] = QtGui.QColor(colors.get("on_fg", QtGui.QColor("white")))
        colors["glass_gloss"] = QtGui.QColor(255, 255, 255, 14 if self.dark else 26)
        colors["edge_glow"] = QtGui.QColor(255, 255, 255, 55 if self.dark else 80)
        for key, factor in (("_text2", self.pal["t2"]), ("_text3", self.pal["t3"])):
            colors[key] = QtGui.QColor(colors["text"])
            colors[key].setAlphaF(colors[key].alphaF() * factor)
        if initial:
            self._colors = colors
            self.changed.emit()
            return
        self._color_from = {key: QtGui.QColor(value) for key, value in self._colors.items()}
        self._color_to = colors
        self._transition.start()

    def _animate_colors(self, value):
        """逐帧混合颜色与透明度，通知面板和子控件同步重绘。"""
        progress = float(value)
        for key, target in self._color_to.items():
            source = self._color_from[key]
            channels = [a + (b - a) * progress for a, b in zip(source.getRgbF(), target.getRgbF())]
            self._colors[key] = QtGui.QColor.fromRgbF(*channels)
        self.changed.emit()

    # --- 颜色（按当前主题的色板） ---
    def _c(self, key: str, a: float = 1.0) -> QtGui.QColor:
        c = QtGui.QColor(self._colors[key])
        c.setAlphaF(max(0.0, min(1.0, c.alphaF() * a)))
        return c

    def text(self, a: float = 1.0) -> QtGui.QColor:
        return self._c("text", a)

    def text2(self, a: float = 1.0) -> QtGui.QColor:
        return self._c("_text2", a)

    def text3(self, a: float = 1.0) -> QtGui.QColor:
        return self._c("_text3", a)

    def tint(self) -> QtGui.QColor:
        return self._c("tint")

    def glass_gloss(self) -> QtGui.QColor:
        return self._c("glass_gloss")

    def edge_glow(self) -> QtGui.QColor:
        return self._c("edge_glow")

    def tint_brush(self, rect: QtCore.QRectF) -> QtGui.QBrush:
        """玻璃色调：渐变主题用纵向渐变，其余为纯色。"""
        g2 = self._c("tint2")
        if g2 == self.tint():
            return QtGui.QBrush(self.tint())
        g = QtGui.QLinearGradient(rect.left(), rect.top(), rect.right() * 0.25 + rect.left() * 0.75, rect.bottom())
        g.setColorAt(0, self._c("tint"))
        g.setColorAt(1, g2)
        return QtGui.QBrush(g)

    def card(self, a: float = 1.0) -> QtGui.QColor:
        return self._c("card", a)

    def hover(self) -> QtGui.QColor:
        return self._c("hover")

    def active_pill(self) -> QtGui.QColor:
        """导航当前项的圆角底。"""
        return self._c("pill")

    def pill_text(self, a: float = 1.0) -> QtGui.QColor:
        """当前项（亮色底块）上的文字颜色。"""
        return self._c("pill_fg", a)

    def track(self) -> QtGui.QColor:
        return self._c("track")

    def fill(self) -> QtGui.QColor:
        """滑块填充。"""
        return self._c("fill")

    @property
    def accent(self) -> QtGui.QColor:
        return self._c("on")

    def on_color(self) -> QtGui.QColor:
        return self._c("on")

    def on_fg(self, a: float = 1.0) -> QtGui.QColor:
        return self._c("on_fg", a)

    def up(self) -> QtGui.QColor:       # A 股习惯：红涨
        return self._c("up")

    def down(self) -> QtGui.QColor:     # 绿跌
        return self._c("down")

    def rim_top(self) -> QtGui.QColor:
        return self._c("rim1")

    def rim_bottom(self) -> QtGui.QColor:
        return self._c("rim2")

    def outline(self) -> QtGui.QColor:
        return self._c("outline")

    def menu_qss(self) -> tuple[str, str, str]:
        return self.pal["menu"]

    def qss_color(self, c: QtGui.QColor) -> str:
        return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


KIND_COLORS = {
    "qq": QtGui.QColor("#12b7f5"),
    "wechat": QtGui.QColor("#07c160"),
    "dingtalk": QtGui.QColor("#0089ff"),
    "mail": QtGui.QColor("#ff9f0a"),
    "stock": QtGui.QColor("#ff5a52"),
    "other": QtGui.QColor("#8e8e93"),
}

theme = Theme()
