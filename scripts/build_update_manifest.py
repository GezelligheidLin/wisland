"""生成 GitHub Release 和 OSS 共用的更新清单。"""
import hashlib
import json
import os
from pathlib import Path

from wisland.core.release import MANIFEST_NAME, release_object_keys, sign_manifest, version_number


def build_manifest(version: str, artifacts: Path, private_key: str, public_key: str):
    """为已打包的 EXE 和 ZIP 生成一份清单，使用发行配置中的密钥签名。"""
    version = ".".join(map(str, version_number(version)))
    if bool(private_key) != bool(public_key):
        raise ValueError("请同时配置更新签名私钥与公钥")
    metadata = {}
    for kind, key in release_object_keys(version).items():
        path = artifacts / Path(key).name
        with path.open("rb") as body:
            size = path.stat().st_size
            if not size:
                raise ValueError(f"安装包为空：{path.name}")
            if private_key:
                metadata[kind] = {"size": size, "sha256": hashlib.file_digest(body, "sha256").hexdigest()}
    manifest = {"version": version}
    if private_key:
        manifest["packages"] = metadata
        manifest["signature"] = sign_manifest(manifest, private_key, public_key)
    (artifacts / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")


if __name__ == "__main__":
    build_manifest(os.environ["WISLAND_RELEASE_VERSION"], Path("dist"),
                   os.environ.get("WISLAND_UPDATE_PRIVATE_KEY", ""),
                   os.environ.get("WISLAND_UPDATE_PUBLIC_KEY", ""))
