# -*- coding: utf-8 -*-
"""后台检查更新并向设置窗口提供检查状态。"""
from PySide6 import QtCore

from ..core import bg
from ..services.updates import InstalledRelease, Update, check_for_update


class UpdateNotifications(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, installed: InstalledRelease, parent=None):
        super().__init__(parent)
        self.installed = installed
        self.latest: Update | None = None
        self._checking = False
        self.error = False
        self.checked = False
        self._timer = QtCore.QTimer(self, interval=24 * 60 * 60 * 1000, timeout=self.check)
        self._timer.start()
        QtCore.QTimer.singleShot(5000, self.check)

    def check(self):
        """检查发布状态，设置页通过 changed 信号刷新显示。"""
        if self._checking:
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
