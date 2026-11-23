# -*- coding: utf-8 -*-
"""将 GitHub Actions 已打包的发行文件镜像到私有 OSS Bucket。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import alibabacloud_oss_v2 as oss

from wisland.core.release import MANIFEST_KEY, download_origin, release_object_keys, version_number


def published_version(client: oss.Client, bucket: str) -> tuple[int, ...] | None:
    """读取源站的最新版本，避免重跑旧的构建时让客户端下载降级。"""
    try:
        result = client.get_object(oss.GetObjectRequest(bucket=bucket, key=MANIFEST_KEY))
    except oss.exceptions.OperationError as error:
        service_error = error.unwrap()
        if isinstance(service_error, oss.exceptions.ServiceError) and service_error.code == "NoSuchKey":
            return None
        raise
    with result.body as stream:
        return version_number(json.loads(stream.read())["version"])


def publish(client: oss.Client, bucket: str, version: str, artifacts: Path):
    """先上传两个安装包，确认完成后再更新客户端读取的 latest.json。"""
    target_version = version_number(version)
    version = ".".join(map(str, target_version))
    current_version = published_version(client, bucket)
    if current_version is not None and current_version >= target_version:
        print(f"OSS 已有 v{version} 或更新的版本，跳过镜像发布")
        return

    packages = [(key, artifacts / Path(key).name) for key in release_object_keys(version).values()]
    for _, path in packages:
        if not path.is_file() or not path.stat().st_size:
            raise FileNotFoundError(f"安装包不存在或为空：{path.name}")

    uploader = client.uploader()
    for key, path in packages:
        uploader.upload_file(oss.PutObjectRequest(
            bucket=bucket,
            key=key,
            content_type="application/zip" if path.suffix == ".zip" else "application/octet-stream",
            content_disposition=f'attachment; filename="{path.name}"',
            cache_control="public, max-age=86400",
        ), filepath=str(path))
        print(f"已上传 {path.name}")

    client.put_object(oss.PutObjectRequest(
        bucket=bucket,
        key=MANIFEST_KEY,
        content_type="application/json",
        cache_control="no-store",
        body=json.dumps({"version": version}).encode("utf-8"),
    ))
    print(f"OSS 更新源已发布 v{version}")


def main():
    bucket = os.environ.get("WISLAND_OSS_BUCKET")
    region = os.environ.get("WISLAND_OSS_REGION")
    if not bucket or not region:
        raise SystemExit("请配置 WISLAND_OSS_BUCKET 和 WISLAND_OSS_REGION")
    if not all(os.environ.get(name) for name in ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET")):
        raise SystemExit("请配置 OSS_ACCESS_KEY_ID 和 OSS_ACCESS_KEY_SECRET")

    config = oss.config.load_default()
    config.region = region
    config.credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
    if upload_domain := os.environ.get("WISLAND_OSS_UPLOAD_ORIGIN"):
        config.endpoint = download_origin(upload_domain)
        config.use_cname = True
    publish(oss.Client(config), bucket, os.environ["WISLAND_RELEASE_VERSION"], Path("release-assets"))


if __name__ == "__main__":
    main()
