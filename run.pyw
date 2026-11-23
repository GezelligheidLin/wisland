# -*- coding: utf-8 -*-
"""双击运行（无控制台）：用 .venv\\Scripts\\pythonw.exe run.pyw"""
import os
import sys
import traceback

FROZEN = bool(getattr(sys, "frozen", False))
# 打包后的 exe 以自身所在目录为根：config.json / data / models 都跟着 exe 走，复制文件夹即可迁移。
ROOT = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))


def _sanitize_proxy_env():
    """httpx 0.28 把 NO_PROXY 按 URL 解析，Windows 常写入 [::1] 这类带括号的 IPv6，
    会被当作非法端口，导致新建 httpx.Client 时直接抛 InvalidURL。"""
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        parts = [part for part in raw.split(",") if "[" not in part and "]" not in part]
        if len(parts) != len(raw.split(",")):
            os.environ[name] = ",".join(parts) or "localhost,127.0.0.1"


_sanitize_proxy_env()


def _relaunch_windowless():
    """uv 创建的 .venv 里 Scripts\\pythonw.exe 其实是控制台程序，直接用它启动（例如旧的开机自启项、
    电脑管家恢复的启动项）会弹出终端窗口，关掉窗口灵动岛也会退出。
    检测到自己挂在控制台上时，改用基础解释器的 pythonw.exe 重新启动自己并立即退出。"""
    if FROZEN or os.environ.get("WISLAND_CONSOLE") or os.environ.get("WISLAND_RELAUNCHED"):
        return
    try:
        import ctypes
        if not ctypes.windll.kernel32.GetConsoleWindow():
            return
        import subprocess
        base = os.path.dirname(getattr(sys, "_base_executable", "") or sys.executable)
        pyw = os.path.join(base, "pythonw.exe")
        if not os.path.isfile(pyw):
            return
        env = dict(os.environ, WISLAND_RELAUNCHED="1")
        venv_py = os.path.join(sys.prefix, "Scripts", "python.exe")
        if sys.prefix != sys.base_prefix and os.path.isfile(venv_py):
            env["__PYVENV_LAUNCHER__"] = venv_py
        subprocess.Popen([pyw, os.path.abspath(__file__)] + sys.argv[1:], cwd=ROOT, env=env,
                         creationflags=0x00000008 | 0x00000200,   # DETACHED_PROCESS | NEW_PROCESS_GROUP
                         close_fds=True)
    except Exception:
        return
    os._exit(0)


_relaunch_windowless()
sys.path.insert(0, ROOT)
os.chdir(ROOT)

class _Tee:
    """日志始终写入 data/wisland.log；有控制台时同时输出到控制台。"""
    def __init__(self, *streams):
        self.streams = [x for x in streams if x is not None]

    def write(self, s):
        for x in self.streams:
            try:
                x.write(s)
            except Exception:
                pass

    def flush(self):
        for x in self.streams:
            try:
                x.flush()
            except Exception:
                pass


os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
_log = open(os.path.join(ROOT, "data", "wisland.log"), "a", encoding="utf-8", buffering=1)
sys.stdout = _Tee(sys.stdout, _log)
sys.stderr = _Tee(sys.stderr, _log)


def _self_check() -> int:
    """打包冒烟检查（GitHub Actions 用）：不显示界面，验证 Qt、内置资产和动态依赖是否齐全。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = os.path.join(ROOT, "data", "selfcheck.txt")
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication([])          # noqa: F841  Qt 插件与离屏平台可用性
        import comtypes                            # noqa: F401  pycaw 依赖
        import fugashi                             # noqa: F401
        import genanki                             # noqa: F401
        import numpy                               # noqa: F401
        import sherpa_onnx                         # noqa: F401  实时字幕
        import soundcard                           # noqa: F401
        import winrt.windows.devices.bluetooth     # noqa: F401
        import winrt.windows.devices.radios        # noqa: F401
        import winrt.windows.media.control         # noqa: F401
        from wisland.services import media, system  # noqa: F401
        from wisland.services.install_update import package_kind
        from wisland.services.updates import InstalledRelease
        from wisland.services.weather import weather_glyph
        if expected_version := os.environ.get("WISLAND_EXPECT_VERSION"):
            installed = InstalledRelease.load()
            if installed is None or installed.version != expected_version:
                raise RuntimeError("安装包内的版本信息无效")
            if FROZEN and package_kind() is None:
                raise RuntimeError("独立更新器没有打进安装包")
        glyph, family = weather_glyph("100")
        if not glyph or family == "Segoe Fluent Icons":
            raise RuntimeError("QWeather Icons 资产没有打进 exe")
        text = f"ok: {ROOT}"
    except Exception:
        traceback.print_exc()
        try:
            with open(result, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            pass
        return 1
    try:
        with open(result, "w", encoding="utf-8") as f:
            f.write(text)
        print(text)
    except OSError:
        pass
    return 0


if "--self-check" in sys.argv:
    sys.exit(_self_check())

try:
    from wisland.app import main
    sys.exit(main())
except SystemExit:
    raise
except ImportError as e:
    traceback.print_exc()
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        None, f"缺少依赖：{e}\n\n当前 Python：{sys.executable}\n\n请双击项目文件夹里的 setup.bat，"
              "它会把依赖安装进 .venv；装完后再双击 wisland.vbs 启动。", "wisland 启动失败", 0x10)
    sys.exit(1)
except Exception:
    traceback.print_exc()
    raise
