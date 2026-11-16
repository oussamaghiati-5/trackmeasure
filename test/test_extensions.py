"""Pinned package integrity, MV3 validation, and private-copy lifecycle."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import zipfile

from scripts.get_extensions import unpack_package
from extension_state import manifest_at, wait_for_extensions


def package(manifest_version=3, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as z:
        z.writestr("manifest.json", json.dumps(dict(name="fixture", version="1.2",
                   manifest_version=manifest_version, background={"service_worker": "worker.js"})))
        z.writestr("worker.js", "// fixture")
        if extra:
            z.writestr(extra, "unsafe")
    return output.getvalue()


def spec(data, kind="zip"):
    return dict(name="fixture", version="1.2", archive=kind, sha256=hashlib.sha256(data).hexdigest())


class ExtensionPackages(unittest.TestCase):
    def test_zip_and_crx3_packages_extract_without_code_changes(self):
        zipped = package()
        for data, kind in ((zipped, "zip"), (struct.pack("<4sII", b"Cr24", 3, 0) + zipped, "crx")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                target = Path(td) / "extension"
                manifest = unpack_package(data, spec(data, kind), target)
                self.assertEqual(manifest["manifest_version"], 3)
                self.assertEqual((target / "worker.js").read_text(), "// fixture")

    def test_modified_download_fails_before_extraction(self):
        data = package()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "extension"
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                unpack_package(data + b"changed", spec(data), target)
            self.assertFalse(target.exists())

    def test_manifest_v2_and_wrong_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            for version in (2, 3):
                data = package(version)
                expected = spec(data)
                if version == 3:
                    expected["version"] = "9.9"
                with self.assertRaisesRegex(ValueError, "Expected Chromium MV3"):
                    unpack_package(data, expected, Path(td) / str(version))

    def test_archive_path_cannot_escape_destination(self):
        data = package(extra="../outside")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "Unsafe extension archive path"):
                unpack_package(data, spec(data), Path(td) / "extension")
            self.assertFalse((Path(td) / "outside").exists())

    def test_missing_extension_manifest_fails(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaisesRegex(ValueError, "manifest missing"):
            manifest_at(td)


class ExtensionReadiness(unittest.IsolatedAsyncioTestCase):
    async def test_translated_manifest_name_matches_runtime_name(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            data = package()
            unpack_package(data, spec(data), target)
            manifest = json.loads((target / "manifest.json").read_text())
            manifest.update(name="__MSG_name__", default_locale="en")
            (target / "manifest.json").write_text(json.dumps(manifest))
            locales = target / "_locales/en"
            locales.mkdir(parents=True)
            (locales / "messages.json").write_text(json.dumps({"name": {"message": "fixture"}}))
            worker = SimpleNamespace(url="chrome-extension://fixture/worker.js", evaluate=AsyncMock(return_value={
                "name": "fixture", "version": "1.2", "enabled_rulesets": [], "dynamic_rules": 1}))
            reports = await wait_for_extensions(SimpleNamespace(service_workers=[worker]), [target])
            self.assertEqual(reports[0]["name"], "fixture")

    async def test_privacy_badger_worker_presence_is_not_filter_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "privacybadger"
            data = package()
            unpack_package(data, spec(data), target)
            worker = SimpleNamespace(url="chrome-extension://fixture/worker.js", evaluate=AsyncMock(return_value={
                "name": "fixture", "version": "1.2", "enabled_rulesets": [], "dynamic_rules": 0,
                "initialized": False, "seed_loaded": False}))
            with self.assertRaisesRegex(RuntimeError, "Privacy Badger seed/filter initialization"):
                await wait_for_extensions(SimpleNamespace(service_workers=[worker]), [target], timeout=0.01)

    async def test_no_service_worker_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            data = package()
            unpack_package(data, spec(data), target)
            with self.assertRaisesRegex(RuntimeError, "service worker not found"):
                await wait_for_extensions(SimpleNamespace(service_workers=[]), [target], timeout=0.01)


HAS_DEPS = (importlib.util.find_spec("playwright") is not None
            and importlib.util.find_spec("tldextract") is not None)


@unittest.skipUnless(HAS_DEPS, "Run inside the project container")
class PrivateExtensionCopies(unittest.IsolatedAsyncioTestCase):
    async def test_copy_is_writable_outside_profile_and_removed_on_close(self):
        from common import launch
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            data = package()
            unpack_package(data, spec(data), source)
            callbacks = {}
            context = SimpleNamespace(on=lambda name, callback: callbacks.update({name: callback}))
            browser = SimpleNamespace(launch_persistent_context=AsyncMock(return_value=context))
            profile = Path(td) / "profile"
            await launch(SimpleNamespace(chromium=browser), profile, extensions=[source])
            args = browser.launch_persistent_context.call_args.kwargs["args"]
            copied = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--load-extension=")))
            self.assertFalse(copied.is_relative_to(profile))
            (copied / "generated_cache").write_text("cache")
            self.assertFalse((source / "generated_cache").exists())
            callbacks["close"]()
            self.assertFalse(copied.exists())

    async def test_failed_browser_launch_removes_private_copies(self):
        from common import launch
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            data = package()
            unpack_package(data, spec(data), source)
            browser = SimpleNamespace(launch_persistent_context=AsyncMock(side_effect=RuntimeError("launch failed")))
            with self.assertRaisesRegex(RuntimeError, "launch failed"):
                await launch(SimpleNamespace(chromium=browser), Path(td) / "profile", extensions=[source])
            args = browser.launch_persistent_context.call_args.kwargs["args"]
            copied = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--load-extension=")))
            self.assertFalse(copied.exists())


if __name__ == "__main__":
    unittest.main()
