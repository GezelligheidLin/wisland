"""下载、验证并暂存与当前运行方式相符的 OSS 更新包。"""
from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PureWindowsPath

import httpx

from ..core.apply_update import (PAYLOAD, PORTABLE_EXE, READY, RUNTIME, STAGED_OK,
                                 STAGE_PREFIX, UPDATER_NAME, clean_update_stage)
from ..core.release import Package
from .updates import Update

UPDATER = Path(__file__).resolve().parents[1] / "assets" / UPDATER_NAME


def package_kind() -> str | None:
    """根据正在运行的便携包判断要更新的文件，源码模式不支持原地替换。"""
    if os.name != "nt" or not getattr(sys, "frozen", False) or not UPDATER.is_file():
        return None
    root = Path(sys.executable).resolve().parent
    return "zip" if Path(sys._MEIPASS).resolve() == root / RUNTIME else "exe"


def _download(url: str, path: Path, package: Package, progress):
    digest = hashlib.sha256()
    total = 0
    reported = 0
    with httpx.stream("GET", url, timeout=30, headers={"Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        with path.open("xb") as output:
            for block in response.iter_bytes(256 * 1024):
                total += len(block)
                if total > package.size:
                    raise ValueError("更新包大小与清单不符")
                digest.update(block)
                output.write(block)
                percent = min(99, total * 100 // package.size)
                if percent > reported:
                    reported = percent
                    progress(percent)
    if total != package.size or digest.hexdigest() != package.sha256:
        raise ValueError("更新包校验失败")


def _extract_portable(path: Path, payload: Path):
    """只暂存 ZIP 中的程序和运行库，拒绝路径穿越及文件链接。"""
    total = 0
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            parts = entry.filename.rstrip("/").split("/")
            if parts != [PORTABLE_EXE] and parts[0] != RUNTIME:
                continue
            if (any(part in ("", ".", "..") or part.endswith((".", " ")) or any(c in part for c in '\\:')
                    or PureWindowsPath(part).is_reserved()
                    for part in parts) or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.external_attr & 0x400):
                raise ValueError("更新 ZIP 包含无效程序路径")
            destination = payload.joinpath(*parts)
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            total += entry.file_size
            if total > 2 * 1024**3:
                raise ValueError("解压后的更新包过大")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)
    if not (payload / PORTABLE_EXE).is_file() or not (payload / RUNTIME).is_dir():
        raise ValueError("更新 ZIP 缺少程序文件或运行库")


def prepare_update(update: Update, kind: str, progress) -> Path:
    """将已验签的包下载到同一卷，以便随后通过重命名替换程序文件。"""
    target = Path(sys.executable).resolve()
    data_dir = target.parent / "data"
    stage = Path(tempfile.mkdtemp(dir=data_dir, prefix=STAGE_PREFIX))
    try:
        if stage.stat().st_dev != target.stat().st_dev:
            raise OSError("更新暂存目录与程序必须位于同一个磁盘卷")
        payload = stage / PAYLOAD
        payload.mkdir()
        download = stage / "package.zip" if kind == "zip" else payload / PORTABLE_EXE
        _download(update.downloads[kind], download, update.packages[kind], progress)
        progress(100)
        if kind == "zip":
            _extract_portable(download, payload)
            download.unlink()
        with (payload / PORTABLE_EXE).open("rb") as executable:
            if executable.read(2) != b"MZ":
                raise ValueError("更新包中的 EXE 无效")
        process = _launch([str(payload / PORTABLE_EXE), "--self-check"], payload,
                          WISLAND_EXPECT_VERSION=update.version, QT_QPA_PLATFORM="offscreen")
        try:
            if process.wait(timeout=60) != 0:
                raise RuntimeError("新版程序启动校验失败")
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("新版程序启动校验超时") from None
        shutil.copyfile(UPDATER, stage / UPDATER_NAME)
        (stage / STAGED_OK).touch(exist_ok=False)
        return stage
    except BaseException:
        clean_update_stage(stage, data_dir)
        raise


def _launch(args: list[str], cwd: Path, **environment: str) -> subprocess.Popen:
    kernel32 = ctypes.windll.kernel32
    kernel32.SetDllDirectoryW(None)
    try:
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1", **environment)
        return subprocess.Popen(args, cwd=cwd, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        kernel32.SetDllDirectoryW(sys._MEIPASS)


def launch_updater(stage: Path, kind: str):
    """启动独立更新器并等它准备好交接，失败时让主程序继续运行。"""
    target = Path(sys.executable).resolve()
    helper = stage / UPDATER_NAME
    process = _launch([str(helper), str(stage), target.name, kind, str(os.getpid())], stage)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if (stage / READY).is_file():
            return
        time.sleep(0.1)
    raise RuntimeError("无法启动独立更新器")
