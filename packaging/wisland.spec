# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包：一次分析，同时产出 onedir（免安装 zip 用）和 onefile（单文件 exe）。

GitHub Actions 在 windows-latest 上执行 `pyinstaller packaging/wisland.spec`。
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent          # packaging/ 的上一级 = 仓库根目录
ICON = str(ROOT / "wisland" / "assets" / "wisland.ico")

updater_analysis = Analysis(
    [str(ROOT / "wisland" / "core" / "apply_update.py")],
    pathex=[str(ROOT)],
)
updater = EXE(
    PYZ(updater_analysis.pure),
    updater_analysis.scripts,
    updater_analysis.binaries,
    updater_analysis.datas,
    [],
    name="WislandUpdater",
    upx=False,
    console=False,
    icon=ICON,
)

# 随包资源：天气图标字体 / 样式表 / 许可证。运行时按 _MEIPASS/wisland/assets 查找。
datas = [(str(ROOT / "wisland" / "assets"), "wisland/assets"), (updater.name, "wisland/assets")]
if release_info := os.environ.get("WISLAND_RELEASE_INFO"):
    datas.append((release_info, "wisland/services"))
binaries = []
hiddenimports = [
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.media.control",
    "winrt.windows.storage.streams",
    "winrt.windows.devices.radios",
    "winrt.windows.devices.bluetooth",
    "comtypes.gen",
]

# 这些包在运行时动态加载子模块或数据文件，静态分析容易漏掉。
for package in ("winrt", "sherpa_onnx", "soundcard", "unidic_lite", "pycaw"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(ROOT / "run.pyw")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# 免安装目录版：dist/Wisland/Wisland.exe + _internal/
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Wisland",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Wisland",
)

# 单文件版：dist/Wisland-onefile.exe
onefile = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Wisland-onefile",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON,
)
