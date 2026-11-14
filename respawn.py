#!/usr/bin/env python3
"""Cookie-respawning check (stateful and fingerprint-based).

Per site, three browsers on the same machine/IP:
  A1  profile A, fingerprint F1: visit, snapshot cookies + localStorage + IndexedDB
  A2  --clear cookies : same profile A, cookies deleted, revisit   -> stateful respawning
      --clear all     : brand-new profile, same F1, revisit        -> fingerprint/server-side respawning
  B   control: brand-new profile, different fingerprint F2 (viewport, DPR, locale, timezone,
      hardwareConcurrency, deviceMemory, canvas noise), same IP

An identifier-like token (>=8 alnum chars incl. a digit, not a timestamp) found in an A1 cookie
counts as respawned if it reappears in an A2 cookie (any name) and appears nowhere in B.
"Nowhere in B" rules out constants shared by all users; with --clear all and the same IP,
it also rules out pure IP keying, leaving browser-attribute (fingerprint) derivation.

  python respawn.py --sites respawn_sites.csv --clear cookies --out data/respawn.sqlite
  python respawn.py --sites respawn_sites.csv --clear all     --out data/respawn.sqlite
"""
import argparse
import asyncio
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote

from playwright.async_api import async_playwright

from common import etld1, launch, load_sites, site_url

TOKEN = re.compile(r"[A-Za-z0-9]{8,}")

F1 = dict(viewport=(1366, 768), scale=1, locale="en-US", timezone="Europe/Paris")
F2 = dict(viewport=(1536, 864), scale=1.25, locale="fr-FR", timezone="America/New_York")
F2_JS = r"""
(() => {
  const def = (p, n, v) => Object.defineProperty(p, n, { get: { [n]() { return v; } }[n], configurable: true });
  def(Navigator.prototype, 'hardwareConcurrency', 3);
  def(Navigator.prototype, 'deviceMemory', 2);
  // Canvas noise: flip the low bit of a few pixels before readback.
  const noise = (ctx, w, h) => { try { const d = ctx.getImageData(0, 0, Math.min(w, 8), Math.min(h, 8));
    for (let i = 0; i < d.data.length; i += 16) d.data[i] ^= 1; ctx.putImageData(d, 0, 0); } catch (e) {} };
  const td = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function (...a) {
    const c = this.getContext && this.getContext('2d'); if (c && this.width && this.height) noise(c, this.width, this.height);
    return td.apply(this, a); };
  const gid = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function (...a) {
    const d = gid.apply(this, a); for (let i = 0; i < d.data.length; i += 64) d.data[i] ^= 1; return d; };
})();
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(site TEXT, mode TEXT, phase TEXT, state TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS respawned(site TEXT, mode TEXT, token TEXT, a1_cookie TEXT, a2_cookie TEXT,
  in_a1_storage TEXT, third_party INT, verdict TEXT);
CREATE TABLE IF NOT EXISTS respawn_done(site TEXT, mode TEXT, ok INT, error TEXT, n_respawned INT,
  PRIMARY KEY(site, mode));
"""


def is_timestamp(tok):
    if not tok.isdigit() or not (9 <= len(tok) <= 13):
        return False
    v = int(tok)
    now = time.time()
    return abs(v - now) < 5 * 365 * 86400 or abs(v / 1000 - now) < 5 * 365 * 86400


def tokens(value):
    v = unquote(value or "")
    return {t for t in TOKEN.findall(v) if any(ch.isdigit() for ch in t) and not is_timestamp(t)
            and len(set(t)) > 3}


def cookie_tokens(state):
    out = {}
    for c in state.get("cookies", []):
        for t in tokens(c["value"]):
            out.setdefault(t, f'{c["name"]}@{c["domain"]}')
    return out


def storage_tokens(state):
    out = {}
    for o in state.get("origins", []):
        for item in o.get("localStorage", []):
            for t in tokens(item.get("value")):
                out.setdefault(t, f'localStorage:{item["name"]}@{o["origin"]}')
        for idb in o.get("indexedDB", []) or []:
            for t in tokens(json.dumps(idb)):
                out.setdefault(t, f'indexedDB:{idb.get("name")}@{o["origin"]}')
    return out


async def snapshot(ctx):
    try:
        return await ctx.storage_state(indexed_db=True)
    except TypeError:
        return await ctx.storage_state()


async def visit(ctx, url, dwell, timeout):
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    try:
        await page.goto(url, wait_until="load", timeout=timeout * 1000)
    except Exception:
        pass
    try:
        await page.mouse.wheel(0, 800)
    except Exception:
        pass
    await page.wait_for_timeout(dwell * 1000)
    return await snapshot(ctx)


async def check_site(pw, args, db, domain):
    url = site_url(domain)
    site_dom = etld1(url)
    dirs = [Path(tempfile.mkdtemp(prefix="tm_resp_")) for _ in range(3)]
    common = dict(headless=not args.headful, proxy=args.proxy, extra_args=args.chromium_arg)
    snaps = {}
    try:
        ctx = await launch(pw, dirs[0], **common, **F1)
        snaps["A1"] = await visit(ctx, url, args.dwell, args.timeout)
        if args.clear == "cookies":
            await ctx.clear_cookies()
            await asyncio.sleep(1)
            snaps["A2"] = await visit(ctx, url, args.dwell, args.timeout)
            await ctx.close()
        else:
            await ctx.close()
            ctx = await launch(pw, dirs[1], **common, **F1)
            snaps["A2"] = await visit(ctx, url, args.dwell, args.timeout)
            await ctx.close()
        ctx = await launch(pw, dirs[2], **common, **F2, extra_init=[F2_JS])
        snaps["B"] = await visit(ctx, url, args.dwell, args.timeout)
        await ctx.close()
    finally:
        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)

    for ph, s in snaps.items():
        db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)", (domain, args.clear, ph, json.dumps(s), time.time()))

    a1c, a2c = cookie_tokens(snaps["A1"]), cookie_tokens(snaps["A2"])
    a1s = storage_tokens(snaps["A1"])
    b_all = set(cookie_tokens(snaps["B"])) | set(storage_tokens(snaps["B"]))
    rows = []
    for tok in sorted(set(a1c) & set(a2c) - b_all):
        cdom = a2c[tok].rsplit("@", 1)[1]
        third = int(etld1(cdom) != site_dom)
        if args.clear == "cookies":
            verdict = "stateful (value kept in " + a1s[tok].split(":")[0] + ")" if tok in a1s else "no stored copy: fingerprint, cache/ETag or SW (re-run with --clear all)"
        else:
            verdict = "fingerprint-derived"
        rows.append((domain, args.clear, tok, a1c[tok], a2c[tok], a1s.get(tok), third, verdict))
    db.executemany("INSERT INTO respawned VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sites", required=True)
    ap.add_argument("--clear", choices=["cookies", "all"], default="cookies")
    ap.add_argument("--out", default="data/respawn.sqlite")
    ap.add_argument("--dwell", type=float, default=15)
    ap.add_argument("--timeout", type=float, default=45)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--proxy")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--chromium-arg", action="append", default=[])
    args = ap.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.out)
    db.executescript(SCHEMA)
    done = {r[0] for r in db.execute("SELECT site FROM respawn_done WHERE mode=? AND ok=1", (args.clear,))}
    sites = [d for d, _ in load_sites(args.sites) if d not in done]
    sem = asyncio.Semaphore(args.workers)
    async with async_playwright() as pw:
        async def one(d):
            async with sem:
                db.execute("DELETE FROM respawned WHERE site=? AND mode=?", (d, args.clear))
                db.execute("DELETE FROM snapshots WHERE site=? AND mode=?", (d, args.clear))
                try:
                    n = await asyncio.wait_for(check_site(pw, args, db, d), args.timeout * 6 + args.dwell * 4)
                    db.execute("INSERT OR REPLACE INTO respawn_done VALUES (?,?,?,?,?)", (d, args.clear, 1, None, n))
                    print(f"[respawn/{args.clear}] {d}: {n} respawned token(s)", file=sys.stderr)
                except Exception as e:
                    db.execute("INSERT OR REPLACE INTO respawn_done VALUES (?,?,?,?,?)",
                               (d, args.clear, 0, f"{type(e).__name__}: {e}"[:300], None))
                    print(f"[respawn/{args.clear}] {d}: FAIL {e}", file=sys.stderr)
                db.commit()
        await asyncio.gather(*(one(d) for d in sites))
    for r in db.execute("SELECT site, verdict, a1_cookie, a2_cookie, in_a1_storage FROM respawned WHERE mode=?", (args.clear,)):
        print(" | ".join(str(x) for x in r))
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
