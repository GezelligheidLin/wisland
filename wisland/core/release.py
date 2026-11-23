# -*- coding: utf-8 -*-
"""客户端与发版脚本共用的版本号和更新源路径。"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

RELEASE_INFO_NAME = "release-info.json"
_OSS_RELEASE_PREFIX = "wisland/releases"
MANIFEST_KEY = f"{_OSS_RELEASE_PREFIX}/latest.json"
_STABLE_VERSION = re.compile(r"v?(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}")


def version_number(version: str) -> tuple[int, ...]:
    """将正式版号（可带 v 前缀）转换为可按数值比较的三段版本。"""
    if not isinstance(version, str) or not _STABLE_VERSION.fullmatch(version):
        raise ValueError("版本号应为 x.y.z")
    return tuple(map(int, version.removeprefix("v").split(".")))


def download_origin(url: str) -> str:
    """校验发行方的 HTTPS 下载域名，供更新客户端拼接固定对象路径。"""
    if not isinstance(url, str):
        raise ValueError("下载域名应为 HTTPS URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("下载域名应为 HTTPS URL")
    return parsed._replace(path="").geturl()


def release_object_keys(version: str) -> dict[str, str]:
    """返回该版本的 ZIP 和 EXE 在 OSS 中的对象路径。"""
    v = ".".join(map(str, version_number(version)))
    directory = f"{_OSS_RELEASE_PREFIX}/v{v}"
    return {"zip": f"{directory}/wisland-{v}.zip", "exe": f"{directory}/Wisland-{v}.exe"}
