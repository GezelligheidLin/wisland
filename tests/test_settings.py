"""设置窗口的交互回归测试，不修改用户配置、注册表或系统热键。"""
import ctypes
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6 import QtCore, QtTest, QtWidgets

from wisland.core.config import Config
from wisland.ui.island import Island, parse_hotkey
from wisland.ui.settings import SettingsWindow


class SettingsIsland(QtWidgets.QWidget):
    mode_changed = QtCore.Signal(str)
    passthrough_changed = QtCore.Signal(bool)
    auto_cycle_enabled = Island.auto_cycle_enabled
    _auto_cycle_interval = Island._auto_cycle_interval
    set_auto_cycle_enabled = Island.set_auto_cycle_enabled
    apply_auto_cycle = Island.apply_auto_cycle

    def __init__(self, config):
        super().__init__()
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.config = config
        self._auto_cycle_timer = QtCore.QTimer(self)
        self.apply_auto_cycle()
        self.pages = [SimpleNamespace(key="home", title="主页"), SimpleNamespace(key="msg", title="消息")]
        self.nav = SimpleNamespace(keys=lambda: self.visible_tabs)
        self.apply_visible_tabs()

    def apply_visible_tabs(self):
        selected = self.config.get("island.visible_tabs")
        self.visible_tabs = [page.key for page in self.pages if page.key in selected]

    def expand_mode(self):
        return self.config.get("island.expand_mode")

    def set_expand_mode(self, mode):
        self.config.set("island.expand_mode", mode)
        self.mode_changed.emit(mode)

    def passthrough(self):
        return self.config.get("island.passthrough")

    def set_passthrough(self, on):
        self.config.set("island.passthrough", on)
        self.passthrough_changed.emit(on)

    def capture_visible(self):
        return not self.config.get("island.exclude_from_capture")

    def set_capture_visible(self, on):
        self.config.set("island.exclude_from_capture", not on)

    def remote_mode(self):
        return self.config.get("island.remote_mode")

    def set_remote_mode(self, mode):
        self.config.set("island.remote_mode", mode)

    def screen(self):
        return QtWidgets.QApplication.primaryScreen()


class SettingsUpdates(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self._checking = False
        self.install_progress = None
        self.install_failed = False
        self.can_install = False
        self.error = False
        self.checked = False
        self.latest = None
        self.check_count = 0
        self.install = Mock()

    def check(self):
        self.check_count += 1
        self._checking = True
        self.changed.emit()


class _ConfigTests(unittest.TestCase):
    def start_patch(self, target, replacement):
        patcher = patch(target, replacement)
        self.addCleanup(patcher.stop)
        patcher.start()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.start_patch("wisland.core.config.CONFIG_PATH", Path(folder.name) / "config.json")
        self.config = Config()


class SettingsWindowTests(_ConfigTests):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        super().setUp()
        self.start_patch("wisland.ui.settings.cfg", self.config)
        self.start_patch("wisland.ui.island.cfg", self.config)
        self.island = SettingsIsland(self.config)
        self.addCleanup(self.island.deleteLater)
        self.set_startup = Mock()
        self.reload = Mock(side_effect=self.config.load)
        self.window = SettingsWindow(self.island, lambda: bool(self.set_startup.call_args),
                                     self.set_startup, self.reload)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.window.present()
        self.app.processEvents()

    def control(self, kind, name):
        return next(widget for widget in self.window.findChildren(kind) if widget.accessibleName() == name)

    def click(self, widget):
        self.assertTrue(widget.isVisible())
        QtTest.QTest.mouseClick(widget, QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def test_search_across_sections_and_clear(self):
        self.click(self.window._buttons["外观"])
        self.window._search.setText("贴边")
        self.assertTrue(self.control(QtWidgets.QPushButton, "贴边隐藏 / 恢复").isVisible())
        self.assertFalse(self.control(QtWidgets.QCheckBox, "可穿透").isVisible())
        self.window._search.setText("没有这项设置")
        self.assertTrue(self.window._empty.isVisible())
        self.window._search.clear()
        self.assertEqual(self.window._title.text(), "外观")
        self.assertTrue(self.control(QtWidgets.QComboBox, "主题").isVisible())

    def test_switch_changes_live_and_refreshes_from_island(self):
        switch = self.control(QtWidgets.QCheckBox, "可穿透")
        self.click(switch)
        self.assertTrue(Config().get("island.passthrough"))
        self.island.set_passthrough(False)
        self.assertFalse(switch.isChecked())
        self.click(self.control(QtWidgets.QCheckBox, "开机自启"))
        self.set_startup.assert_called_once_with(True)

    def test_theme_dropdown_applies_and_saves_choice(self):
        self.click(self.window._buttons["外观"])
        combo = self.control(QtWidgets.QComboBox, "主题")
        with patch("wisland.ui.settings.theme.set_mode") as apply_theme:
            combo.activated.emit(combo.findData("sky"))
            apply_theme.assert_called_once_with("sky")
        self.assertEqual(combo.currentData(), "sky")
        self.assertEqual(Config().get("island.theme"), "sky")

    def test_auto_cycle_switch_controls_timer_and_preserves_interval(self):
        self.click(self.window._buttons["外观"])
        switch = self.control(QtWidgets.QCheckBox, "自动轮换收起视图")
        timer = self.island._auto_cycle_timer
        self.assertTrue(switch.isChecked())
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), 30000)
        self.config.set("island.auto_cycle_s", 45)
        self.click(switch)
        self.assertFalse(timer.isActive())
        self.assertFalse(Config().get("island.auto_cycle_enabled"))
        self.click(switch)
        self.assertTrue(timer.isActive())
        self.assertEqual(timer.interval(), 45000)
        self.assertTrue(Config().get("island.auto_cycle_enabled"))

    def test_legacy_zero_interval_stays_off_until_enabled(self):
        self.config.set("island.auto_cycle_s", 0)
        self.island.apply_auto_cycle()
        self.window.refresh()
        self.click(self.window._buttons["外观"])
        switch = self.control(QtWidgets.QCheckBox, "自动轮换收起视图")
        self.assertFalse(switch.isChecked())
        self.assertFalse(self.island._auto_cycle_timer.isActive())
        self.click(switch)
        self.assertEqual(Config().get("island.auto_cycle_s"), 30)
        self.assertTrue(self.island._auto_cycle_timer.isActive())

    def test_window_is_independent_of_topmost_island(self):
        self.assertIsNone(self.window.parentWidget())
        self.assertEqual(self.window.windowType(), QtCore.Qt.WindowType.Window)
        self.assertFalse(self.window.windowFlags() & QtCore.Qt.WindowType.WindowStaysOnTopHint)

    def test_normal_window_can_cover_settings_on_windows(self):
        if self.app.platformName() != "windows":
            self.skipTest("需要 Windows 原生窗口")

        user32 = ctypes.WinDLL("user32")
        user32.GetWindow.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        user32.GetWindow.restype = ctypes.c_void_p
        user32.GetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int)
        settings = int(self.window.winId())
        self.assertFalse(user32.GetWindowLongW(settings, -20) & 0x00000008)  # WS_EX_TOPMOST
        self.assertFalse(user32.GetWindow(settings, 4))  # GW_OWNER

        other = QtWidgets.QWidget()
        self.addCleanup(other.deleteLater)
        self.addCleanup(other.close)
        other.setGeometry(self.window.geometry())
        other.show()
        other.raise_()
        other.activateWindow()
        self.app.processEvents()
        hwnd = settings
        while hwnd and hwnd != int(other.winId()):
            hwnd = user32.GetWindow(hwnd, 3)  # GW_HWNDPREV
        self.assertEqual(hwnd, int(other.winId()))

    def test_remote_mode_dropdown_applies_and_saves_choice(self):
        self.click(self.window._buttons["外观"])
        combo = self.control(QtWidgets.QComboBox, "远程模式")
        self.assertEqual(combo.currentData(), "auto")
        combo.activated.emit(combo.findData("on"))
        self.assertEqual(combo.currentData(), "on")
        self.assertEqual(Config().get("island.remote_mode"), "on")
        combo.activated.emit(combo.findData("off"))
        self.assertEqual(Config().get("island.remote_mode"), "off")

    def test_at_least_one_tab_is_saved(self):
        self.click(self.window._buttons["标签页"])
        home = self.control(QtWidgets.QCheckBox, "显示主页标签页")
        msg = self.control(QtWidgets.QCheckBox, "显示消息标签页")
        self.click(home)
        self.assertTrue(msg.isChecked())
        self.assertFalse(msg.isEnabled())
        self.click(msg)
        self.assertEqual(self.island.nav.keys(), ["msg"])
        self.assertEqual(Config().get("island.visible_tabs"), ["msg"])
        self.click(home)
        self.assertTrue(msg.isEnabled())
        self.assertEqual(self.island.nav.keys(), ["home", "msg"])

    def test_reopen_and_reload_refresh_the_display(self):
        self.window.close()
        self.config.set("island.expand_mode", "always")
        self.window.present()
        self.assertEqual(self.control(QtWidgets.QComboBox, "展开方式").currentData(), "always")
        self.click(self.window._buttons["高级"])
        self.click(self.control(QtWidgets.QPushButton, "重新加载配置"))
        self.reload.assert_called_once_with()

    def test_settings_save_uapis_key_to_dedicated_config_field(self):
        self.click(self.window._buttons["高级"])
        key = self.control(QtWidgets.QLineEdit, "UAPIs API Key")
        self.assertEqual(key.echoMode(), QtWidgets.QLineEdit.EchoMode.Password)
        key.setText("  uapi-test-key  ")
        self.click(self.control(QtWidgets.QPushButton, "保存UAPIs API Key"))
        self.assertEqual(Config().get("weather.uapi_key"), "uapi-test-key")

    def test_advanced_translation_settings_mask_and_save_fields(self):
        self.click(self.window._buttons["高级"])
        key = self.control(QtWidgets.QLineEdit, "翻译 API Key")
        model = self.control(QtWidgets.QLineEdit, "翻译模型")
        self.assertTrue(key.isVisible())
        self.assertEqual(key.echoMode(), QtWidgets.QLineEdit.EchoMode.Password)
        key.setText(" example-key ")
        model.setText("example-model")
        self.click(self.control(QtWidgets.QPushButton, "保存翻译 API Key"))
        self.click(self.control(QtWidgets.QPushButton, "保存翻译模型"))
        saved = Config()
        self.assertEqual(saved.get("translate.api_key"), "example-key")
        self.assertEqual(saved.get("translate.model"), "example-model")

    def test_translation_page_and_settings_share_language_selections(self):
        from wisland.ui.pages.tools import TranslatePage

        self.start_patch("wisland.ui.pages.tools.cfg", self.config)
        live = Mock()
        live.available.return_value = (True, "")
        page = TranslatePage(SimpleNamespace(live=live, learn=Mock(), translate=Mock(), actions=Mock()))
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.popup.deleteLater)
        self.click(self.window._buttons["高级"])
        source = self.control(QtWidgets.QComboBox, "原文语言")
        target = self.control(QtWidgets.QComboBox, "译文语言")
        source_button = page._language_buttons["translate.source_language"][0]
        target_button = page._language_buttons["translate.target_language"][0]
        self.assertEqual(source.currentData(), "auto")
        self.assertEqual(target.currentData(), "zh-Hans")
        with patch.object(QtWidgets.QMenu, "popup"):
            source_button.clicked.emit()
        menu = source_button.findChild(QtWidgets.QMenu)
        next(action for action in menu.actions() if action.text().strip() == "韩语").trigger()
        self.assertEqual(source.currentData(), "ko")
        self.assertIn("韩语", source_button.text)
        target.activated.emit(target.findData("en"))
        self.assertIn("英语", target_button.text)
        saved = Config()
        self.assertEqual(saved.get("translate.source_language"), "ko")
        self.assertEqual(saved.get("translate.target_language"), "en")

    def test_advanced_live_subtitle_settings_are_searchable_and_saved(self):
        self.window._search.setText("实时字幕 音频来源")
        source = self.control(QtWidgets.QComboBox, "音频来源")
        self.assertTrue(source.isVisible())
        source.activated.emit(source.findData("mic"))
        self.assertEqual(Config().get("live.audio.source"), "mic")

        self.window._search.setText("实时字幕 中文字幕")
        subtitles = self.control(QtWidgets.QCheckBox, "中文字幕")
        self.assertTrue(subtitles.isChecked())
        self.click(subtitles)
        self.assertFalse(Config().get("live.translate.enabled"))

    def test_passthrough_shows_as_expand_mode_and_turns_off_with_others(self):
        combo = self.control(QtWidgets.QComboBox, "展开方式")
        self.config.set("island.expand_mode", "click")
        self.config.set("island.passthrough", True)
        self.window.refresh()
        self.assertEqual(combo.currentData(), "pass")
        self.window._apply(self.window._set_mode, "click")
        self.assertFalse(self.island.passthrough())
        self.assertEqual(self.island.expand_mode(), "click")
        self.assertEqual(combo.currentData(), "click")
        self.window._apply(self.window._set_mode, "pass")
        self.assertTrue(self.island.passthrough())
        self.assertEqual(combo.currentData(), "pass")

    def test_version_and_update_actions_are_in_settings(self):
        from wisland.services.updates import InstalledRelease, Update

        updates = SettingsUpdates()
        window = SettingsWindow(self.island, lambda: False, Mock(), Mock(),
                                installed_release=InstalledRelease("1.2.3"), updates=updates)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.present()
        QtTest.QTest.mouseClick(window._buttons["版本与更新"], QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()
        control = lambda kind, name: next(
            widget for widget in window.findChildren(kind) if widget.accessibleName() == name)
        version = control(QtWidgets.QLabel, "当前安装版本号")
        status = control(QtWidgets.QLabel, "更新检查状态")
        self.assertEqual(version.text(), "v1.2.3")
        self.assertTrue(version.isVisible())
        self.assertEqual(window._title.text(), "版本与更新")
        self.assertEqual({card.category for card in window._cards if card.isVisible()}, {"版本与更新"})
        self.assertEqual(status.text(), "尚未检查")
        QtTest.QTest.mouseClick(control(QtWidgets.QPushButton, "立即检查更新"),
                                QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(updates.check_count, 1)
        self.assertEqual(status.text(), "正在检查更新…")
        updates._checking = False
        updates.checked = True
        updates.latest = Update("1.2.4", {"zip": "https://example.test/update.zip"})
        updates.changed.emit()
        self.assertEqual(status.text(), "发现新版本 v1.2.4")
        self.assertTrue(control(QtWidgets.QPushButton, "下载 ZIP").isVisible())
        self.assertFalse(control(QtWidgets.QPushButton, "下载 EXE").isVisible())
        self.assertTrue(window._update_install_button.isHidden())
        updates.can_install = True
        updates.changed.emit()
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Yes):
            self.click(window._update_install_button)
        updates.install.assert_called_once_with()
        updates.can_install = False
        updates.install_progress = 42
        updates.changed.emit()
        self.assertEqual(status.text(), "正在下载更新 42%")
        self.assertFalse(control(QtWidgets.QPushButton, "立即检查更新").isEnabled())


class TranslationSettingsTests(_ConfigTests):
    def test_translation_request_uses_selected_languages(self):
        import httpx

        from wisland.services.translate import TranslateService

        self.start_patch("wisland.services.translate.cfg", self.config)
        service = TranslateService()
        self.config.set("translate.api_key", "example-key")
        self.config.set("translate.source_language", "en")
        self.config.set("translate.target_language", "fr")
        requests = []

        def reply(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"Bonjour"}}]}\n\ndata: [DONE]\n')

        done = Mock()
        service.finished.connect(done)
        service._current = 1
        with httpx.Client(transport=httpx.MockTransport(reply)) as client, \
                patch.object(service, "_http", return_value=client), patch("wisland.services.translate.ui"):
            service._work(1, "Hello")
        self.assertIn("原文语言为英语", requests[0]["messages"][0]["content"])
        self.assertIn("翻译成法语", requests[0]["messages"][0]["content"])
        self.assertEqual(requests[0]["messages"][1]["content"], "Hello")
        done.assert_called_once_with(1, "Bonjour")

    def test_changing_import_path_refreshes_translation_credentials(self):
        from wisland.services.translate import TranslateService

        self.start_patch("wisland.services.translate.cfg", self.config)
        from wisland.core import config

        service = TranslateService()
        for label in ("first", "second"):
            source = config.CONFIG_PATH.with_name(f"{label}.json")
            source.write_text(json.dumps({"translate": {"api_key": f"example-{label}"}}), encoding="utf-8")
            self.config.set("translate.import_from", str(source))
            self.assertEqual(service._settings()[0], f"example-{label}")
        self.config.set("translate.import_from", "")
        self.assertFalse(service.has_key())


class HotkeySettingsTests(_ConfigTests):
    def setUp(self):
        super().setUp()
        self.start_patch("wisland.ui.island.cfg", self.config)
        self.island = SimpleNamespace(_registered_hotkeys={0xB000: "edge_hide"})
        self.island.register_hotkeys = Mock(side_effect=self.register)

    def register(self):
        # 模拟 Alt+F4 无法注册，其他合法组合可用。
        spec = self.config.get("hotkeys.edge_hide")
        available = parse_hotkey(spec) not in (None, parse_hotkey("Alt+F4"))
        self.island._registered_hotkeys = {0xB000: "edge_hide"} if available else {}

    def set_hotkey(self, value):
        Island.set_hotkey(self.island, "edge_hide", value)

    def test_saves_successful_change_and_can_disable(self):
        self.set_hotkey(" Ctrl+Shift+F8 ")
        self.assertEqual(Config().get("hotkeys.edge_hide"), "Ctrl+Shift+F8")
        self.set_hotkey("")
        self.assertFalse(self.island._registered_hotkeys)
        self.assertEqual(Config().get("hotkeys.edge_hide"), "")

    def test_rejects_invalid_and_duplicate_bindings(self):
        for spec in ("Ctrl++H", "Ctrl+F999", "Alt+J+K", "Ctrl+ß", "H", "Alt+Control+I"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                self.set_hotkey(spec)
        self.island.register_hotkeys.assert_not_called()
        self.assertEqual(Config().get("hotkeys.edge_hide"), "Ctrl+Alt+H")

    def test_failed_registration_leaves_previous_binding(self):
        with self.assertRaises(ValueError):
            self.set_hotkey("Alt+F4")
        self.assertEqual(self.island.register_hotkeys.call_count, 2)
        self.assertIn("edge_hide", self.island._registered_hotkeys.values())
        self.assertEqual(self.config.get("hotkeys.edge_hide"), "Ctrl+Alt+H")
        self.assertEqual(Config().get("hotkeys.edge_hide"), "Ctrl+Alt+H")
