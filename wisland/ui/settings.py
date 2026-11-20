"""Wisland 的独立设置窗口；改动复用灵动岛现有的设置入口。"""
from __future__ import annotations

import os

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.config import CONFIG_PATH, DATA_DIR, cfg
from .theme import G, THEME_NAMES, theme, ui_font
from .widgets import ChoiceBox, glyph_pixmap

Qt = QtCore.Qt


class _Switch(QtWidgets.QCheckBox):
    def __init__(self, label):
        super().__init__()
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 26)
        self.colors = {}

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        if self.hasFocus():
            p.setPen(QtGui.QColor(self.colors["focus"]))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QtCore.QRectF(1, 2, 38, 22), 11, 11)
        p.setOpacity(1 if self.isEnabled() else 0.48)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(self.colors["accent" if self.isChecked() else "track"]))
        p.drawRoundedRect(QtCore.QRectF(4, 4, 32, 18), 9, 9)
        p.setBrush(Qt.GlobalColor.white)
        p.drawEllipse(QtCore.QRectF(19 if self.isChecked() else 6, 6, 14, 14))
        p.end()


class _Card(QtWidgets.QWidget):
    """带标题的设置卡片；搜索时只保留匹配的行和对应分隔线。"""

    def __init__(self, category, title):
        super().__init__()
        self.category = category
        self.search_text = f"{category} {title}"
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        self._title = title
        self._caption = QtWidgets.QLabel(title, objectName="sectionTitle")
        outer.addWidget(self._caption)
        frame = QtWidgets.QFrame(objectName="card")
        outer.addWidget(frame)
        self._layout = QtWidgets.QVBoxLayout(frame)
        self._layout.setContentsMargins(16, 0, 16, 0)
        self._layout.setSpacing(0)
        self._rows = []

    def add_row(self, title, description, control, keywords=""):
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 11, 0, 11)
        layout.setSpacing(16)
        labels = QtWidgets.QVBoxLayout()
        labels.setSpacing(3)
        labels.addWidget(QtWidgets.QLabel(title, objectName="rowTitle"))
        desc = QtWidgets.QLabel(description, objectName="muted", wordWrap=True)
        labels.addWidget(desc)
        layout.addLayout(labels, 1)
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        if not control.accessibleName():
            control.setAccessibleName(title)
        rule = QtWidgets.QFrame(objectName="rule")
        rule.setFixedHeight(1)
        self._layout.addWidget(row)
        self._layout.addWidget(rule)
        self._rows.append((row, rule, f"{self.search_text} {title} {description} {keywords}".casefold()))

    def filter(self, terms: list[str]) -> bool:
        self._caption.setText(f"{self.category} · {self._title}" if terms else self._title)
        visible_rules = []
        for row, rule, text in self._rows:
            match = all(term in text for term in terms)
            row.setVisible(match)
            rule.setVisible(match)
            if match:
                visible_rules.append(rule)
        if visible_rules:
            visible_rules[-1].hide()
        return bool(visible_rules)


class SettingsWindow(QtWidgets.QWidget):
    """非模态设置窗口，可从菜单和齿轮复用同一个实例。"""

    CATEGORIES = {
        "常规": (G.GEAR, "管理灵动岛的启动和交互方式。"),
        "外观": (G.SUN, "调整灵动岛的主题和在截图中的显示方式。"),
        "标签页": (G.APPS, "顶栏标签等距排列，至少保留一项。隐藏待办后仍可从托盘打开，提醒继续运行。"),
        "快捷键": (G.BOLT, "为常用操作设置 Windows 全局快捷键。"),
        "待办": (G.TODO, "管理本机待办的提醒方式。"),
        "高级": (G.SLIDERS, "管理天气服务、配置文件和本地数据。"),
    }
    MODES = [("hover", "悬停展开"), ("always", "常驻展开"), ("never", "长关")]

    def __init__(self, island, autostart_enabled, set_autostart, reload_config):
        super().__init__()
        self._island = island
        self._autostart_enabled = autostart_enabled
        self._reload_config = reload_config
        self._section = "常规"
        self._cards = []
        self._buttons = {}
        self._switches = []
        self._bindings = []
        self._tab_switches = {}
        self._hotkey_buttons = {}

        self.setObjectName("settings")
        self.setWindowTitle("Wisland 设置")
        self.setMinimumSize(680, 460)
        self.resize(938, 660)
        self.setFont(ui_font(14))
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QtWidgets.QFrame(objectName="sidebar")
        side.setFixedWidth(204)
        outer.addWidget(side)
        sidebar = QtWidgets.QVBoxLayout(side)
        sidebar.setContentsMargins(12, 17, 12, 16)
        sidebar.setSpacing(4)
        sidebar.addWidget(QtWidgets.QLabel("设置", objectName="sideTitle"))
        sidebar.addSpacing(7)
        self._search = QtWidgets.QLineEdit(objectName="settingsSearch", placeholderText="搜索设置", clearButtonEnabled=True)
        self._search.setAccessibleName("搜索设置")
        self._search_icon = self._search.addAction(QtGui.QIcon(), QtWidgets.QLineEdit.ActionPosition.LeadingPosition)
        sidebar.addWidget(self._search)
        sidebar.addSpacing(12)
        sidebar.addWidget(QtWidgets.QLabel("偏好设置", objectName="sideCaption"))
        group = QtWidgets.QButtonGroup(self)
        for name in self.CATEGORIES:
            if name == "高级":
                sidebar.addSpacing(13)
                sidebar.addWidget(QtWidgets.QLabel("管理", objectName="sideCaption"))
            button = QtWidgets.QToolButton(objectName="nav", text=name, checkable=True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QtCore.QSize(17, 17))
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked, category=name: self._select(category))
            group.addButton(button)
            sidebar.addWidget(button)
            self._buttons[name] = button
        sidebar.addStretch()
        sidebar.addWidget(QtWidgets.QLabel("Wisland · 桌面灵动岛", objectName="sideCaption"))

        self._scroll = QtWidgets.QScrollArea(widgetResizable=True)
        self._scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll, 1)
        content = QtWidgets.QWidget(objectName="content")
        self._scroll.setWidget(content)
        self._body = QtWidgets.QVBoxLayout(content)
        self._body.setContentsMargins(38, 28, 38, 28)
        self._body.setSpacing(23)
        header = QtWidgets.QVBoxLayout()
        header.setSpacing(7)
        self._title = QtWidgets.QLabel(objectName="pageTitle")
        self._description = QtWidgets.QLabel(objectName="muted", wordWrap=True)
        header.addWidget(self._title)
        header.addWidget(self._description)
        self._body.addLayout(header)
        self._build_cards(set_autostart)
        self._empty = QtWidgets.QLabel("未找到匹配的设置，试试其他关键词。", objectName="muted", wordWrap=True)
        self._body.addWidget(self._empty)
        self._body.addStretch()

        self._search.textChanged.connect(self._update_view)
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Find, self, activated=self._search.setFocus)
        island.mode_changed.connect(self.refresh)
        island.passthrough_changed.connect(self.refresh)
        hints = QtGui.QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(self._apply_style)
        self._apply_style(hints.colorScheme())
        self._select("常规")
        self.refresh()
        self.setFocus()

    def present(self):
        """首次在灵动岛所在屏幕居中，之后保留用户移动和调整过的位置。"""
        if not self.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide):
            screen = self._island.screen().availableGeometry()
            self.resize(min(self.width(), screen.width() - 32), min(self.height(), screen.height() - 60))
            self.move(screen.center() - self.rect().center())
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.refresh()
        self.raise_()
        self.activateWindow()

    def refresh(self):
        """同步配置及主页快捷操作，不触发新的写入。"""
        for control, getter, display in self._bindings:
            with QtCore.QSignalBlocker(control):
                display(getter())
        visible = self._island.nav.keys()
        for key, switch in self._tab_switches.items():
            with QtCore.QSignalBlocker(switch):
                switch.setChecked(key in visible)
                switch.setEnabled(key not in visible or len(visible) > 1)
        for key, button in self._hotkey_buttons.items():
            button.setText(self._hotkey_text(key) or "未设置")

    def _card(self, category, title):
        card = _Card(category, title)
        self._cards.append(card)
        self._body.addWidget(card)
        return card

    def _switch(self, title):
        switch = _Switch(title)
        self._switches.append(switch)
        return switch

    def _toggle(self, card, title, description, getter, setter):
        switch = self._switch(title)
        card.add_row(title, description, switch)
        self._bindings.append((switch, getter, switch.setChecked))
        switch.toggled.connect(lambda on: self._apply(setter, on))

    def _choice(self, card, title, description, choices, getter, setter):
        combo = ChoiceBox()
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for value, label in choices:
            combo.addItem(label, value)
        card.add_row(title, description, combo, " ".join(label for _, label in choices))
        self._bindings.append((combo, getter, lambda value: combo.setCurrentIndex(max(0, combo.findData(value)))))
        combo.activated.connect(lambda index: self._apply(setter, combo.itemData(index)))

    def _action(self, card, title, description, label, callback):
        button = QtWidgets.QPushButton(label)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self._apply(callback))
        card.add_row(title, description, button)
        return button

    def _build_cards(self, set_autostart):
        island = self._island
        startup = self._card("常规", "启动")
        self._toggle(startup, "开机自启", "登录 Windows 时自动启动 Wisland。", self._autostart_enabled, set_autostart)
        behavior = self._card("常规", "交互")
        self._choice(behavior, "展开方式", "决定灵动岛是悬停展开、常驻展开，还是保持收起。",
                     self.MODES, island.expand_mode, island.set_expand_mode)
        self._toggle(behavior, "可穿透", "收起时让鼠标操作穿过灵动岛，仍可通过右键打开菜单。",
                     island.passthrough, island.set_passthrough)

        appearance = self._card("外观", "灵动岛")
        self._choice(appearance, "主题", "为灵动岛选择配色，或根据背后画面的亮度自动切换。",
                     THEME_NAMES, lambda: cfg.get("island.theme"), self._set_theme)
        self._toggle(appearance, "参与截图", "允许在系统截图和录屏中显示灵动岛。",
                     island.capture_visible, island.set_capture_visible)

        tabs = self._card("标签页", "显示标签页")
        descriptions = {
            "home": "播放控制、常用开关和应用启动器。", "msg": "查看应用提醒和消息记录。",
            "stock": "查看自选股票的行情与分时。", "weather": "查看天气和多日预报。",
            "ctl": "调节音量、亮度和系统开关。", "clip": "查看最近复制的文字和图片。",
            "trans": "翻译文字和剪贴板内容。",
        }
        for page in island.pages:
            key = page.key
            switch = self._switch(f"显示{page.title}标签页")
            self._tab_switches[key] = switch
            tabs.add_row(page.title, descriptions.get(key, "在展开面板中显示此标签。"), switch)
            switch.toggled.connect(lambda on, key=key: self._apply(self._set_tab_visible, key, on))

        hotkeys = self._card("快捷键", "全局快捷键")
        for key, title, description in (
            ("toggle", "展开 / 收起", "切换灵动岛的展开状态。"),
            ("edge_hide", "贴边隐藏 / 恢复", "把灵动岛收成屏幕边缘的一条细线，或从细线恢复。"),
            ("translate", "翻译剪贴板", "翻译剪贴板中的文字。"),
            ("clipboard", "打开剪贴板", "切换到剪贴板页面。"),
            ("todo_add", "快速添加待办", "打开待办窗口并开始输入标题。"),
        ):
            self._hotkey_buttons[key] = self._action(
                hotkeys, title, description, "未设置", lambda key=key, title=title: self._edit_hotkey(key, title))

        todos = self._card("待办", "提醒")
        self._toggle(todos, "弹出待办提醒", "灵动岛只提示数量。关闭后，仍可在待办清单查看到期提醒。",
                     lambda: bool(cfg.get("todo.popup", True)), lambda on: cfg.set("todo.popup", on))

        weather = self._card("高级", "天气服务")
        key_input = QtWidgets.QLineEdit()
        key_input.setPlaceholderText("留空使用免费额度")
        key_input.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        key_input.setFixedSize(250, 30)
        self._bindings.append((key_input, lambda: cfg.get("weather.uapi_key") or "", key_input.setText))
        key_row = QtWidgets.QWidget()
        key_layout = QtWidgets.QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        key_layout.addWidget(key_input)
        key_save = QtWidgets.QPushButton("保存")
        key_save.setFixedSize(64, 30)
        key_layout.addWidget(key_save)
        weather.add_row("UAPIs API Key", "可选；留空使用免费额度。Key 保存在本机 config.json。今日已有天气缓存时不会重复请求。",
                        key_row, "UAPIs 天气 API Key 天气密钥")
        key_save.clicked.connect(lambda: self._apply(self._save_weather_api_key, key_input.text()))

        files = self._card("高级", "配置文件")
        self._action(files, "编辑配置", "在默认编辑器中打开 config.json。", "打开文件",
                     lambda: os.startfile(str(CONFIG_PATH)))
        self._action(files, "重新加载配置", "重新读取 config.json；部分后台服务的设置需要重启。", "重新加载",
                     self._reload_config)
        data = self._card("高级", "本地数据")
        self._action(data, "数据目录", "在资源管理器中查看日志等本地文件。", "打开目录",
                     lambda: os.startfile(str(DATA_DIR)))

    def _select(self, category):
        self._section = category
        self._buttons[category].setChecked(True)
        with QtCore.QSignalBlocker(self._search):
            self._search.clear()
        self._update_view()

    def _update_view(self):
        terms = self._search.text().casefold().split()
        self._title.setText("搜索结果" if terms else self._section)
        self._description.setText("按名称和说明搜索所有设置。" if terms else self.CATEGORIES[self._section][1])
        matched = False
        for card in self._cards:
            visible = card.filter(terms) if terms else card.category == self._section and card.filter([])
            card.setVisible(visible)
            matched |= visible
        self._empty.setVisible(not matched)
        self._scroll.verticalScrollBar().setValue(0)

    def _apply(self, setter, *args):
        try:
            setter(*args)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法修改设置", str(exc))
        except OSError:
            QtWidgets.QMessageBox.warning(self, "操作失败", "无法完成操作，请检查文件或系统权限后重试。")
        finally:
            self.refresh()

    def _set_theme(self, mode):
        cfg.set("island.theme", mode)
        theme.set_mode(mode)

    @staticmethod
    def _save_weather_api_key(value):
        cfg.set("weather.uapi_key", str(value or "").strip())

    def _set_tab_visible(self, key, on):
        selected = set(self._island.nav.keys())
        if on:
            selected.add(key)
        else:
            selected.discard(key)
        if selected:
            cfg.set("island.visible_tabs", [page.key for page in self._island.pages if page.key in selected])
            self._island.apply_visible_tabs()

    @staticmethod
    def _hotkey_text(key):
        spec = cfg.get(f"hotkeys.{key}")
        return spec if isinstance(spec, str) else ""

    def _edit_hotkey(self, key, title):
        value, accepted = QtWidgets.QInputDialog.getText(
            self, title, "请输入组合键，例如 Ctrl+Alt+H（留空禁用）：", text=self._hotkey_text(key))
        if accepted:
            self._island.set_hotkey(key, value)

    def _apply_style(self, scheme):
        dark = scheme == Qt.ColorScheme.Dark
        c = dict(zip(
            ("surface", "side", "card", "border", "rule", "text", "muted", "fill", "hover", "accent", "track", "focus"),
            ("#191a1d", "#202125", "#1c1d21", "#36373d", "#33343a", "#f2f2f4", "#a9aab1", "#2d2e34",
             "#383a41", "#3384e8", "#575960", "#70afff") if dark else
            ("#fdfdfd", "#f6f6f8", "#ffffff", "#e6e6ea", "#ebebee", "#202228", "#656872", "#ebebee",
             "#e4e5e9", "#087bfa", "#b6b9c2", "#2878d1"),
        ))
        self.setStyleSheet(f"""
            QWidget {{ color: {c['text']}; }}
            QWidget#settings, QWidget#content, QScrollArea {{ background: {c['surface']}; }}
            QFrame#sidebar {{ background: {c['side']}; border-right: 1px solid {c['border']}; }}
            QLabel#sideTitle {{ font-size: 19px; font-weight: 650; padding-left: 5px; }}
            QLabel#sideCaption {{ color: {c['muted']}; font-size: 12px; padding: 3px 5px; }}
            QLineEdit#settingsSearch {{ background: {c['fill']}; border: 1px solid transparent;
                border-radius: 17px; min-height: 32px; padding: 0 7px; selection-background-color: {c['accent']}; selection-color: white; }}
            QToolButton#nav {{ background: transparent; border: 1px solid transparent; border-radius: 8px;
                text-align: left; min-height: 30px; padding: 0 7px; }}
            QToolButton#nav:hover, QToolButton#nav:checked {{ background: {c['fill']}; }}
            QToolButton#nav:focus, QLineEdit#settingsSearch:focus, QPushButton:focus, QComboBox:focus {{
                border-color: {c['focus']}; }}
            QLabel#pageTitle {{ font-size: 30px; font-weight: 650; }}
            QLabel#sectionTitle, QLabel#rowTitle {{ font-weight: 600; }}
            QLabel#muted {{ color: {c['muted']}; font-size: 13px; }}
            QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 14px; }}
            QFrame#rule {{ background: {c['rule']}; }}
            QPushButton, QComboBox {{ background: {c['card']}; border: 1px solid {c['border']};
                border-radius: 8px; font-size: 13px; min-height: 28px; padding: 0 10px; }}
            QPushButton:hover, QComboBox:hover {{ background: {c['fill']}; }}
            QPushButton:pressed {{ background: {c['hover']}; }}
            QComboBox {{ padding-right: 24px; }}
            QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;
                width: 24px; border: none; background: transparent; }}
            QComboBox::down-arrow {{ image: none; }}
            QComboBox QAbstractItemView {{ background: {c['card']}; selection-background-color: {c['fill']};
                selection-color: {c['text']}; border: 1px solid {c['border']}; }}
            QScrollBar:vertical {{ background: {c['surface']}; width: 9px; margin: 3px 1px; }}
            QScrollBar::handle:vertical {{ background: {c['track']}; border-radius: 3px; min-height: 28px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        for name, (glyph, _) in self.CATEGORIES.items():
            self._buttons[name].setIcon(QtGui.QIcon(glyph_pixmap(glyph, 17, QtGui.QColor(c["text"]))))
        self._search_icon.setIcon(QtGui.QIcon(glyph_pixmap(G.SEARCH, 16, QtGui.QColor(c["muted"]))))
        for switch in self._switches:
            switch.colors = c
            switch.update()
