# -*- coding: utf-8 -*-
"""后台检查更新并向设置窗口提供检查状态。"""
from PySide6 import QtCore

from ..core import bg
from ..core.apply_update import APPLY, clean_update_stage
from ..services.install_update import launch_updater, package_kind, prepare_update
from ..services.updates import InstalledRelease, Update, check_for_update


class UpdateNotifications(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, installed: InstalledRelease, parent=None):
        super().__init__(parent)
        self.installed = installed
        self.latest: Update | None = None
        self._package_kind = package_kind()
        self.install_progress: int | None = None
        self.install_failed = False
        self._checking = False
        self.error = False
        self.checked = False
        self._timer = QtCore.QTimer(self, interval=24 * 60 * 60 * 1000, timeout=self.check)
        self._timer.start()
        QtCore.QTimer.singleShot(5000, self.check)

    def check(self):
        """检查发布状态，设置页通过 changed 信号刷新显示。"""
        if self._checking or self.install_progress is not None:
            return
        self._checking = True
        self.error = False
        self.changed.emit()
        bg.run_bg(check_for_update, self.installed, on_done=self._finished, on_error=self._failed)

    def _finished(self, update: Update | None):
        self._checking = False
        self.latest = update
        self.error = False
        self.checked = True
        self.changed.emit()

    def _failed(self, _error: Exception):
        self._checking = False
        self.error = True
        self.checked = True
        self.changed.emit()

    @property
    def can_install(self) -> bool:
        return bool(self.latest and self._package_kind in self.latest.packages and not self._checking
                    and not self.error and self.install_progress is None)

    def install(self):
        """下载并验证当前包型，交给独立更新器后走正常退出流程。"""
        if not self.can_install:
            return
        self.install_failed = False
        self._set_progress(0)
        kind = self._package_kind
        bg.run_bg(prepare_update, self.latest, kind, lambda value: bg.ui(lambda: self._set_progress(value)),
                  on_done=lambda stage: self._prepared(stage, kind), on_error=self._installation_failed)

    def _set_progress(self, value):
        self.install_progress = value
        self.changed.emit()

    def _prepared(self, stage, kind):
        def fail(error):
            bg.run_bg(clean_update_stage, stage, stage.parent)
            self._installation_failed(error)

        def hand_off(_result):
            try:
                (stage / APPLY).touch(exist_ok=False)
            except OSError as error:
                fail(error)
                return
            QtCore.QCoreApplication.quit()

        bg.run_bg(launch_updater, stage, kind, on_done=hand_off, on_error=fail)

    def _installation_failed(self, _error):
        self.install_progress = None
        self.install_failed = True
        self.changed.emit()
