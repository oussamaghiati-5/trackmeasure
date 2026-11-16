#!/usr/bin/env python3
"""Offline check of pinned real blockers against a locally served known domain."""
import argparse
import asyncio
from collections import Counter
import hashlib
import http.server
import json
from pathlib import Path
import sys
import sqlite3
import tempfile
import threading

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.async_api import async_playwright  # noqa: E402
from common import launch  # noqa: E402
from extension_state import wait_for_extensions  # noqa: E402
from scripts.get_extensions import archive_zip  # noqa: E402

TRACKER = "adsrvr.org"
COUNTS = Counter()


class Fixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host == TRACKER:
            COUNTS["tracker"] += 1
            content, kind = b"window.trackerLoaded = true;", "application/javascript"
        elif host == "static.publisher.test":
            content, kind = b"window.cleanLoaded = true;", "application/javascript"
        else:
            content = (f'<!doctype html><script src="http://{TRACKER}/track.js"></script>'
                       '<script src="http://static.publisher.test/clean.js"></script><h1>Local publisher</h1>').encode()
            kind = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def verify_installed(key, spec):
    """Verify actual installed input files, not just a cached archive or version."""
    data = (ROOT / "ext/.downloads" / (spec["sha256"] + "." + spec["archive"])).read_bytes()
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise RuntimeError(f"Cached package checksum changed: {key}")
    with archive_zip(data, spec["archive"]) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            installed = ROOT / "ext" / key / info.filename
            if not installed.is_file() or installed.read_bytes() != archive.read(info):
                raise RuntimeError(f"Installed extension differs from pinned package: {key}/{info.filename}")


async def measure(pw, key, resolver):
    with tempfile.TemporaryDirectory(prefix="tm_real_blocker_") as profile:
        extensions = [ROOT / "ext" / key] if key else []
        ctx = await launch(pw, profile, extensions=extensions, extra_args=[resolver])
        try:
            state = await wait_for_extensions(ctx, extensions)
            page = await ctx.new_page()
            failures = []
            page.on("requestfailed", lambda request: failures.append(dict(url=request.url, error=request.failure)))
            before = COUNTS["tracker"]
            await page.goto("http://publisher.test/", wait_until="load", timeout=20000)
            observed = await page.evaluate("""() => ({tracker: window.trackerLoaded === true,
                clean: window.cleanLoaded === true, gpc: navigator.globalPrivacyControl === true})""")
            return dict(extension=key or "baseline", observed=observed, extension_state=state,
                        tracker_server_hits=COUNTS["tracker"] - before, failed_requests=failures)
        finally:
            await ctx.close()


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "results/blocker_verification.json")
    args = ap.parse_args()
    lock = json.loads((ROOT / "extensions.lock.json").read_text())
    for key, spec in lock["extensions"].items():
        verify_installed(key, spec)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    resolver = (f"--host-resolver-rules=MAP publisher.test 127.0.0.1:{port}, "
                f"MAP static.publisher.test 127.0.0.1:{port}, MAP {TRACKER} 127.0.0.1:{port}")
    results = []
    try:
        async with async_playwright() as pw:
            for key in (None, "ubol", "privacybadger"):
                result = await measure(pw, key, resolver)
                results.append(result)
                if not result["observed"]["clean"]:
                    raise AssertionError(f"{key}: ordinary script did not load")
                if key is None:
                    if not result["observed"]["tracker"] or result["tracker_server_hits"] != 1:
                        raise AssertionError("Baseline must actually load the locally served tracker")
                else:
                    blocked = any(TRACKER in r["url"] and "BLOCKED_BY_CLIENT" in (r["error"] or "")
                                  for r in result["failed_requests"])
                    if result["observed"]["tracker"] or result["tracker_server_hits"] or not blocked:
                        raise AssertionError(f"{key}: known tracker was not verifiably blocked: {result}")
                print(json.dumps(result), flush=True)
            # Exercise the actual crawler, including its stored extension/GPC
            # metadata, not only the launch helper used by the control above.
            with tempfile.TemporaryDirectory(prefix="tm_blocker_crawl_") as td:
                sites = Path(td) / "sites.csv"
                sites.write_text("domain,category\nhttp://publisher.test/,fixture\n")
                database = Path(td) / "crawl.sqlite"
                process = await asyncio.create_subprocess_exec(
                    sys.executable, str(ROOT / "crawler.py"), "--sites", str(sites),
                    "--out", str(database), "--run", "pbadger_check",
                    "--extension", str(ROOT / "ext/privacybadger"), "--workers", "1",
                    "--ext-warmup", "0", "--dwell", "0", "--subpages", "0",
                    "--chromium-arg", resolver)
                if await process.wait() != 0:
                    raise AssertionError("Real-blocker crawler integration failed")
                with sqlite3.connect(database) as con:
                    if con.execute("SELECT ok FROM sites_done").fetchone() != (1,):
                        raise AssertionError("Crawler did not complete the real-blocker visit")
                    record = json.loads(con.execute("SELECT consent FROM visits WHERE phase='after_consent'").fetchone()[0])
                    if record.get("gpc_requested") is not False or record.get("gpc") is not True:
                        raise AssertionError("Crawler failed to distinguish requested GPC from extension-enabled GPC")
                    if record.get("extensions", [{}])[0].get("version") != lock["extensions"]["privacybadger"]["version"]:
                        raise AssertionError("Crawler did not retain actual extension metadata")
                    blocked = con.execute("SELECT COUNT(*) FROM requests WHERE url LIKE '%adsrvr.org%' "
                                          "AND failure LIKE '%BLOCKED_BY_CLIENT%'").fetchone()[0]
                    if not blocked:
                        raise AssertionError("The actual crawler did not record real-extension blocking")
                    results.append(dict(extension="privacybadger_crawler", gpc_requested=False,
                                        gpc_observed=True, extension_metadata_saved=True))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(dict(packages=lock, results=results, status="passed"), indent=2) + "\n")
        print(f"REAL BLOCKER CHECKS PASSED; report: {args.out}", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    asyncio.run(main())
