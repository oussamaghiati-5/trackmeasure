"""Shared helpers: eTLD+1, site list loading, SQLite schema, browser launch."""
import csv
import ipaddress
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

import tldextract

HERE = Path(__file__).parent
# Bundled Public Suffix List snapshot only: offline and reproducible across runs.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def etld1(host_or_url: str) -> str:
    host = host_of(host_or_url) if "://" in host_or_url else host_or_url.lower().lstrip(".")
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    ext = _EXTRACT(host)
    reg = getattr(ext, "top_domain_under_public_suffix", None) or ext.registered_domain
    if reg:
        return reg
    parts = host.split(".")  # unknown suffix (e.g. *.test fixtures)
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def load_sites(path):
    """CSV with header domain,category (category optional) or one domain per line."""
    sites = []
    with open(path, newline="") as f:
        first = f.readline()
        f.seek(0)
        if "domain" in first.lower():
            for row in csv.DictReader(f):
                d = (row.get("domain") or "").strip()
                if d and not d.startswith("#"):
                    sites.append((d, (row.get("category") or "").strip()))
        else:
            for line in f:
                d = line.strip()
                if d and not d.startswith("#"):
                    sites.append((d, ""))
    return sites


def site_url(domain: str) -> str:
    return domain if "://" in domain else f"https://{domain}/"


SCHEMA = """
CREATE TABLE IF NOT EXISTS visits(
  id INTEGER PRIMARY KEY, run TEXT, site TEXT, category TEXT, url TEXT, final_url TEXT,
  phase TEXT, started REAL, ended REAL, status INTEGER, error TEXT, consent TEXT);
CREATE TABLE IF NOT EXISTS requests(
  id INTEGER PRIMARY KEY, visit_id INT, url TEXT, host TEXT, domain TEXT, third_party INT,
  rtype TEXT, method TEXT, frame_url TEXT, failure TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS api_calls(
  visit_id INT, frame_url TEXT, script_url TEXT, script_domain TEXT, third_party INT,
  api TEXT, args TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS cookies(
  run TEXT, site TEXT, name TEXT, domain TEXT, value TEXT, expires REAL,
  third_party INT, http_only INT, secure INT, same_site TEXT);
CREATE TABLE IF NOT EXISTS sites_done(
  run TEXT, site TEXT, ok INT, error TEXT, seconds REAL, PRIMARY KEY(run, site));
CREATE INDEX IF NOT EXISTS ix_api_visit ON api_calls(visit_id);
CREATE INDEX IF NOT EXISTS ix_req_visit ON requests(visit_id);
"""


def open_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode=WAL")
    return db


GPC_JS = """
Object.defineProperty(Navigator.prototype, 'globalPrivacyControl',
  { get: function globalPrivacyControl() { return true; }, configurable: true, enumerable: true });
"""


async def launch(pw, profile_dir, *, headless=True, extensions=(), proxy=None, gpc=False,
                 locale="en-US", timezone=None, viewport=(1366, 768), scale=1, extra_args=(),
                 extra_init=()):
    args = ["--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-background-networking", "--disable-sync", *extra_args]
    if extensions:
        exts = ",".join(str(Path(e).resolve()) for e in extensions)
        args += [f"--disable-extensions-except={exts}", f"--load-extension={exts}"]
    kw = dict(
        channel="chromium",  # full Chromium (new headless): required for extensions
        headless=headless, args=args, locale=locale,
        viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=scale,
    )
    if timezone:
        kw["timezone_id"] = timezone
    if proxy:
        kw["proxy"] = {"server": proxy}
    if gpc:
        kw["extra_http_headers"] = {"Sec-GPC": "1"}
    ctx = await pw.chromium.launch_persistent_context(str(profile_dir), **kw)
    if gpc:
        await ctx.add_init_script(GPC_JS)
    for js in extra_init:
        await ctx.add_init_script(js)
    return ctx


def now():
    return time.time()
