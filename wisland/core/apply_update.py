"""独立 Windows 更新器。等待主程序退出后替换便携包内的程序文件。"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

UPDATER_NAME = "WislandUpdater.exe"
STAGE_PREFIX = ".wisland-update-"
PAYLOAD = "payload"
PORTABLE_EXE = "Wisland.exe"
RUNTIME = "_internal"
STAGED_OK = "prepared"
READY = "ready"
APPLY = "apply"
COMPLETED = "completed"
STAGE_ENV = "WISLAND_UPDATE_STAGE"
_STAGE_NAME = re.compile(re.escape(STAGE_PREFIX) + r"[a-z0-9_]{8}")


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def valid_stage(stage: Path, data_dir: Path) -> bool:
    """更新目录须是数据目录下由本程序创建的普通子目录。"""
    return bool(_STAGE_NAME.fullmatch(stage.name) and stage.parent == data_dir
                and stage.is_dir() and not _is_reparse_point(stage)
                and stage.resolve().parent == data_dir.resolve())


@contextmanager
def update_gate(data_dir: Path, timeout: float = 0):
    """替换期间禁止启动同目录下的另一个 Wisland 进程。"""
    import msvcrt

    with (data_dir / "wisland-update.lock").open("a+b") as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _move(source: Path, destination: Path):
    # 单文件打包还有一个引导进程，主进程结束后需等它释放 EXE。
    deadline = time.monotonic() + 10
    while True:
        try:
            source.replace(destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.15)


class UpdateRolledBack(RuntimeError):
    pass


def replace_application(stage: Path, target: Path, kind: str, restart):
    """原地替换当前包型的程序文件，并在替换或重新启动失败时回退。"""
    if kind not in {"exe", "zip"} or target.suffix.lower() != ".exe":
        raise ValueError("更新包与运行方式不匹配")
    payload = stage / PAYLOAD
    replacements = [(payload / PORTABLE_EXE, target)]
    if kind == "zip":
        replacements.append((payload / RUNTIME, target.parent / RUNTIME))
    for staged, current in replacements:
        if not staged.exists() or not current.exists() or _is_reparse_point(staged) or _is_reparse_point(current):
            raise ValueError("程序文件缺失或链接到其他目录")

    backup = stage / "backup"
    backup.mkdir()
    moved = []
    try:
        for staged, current in replacements:
            previous = backup / current.name
            _move(current, previous)
            moved.append((previous, current))
            _move(staged, current)
            moved.append((current, staged))
        (stage / COMPLETED).touch(exist_ok=False)
        restart()
    except Exception as error:
        (stage / COMPLETED).unlink(missing_ok=True)
        try:
            for source, destination in reversed(moved):
                _move(source, destination)
        except OSError as recovery_error:
            raise RuntimeError(f"无法恢复旧版，备份目录：{backup}") from recovery_error
        raise UpdateRolledBack("已恢复旧版程序") from error


def clean_update_stage(stage: Path, data_dir: Path, *, completed_only: bool = False):
    """清理当前更新的暂存目录，兼容更新器 EXE 句柄尚未释放的情况。"""
    if not valid_stage(stage, data_dir) or (completed_only and not (stage / COMPLETED).is_file()):
        return
    for _ in range(24):
        try:
            shutil.rmtree(stage)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.25)


def _wait_for_exit(pid: int, stage: Path):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        (stage / READY).touch(exist_ok=False)
        deadline = time.monotonic() + 30
        while not (stage / APPLY).is_file():
            if time.monotonic() >= deadline or kernel32.WaitForSingleObject(handle, 100) != 258:
                raise RuntimeError("主程序未确认交接更新")
        if kernel32.WaitForSingleObject(handle, 60_000) != 0:
            raise RuntimeError("等待 Wisland 退出超时")
    finally:
        kernel32.CloseHandle(handle)


def _restart(target: Path, stage: Path):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1", **{STAGE_ENV: stage.name})
    subprocess.Popen([str(target), "--open-settings"], cwd=target.parent, env=env,
                     creationflags=subprocess.CREATE_NO_WINDOW)


def main(stage: Path, exe_name: str, kind: str, pid: int):
    data_dir = stage.parent
    if (data_dir.name != "data" or not valid_stage(stage, data_dir) or Path(exe_name).name != exe_name
            or not (stage / STAGED_OK).is_file()):
        raise ValueError("更新暂存目录或程序路径无效")
    target = data_dir.parent / exe_name
    with update_gate(data_dir):
        _wait_for_exit(pid, stage)
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            replace_application(stage, target, kind, lambda: _restart(target, stage))
        except UpdateRolledBack:
            (stage / COMPLETED).touch(exist_ok=True)
            _restart(target, stage)
            raise


if __name__ == "__main__":
    try:
        stage_path, executable_name, package_kind, parent_pid = sys.argv[1:]
        main(Path(stage_path), executable_name, package_kind, int(parent_pid))
    except Exception as error:
        if len(sys.argv) > 1 and (Path(sys.argv[1]) / APPLY).is_file():
            ctypes.windll.user32.MessageBoxW(None, f"更新失败：{error}", "Wisland 更新", 0x10)
        sys.exit(1)
