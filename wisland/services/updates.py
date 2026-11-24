# -*- coding: utf-8 -*-
"""读取正式版更新信息，优先使用国内下载源。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..core.release import (MANIFEST_KEY, MANIFEST_NAME, RELEASE_INFO_NAME, Package, download_origin,
                            release_object_keys, verified_packages, version_number)

GITHUB_REPO = "GezelligheidLin/wisland"
GITHUB_RELEASES = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


@dataclass(frozen=True)
class InstalledRelease:
    version: str
    download_base: str = ""
    update_public_key: str = ""

    @classmethod
    def load(cls) -> InstalledRelease | None:
        """读取随安装包打入的版本信息。源码与本地开发包可以省略该文件。"""
        try:
            data = json.loads(Path(__file__).with_name(RELEASE_INFO_NAME).read_text(encoding="utf-8-sig"))
            version_number(data["version"])
            base = download_origin(data["download_base"]) if data.get("download_base") else ""
            return cls(data["version"], base, data.get("update_public_key") or "")
        except (OSError, ValueError, TypeError, KeyError):
            return None


@dataclass(frozen=True)
class Update:
    version: str
    downloads: dict[str, str]
    packages: dict[str, Package] = field(default_factory=dict)
    fallback_downloads: dict[str, str] = field(default_factory=dict)

    @property
    def github_url(self) -> str:
        return f"{GITHUB_RELEASES}/tag/v{self.version}"


def check_for_update(installed: InstalledRelease) -> Update | None:
    """优先使用 OSS 签名清单，必要时从 GitHub Release 验签并提供可安装更新。"""
    def fetch(url: str) -> dict:
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
        return data

    current = version_number(installed.version)
    oss_reachable = False
    manual_oss = None
    with httpx.Client(timeout=5, headers={"Accept": "application/json"}, follow_redirects=True) as client:
        if mirror := installed.download_base:
            try:
                manifest = fetch(f"{mirror}/{MANIFEST_KEY}")
                latest = version_number(manifest.get("version"))
                oss_reachable = True
                if latest > current:
                    version = ".".join(map(str, latest))
                    links = {kind: f"{mirror}/{key}" for kind, key in release_object_keys(version).items()}
                    manual_oss = Update(version, links)
                    if installed.update_public_key:
                        packages = verified_packages(manifest, installed.update_public_key)
                        return Update(version, links, packages, _github_downloads(version))
            except (httpx.HTTPError, ValueError):
                pass

        try:
            latest = version_number(fetch(GITHUB_LATEST).get("tag_name"))
        except (httpx.HTTPError, ValueError):
            if oss_reachable:
                return manual_oss
            raise

        if latest <= current or (manual_oss and latest < version_number(manual_oss.version)):
            return manual_oss
        version = ".".join(map(str, latest))
        if installed.update_public_key:
            try:
                manifest = fetch(f"{GITHUB_RELEASES}/download/v{version}/{MANIFEST_NAME}")
                if version_number(manifest.get("version")) != latest:
                    raise ValueError("更新清单与 GitHub 发布版本不符")
                packages = verified_packages(manifest, installed.update_public_key)
                return Update(version, _github_downloads(version), packages)
            except (httpx.HTTPError, ValueError):
                pass
        if manual_oss and manual_oss.version == version:
            return manual_oss
        return Update(version, {})


def _github_downloads(version: str) -> dict[str, str]:
    base = f"{GITHUB_RELEASES}/download/v{version}"
    return {kind: f"{base}/{key.rsplit('/', 1)[-1]}" for kind, key in release_object_keys(version).items()}
