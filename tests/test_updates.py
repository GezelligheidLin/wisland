"""验证更新签名和原地替换的失败路径，无需真实凭据或生产 OSS。"""
import base64
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.build_update_manifest import build_manifest
from wisland.core.apply_update import COMPLETED, PAYLOAD, UpdateRolledBack, replace_application
from wisland.core.release import Package, sign_manifest, verified_packages
from wisland.services.install_update import _download, _extract_portable, prepare_update
from wisland.services.updates import GITHUB_LATEST, GITHUB_RELEASES, InstalledRelease, Update, check_for_update


class SignedUpdateTests(unittest.TestCase):
    def setUp(self):
        key = Ed25519PrivateKey.generate()
        private = base64.b64encode(key.private_bytes_raw()).decode("ascii")
        self.private = private
        self.public = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
        self.origin = "https://updates.example.test"
        self.package = Package(7, hashlib.sha256(b"example").hexdigest())
        metadata = {"size": self.package.size, "sha256": self.package.sha256}
        self.manifest = {"version": "1.2.4", "packages": {"zip": dict(metadata), "exe": dict(metadata)}}
        self.manifest["signature"] = sign_manifest(self.manifest, private, self.public)

    def check(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=self.manifest))
        client = httpx.Client(transport=transport)
        with patch("wisland.services.updates.httpx.Client", return_value=client):
            return check_for_update(InstalledRelease("1.2.3", self.origin, self.public))

    def test_signed_manifest_enables_in_app_update(self):
        update = self.check()
        self.assertEqual(update.version, "1.2.4")
        self.assertEqual(update.packages["zip"], self.package)
        self.assertEqual(update.downloads["exe"], f"{self.origin}/wisland/releases/v1.2.4/Wisland-1.2.4.exe")

    def test_tampered_and_legacy_manifest_only_offer_manual_download(self):
        self.manifest["packages"]["zip"]["size"] += 1
        with self.assertRaises(ValueError):
            verified_packages(self.manifest, self.public)
        update = self.check()
        self.assertEqual(update.packages, {})
        self.assertIn("zip", update.downloads)
        self.manifest = {"version": "1.2.4"}
        self.assertEqual(self.check().packages, {})

    def check_github_fallback(self, manifest):
        release_file = f"{GITHUB_RELEASES}/download/v1.2.4/latest.json"
        redirected = "https://release-assets.githubusercontent.com/test-manifest"

        def respond(request):
            url = str(request.url)
            if url == GITHUB_LATEST:
                return httpx.Response(200, json={"tag_name": "v1.2.4"})
            if url == release_file:
                return httpx.Response(302, headers={"Location": redirected})
            if url == redirected:
                return httpx.Response(200, json=manifest) if manifest else httpx.Response(404)
            return httpx.Response(403)

        client_class = httpx.Client
        with patch("wisland.services.updates.httpx.Client", side_effect=lambda **kwargs: client_class(
                transport=httpx.MockTransport(respond), **kwargs)):
            return check_for_update(InstalledRelease("1.2.3", self.origin, self.public))

    def test_oss_failure_uses_verified_github_release_assets(self):
        update = self.check_github_fallback(self.manifest)
        self.assertEqual(update.packages["exe"], self.package)
        self.assertEqual(update.downloads["exe"], f"{GITHUB_RELEASES}/download/v1.2.4/Wisland-1.2.4.exe")

    def test_missing_or_invalid_github_manifest_preserves_manual_link(self):
        for manifest in (None, {"version": "1.2.4"}, {**self.manifest, "version": "1.2.5"}):
            with self.subTest(manifest=manifest):
                update = self.check_github_fallback(manifest)
                self.assertEqual(update.packages, {})
                self.assertEqual(update.github_url, f"{GITHUB_RELEASES}/tag/v1.2.4")

    def test_published_manifest_verifies_the_built_artifacts(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            (dist / "wisland-1.2.4.zip").write_bytes(b"example")
            (dist / "Wisland-1.2.4.exe").write_bytes(b"example")
            build_manifest("1.2.4", dist, self.private, self.public)
            manifest = json.loads((dist / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(verified_packages(manifest, self.public), {"exe": self.package, "zip": self.package})

    def test_failed_oss_download_retries_github_with_same_digest(self):
        update = Update("1.2.4", {"exe": self.origin}, {"exe": self.package}, {"exe": GITHUB_RELEASES})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preview = root / "Wisland.exe"
            preview.touch()
            (root / "data").mkdir()

            def download(url, path, package, progress):
                self.assertEqual(package, self.package)
                self.assertFalse(path.exists())
                if url == self.origin:
                    path.write_bytes(b"partial")
                    raise httpx.ReadError("connection interrupted")
                path.write_bytes(b"MZexample")

            with patch("wisland.services.install_update.sys.executable", str(preview)), \
                    patch("wisland.services.install_update.UPDATER", preview), \
                    patch("wisland.services.install_update._launch", return_value=Mock(wait=Mock(return_value=0))), \
                    patch("wisland.services.install_update._download", side_effect=download) as fetch:
                stage = prepare_update(update, "exe", Mock())
            self.assertEqual([call.args[0] for call in fetch.call_args_list], [self.origin, GITHUB_RELEASES])
            self.assertEqual((stage / PAYLOAD / "Wisland.exe").read_bytes(), b"MZexample")

    def test_wrong_download_digest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "update.exe"
            response = httpx.Response(200, content=b"badfile", request=httpx.Request("GET", self.origin))
            with patch("wisland.services.install_update.httpx.stream") as stream:
                stream.return_value.__enter__.return_value = response
                with self.assertRaisesRegex(ValueError, "校验失败"):
                    _download(self.origin, target, self.package, Mock())


class PortableUpdateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.stage = self.root / "data" / ".wisland-update-12345678"
        self.payload = self.stage / PAYLOAD
        self.payload.mkdir(parents=True)
        self.exe = self.root / "CustomName.exe"
        self.exe.write_bytes(b"old exe")
        self.runtime = self.root / "_internal"
        self.runtime.mkdir()
        (self.runtime / "old.dll").write_bytes(b"old runtime")
        for name in ("config.json", "data/todos.json", "models/model.bin"):
            user_file = self.root / name
            user_file.parent.mkdir(exist_ok=True)
            user_file.write_bytes(b"user file")

    def add_zip(self, additional: dict | None = None):
        path = self.stage / "release.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in {"Wisland.exe": b"new exe", "_internal/new.dll": b"new runtime",
                               "config.json": b"release file", **(additional or {})}.items():
                archive.writestr(name, data)
        return path

    def test_directory_update_keeps_user_files_and_exe_name(self):
        _extract_portable(self.add_zip(), self.payload)
        restart = Mock()
        replace_application(self.stage, self.exe, "zip", restart)
        restart.assert_called_once_with()
        self.assertEqual(self.exe.read_bytes(), b"new exe")
        self.assertEqual((self.runtime / "new.dll").read_bytes(), b"new runtime")
        self.assertFalse((self.runtime / "old.dll").exists())
        for name in ("config.json", "data/todos.json", "models/model.bin"):
            self.assertEqual((self.root / name).read_bytes(), b"user file")

    def test_restart_failure_rolls_back_both_components(self):
        _extract_portable(self.add_zip(), self.payload)
        with self.assertRaises(UpdateRolledBack):
            replace_application(self.stage, self.exe, "zip", Mock(side_effect=OSError))
        self.assertEqual(self.exe.read_bytes(), b"old exe")
        self.assertEqual((self.runtime / "old.dll").read_bytes(), b"old runtime")
        self.assertFalse((self.stage / COMPLETED).exists())

    def test_single_exe_update_does_not_touch_adjacent_runtime(self):
        (self.payload / "Wisland.exe").write_bytes(b"new exe")
        replace_application(self.stage, self.exe, "exe", Mock())
        self.assertEqual(self.exe.read_bytes(), b"new exe")
        self.assertEqual((self.runtime / "old.dll").read_bytes(), b"old runtime")

    def test_zip_cannot_write_outside_program_files(self):
        path = self.add_zip({"_internal/../../config.json": b"overwritten"})
        with self.assertRaises(ValueError):
            _extract_portable(path, self.payload)
        self.assertEqual((self.root / "config.json").read_bytes(), b"user file")
