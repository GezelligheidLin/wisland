# -*- coding: utf-8 -*-
"""读取正式版更新信息，优先使用国内下载源。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..core.release import MANIFEST_KEY, RELEASE_INFO_NAME, download_origin, release_object_keys, version_number

GITHUB_REPO = "GezelligheidLin/wisland"
GITHUB_RELEASES = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


@dataclass(frozen=True)
class InstalledRelease:
    version: str
    download_base: str = ""

    @classmethod
    def load(cls) -> InstalledRelease | None:
        """读取随安装包打入的版本信息。源码与本地开发包可以省略该文件。"""
        try:
            data = json.loads(Path(__file__).with_name(RELEASE_INFO_NAME).read_text(encoding="utf-8-sig"))
            version_number(data["version"])
            base = download_origin(data["download_base"]) if data.get("download_base") else ""
            return cls(data["version"], base)
        except (OSError, ValueError, TypeError, KeyError):
            return None


@dataclass(frozen=True)
class Update:
    version: str
    downloads: dict[str, str]

    @property
    def github_url(self) -> str:
        return f"{GITHUB_RELEASES}/tag/v{self.version}"


def check_for_update(installed: InstalledRelease) -> Update | None:
    """查询最新版本。镜像缺失或请求失败时改查 GitHub，网络错误交给调用方处理。"""
    def fetch(url: str, key: str) -> tuple[int, ...]:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                body.extend(chunk)
                if len(body) > 256 * 1024:
                    raise ValueError("更新接口响应过大")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("更新接口响应格式无效")
        return version_number(data.get(key))

    current = version_number(installed.version)
    with httpx.Client(timeout=5, headers={"Accept": "application/json"}) as client:
        mirror = installed.download_base
        if mirror:
            try:
                latest = fetch(f"{mirror}/{MANIFEST_KEY}", "version")
            except (httpx.HTTPError, ValueError):
                mirror = ""
        if not mirror:
            latest = fetch(GITHUB_LATEST, "tag_name")

    if latest <= current:
        return None
    version = ".".join(map(str, latest))
    links = {kind: f"{mirror}/{key}" for kind, key in release_object_keys(version).items()} if mirror else {}
    return Update(version, links)
