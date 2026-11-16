#!/usr/bin/env python3
"""Install hash-pinned, unmodified Chromium MV3 packages using Python's stdlib."""
import argparse
import hashlib
import io
import json
import os
import stat
import struct
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent


def archive_zip(data, kind):
    if kind == "crx":
        if len(data) < 12 or data[:4] != b"Cr24":
            raise ValueError("Invalid CRX header")
        version = struct.unpack_from("<I", data, 4)[0]
        if version != 3:
            raise ValueError(f"Expected CRX3, got CRX{version}")
        offset = 12 + struct.unpack_from("<I", data, 8)[0]
        if offset >= len(data) or data[offset:offset + 4] != b"PK\x03\x04":
            raise ValueError("Invalid CRX ZIP payload")
        data = data[offset:]
    elif kind != "zip":
        raise ValueError(f"Unsupported package type: {kind}")
    return zipfile.ZipFile(io.BytesIO(data))


def unpack_package(data, spec, destination):
    actual = hashlib.sha256(data).hexdigest()
    if actual != spec["sha256"]:
        raise ValueError(f"Checksum mismatch for {spec['name']}; installed files were not replaced. "
                         "The upstream download may have changed; review and update the lock explicitly.")
    with archive_zip(data, spec["archive"]) as z:
        for info in z.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise ValueError("Unsafe extension archive path")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Extension archive contains a symbolic link")
        z.extractall(destination)
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Extension manifest must be at the package root")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("manifest_version") != 3 or manifest.get("version") != spec["version"]:
        raise ValueError(f"Expected Chromium MV3 version {spec['version']}")
    worker = manifest.get("background", {}).get("service_worker", "").lstrip("/")
    if not worker or ".." in PurePosixPath(worker).parts or not (destination / worker).is_file():
        raise ValueError("Extension service worker is missing or invalid")
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", type=Path, help="optional directory of hash-named downloaded packages")
    args = ap.parse_args()
    lock = json.loads((ROOT / "extensions.lock.json").read_text())
    ext = ROOT / "ext"
    ext.mkdir(exist_ok=True)
    cache = args.cache_dir or ext / ".downloads"
    cache.mkdir(parents=True, exist_ok=True)
    installed_cache = ext / ".downloads"
    installed_cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=ext) as td:
        stage = Path(td)
        # Validate every download before replacing either installed extension.
        for key, spec in lock["extensions"].items():
            package = cache / (spec["sha256"] + "." + spec["archive"])
            if package.is_file():
                data = package.read_bytes()
            else:
                print(f"Downloading {spec['name']} {spec['version']} from {spec['url']}", flush=True)
                req = urllib.request.Request(spec["url"], headers={"User-Agent": "trackmeasure-extension-setup/1.0"})
                with urllib.request.urlopen(req, timeout=120) as response:
                    data = response.read()
            unpack_package(data, spec, stage / key)
            package.write_bytes(data)
            if cache.resolve() != installed_cache.resolve():
                (installed_cache / package.name).write_bytes(data)
            print(f"Verified {spec['name']} {spec['version']} (MV3, SHA-256 matches)", flush=True)
        for key in lock["extensions"]:
            target = ext / key
            backup = stage / (key + ".previous")
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(stage / key, target)
            except OSError:
                if backup.exists():
                    os.replace(backup, target)
                raise
    print("Installed under ext/. Run make verify-blockers to check actual loading and blocking.")


if __name__ == "__main__":
    main()
