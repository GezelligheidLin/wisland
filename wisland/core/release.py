# -*- coding: utf-8 -*-
"""客户端与发版脚本共用的版本号和更新源路径。"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

RELEASE_INFO_NAME = "release-info.json"
_OSS_RELEASE_PREFIX = "wisland/releases"
MANIFEST_NAME = "latest.json"
MANIFEST_KEY = f"{_OSS_RELEASE_PREFIX}/{MANIFEST_NAME}"
_STABLE_VERSION = re.compile(r"v?(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}")
_MAX_PACKAGE_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class Package:
    size: int
    sha256: str


def _signed_payload(version: str, packages: dict) -> bytes:
    return json.dumps({"version": version, "packages": packages}, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def sign_manifest(manifest: dict, private_key: str, public_key: str) -> str:
    """签发安装包摘要，并核对私钥是否对应客户端内置公钥。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    signer = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key, validate=True))
    if signer.public_key().public_bytes_raw() != base64.b64decode(public_key, validate=True):
        raise ValueError("更新签名公钥与私钥不匹配")
    return base64.b64encode(signer.sign(_signed_payload(manifest["version"], manifest["packages"]))).decode("ascii")


def verified_packages(manifest: dict, public_key: str) -> dict[str, Package]:
    """只将发行方已签名且格式正确的包元数据交给应用内更新器。"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        version = manifest["version"]
        version_number(version)
        packages = manifest["packages"]
        if not isinstance(packages, dict) or set(packages) != {"exe", "zip"}:
            raise ValueError("更新包清单无效")
        verified = {}
        for kind, item in packages.items():
            size, sha256 = item["size"], item["sha256"]
            if type(size) is not int or not 0 < size <= _MAX_PACKAGE_BYTES or not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise ValueError("更新包大小或摘要无效")
            verified[kind] = Package(size, sha256)
        verifier = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        verifier.verify(base64.b64decode(manifest["signature"], validate=True), _signed_payload(version, packages))
        return verified
    except (InvalidSignature, KeyError, TypeError, ValueError) as exc:
        raise ValueError("更新清单签名无效") from exc


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
