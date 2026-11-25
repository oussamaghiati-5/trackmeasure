#!/usr/bin/env python3
"""Instrumented crawler. One fresh browser profile per site.

Flow per site (identical in every mode so runs are comparable):
  landing      homepage load, no banner interaction  -> pre-consent tracking
  after_consent same page, after accept/reject (or nothing in baseline)
  home_reload  homepage reloaded with the consent state stored
  subpage      N same-site subpages (reuse baseline's URLs with --reuse-urls-from)

Examples:
  python crawler.py --sites sites.csv --run baseline --out data/crawl.sqlite
  python crawler.py --sites sites.csv --run ubol --extension ext/ubol --out data/crawl.sqlite --reuse-urls-from data/crawl.sqlite:baseline
  python crawler.py --sites sites.csv --run optout --consent reject --gpc --out data/crawl.sqlite --reuse-urls-from data/crawl.sqlite:baseline
"""
import argparse
import asyncio
import hashlib
import json
import random
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urldefrag, urlparse

from playwright.async_api import async_playwright

import consent
import exfil
from common import HERE, etld1, host_of, launch, load_sites, open_db, site_url
from navigation import measurement_error, navigation_error
from extension_state import wait_for_extensions
from script_evidence import ScriptCapture
from request_evidence import RequestPayloadCapture
from network_evidence import attach_initiators
from coverage_status import site_status, progress_line
import uuid
import weakref

INSTRUMENT = (HERE / "instrument.js").read_text()

def instrument_source(attribute_evidence=False):
    return INSTRUMENT.replace('const ATTRIBUTE_EVIDENCE = false;',
                              'const ATTRIBUTE_EVIDENCE = true;') if attribute_evidence else INSTRUMENT
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp4", ".mp3", ".xml", ".rss", ".svg", ".webp")
SKIP_WORDS = ("logout", "signout", "sign-out", "login", "signin", "sign-in", "register", "cart", "checkout", "mailto:", "tel:")
POST_CAP = 64 * 1024  # bytes of request body kept for exfiltration matching


class SiteState:
    def __init__(self, db, run, site, category, site_domain, capture_payloads=False):
        self.db, self.run, self.site, self.category = db, run, site, category
        self.site_domain = site_domain
        self.visit_id = None
        self.req_rows = {}
        self.intervals = []
        self.payload_capture = RequestPayloadCapture(db) if capture_payloads else None
        self.frame_ids = weakref.WeakKeyDictionary()

    def frame_id(self, frame):
        if frame is None:
            return None
        try:
            if frame not in self.frame_ids:
                self.frame_ids[frame] = str(uuid.uuid4())
            return self.frame_ids[frame]
        except TypeError:
            return None  # Synthetic/unsupported frame handles have no identity evidence.

    def new_visit(self, url, phase):
        started = time.time()
        cur = self.db.execute(
            "INSERT INTO visits(run, site, category, url, phase, started) VALUES (?,?,?,?,?,?)",
            (self.run, self.site, self.category, url, phase, started))
        self.visit_id = cur.lastrowid
        self.intervals.append([self.visit_id, started, None])
        return self.visit_id

    def end_visit(self, vid, status=None, error=None, final_url=None, consent_res=None):
        ended = time.time()
        self.db.execute("UPDATE visits SET ended=?, status=?, error=?, final_url=?, consent=? WHERE id=?",
                        (ended, status, error, final_url, consent_res, vid))
        for interval in self.intervals:
            if interval[0] == vid:
                interval[2] = ended
        if self.visit_id == vid:
            self.visit_id = None
        self.db.commit()

    # --- event handlers ---
    def on_request(self, req):
        if self.visit_id is None:
            return
        url = req.url
        if not url.startswith("http"):
            return
        try:
            frame_url = req.frame.url
        except Exception:
            frame_url = None
        dom = etld1(url)
        post = None
        if req.method != "GET":
            try:
                buf = req.post_data_buffer
            except Exception:
                buf = None
            if buf:
                post = buf[:POST_CAP].decode("utf-8", "replace")
        cur = self.db.execute(
            "INSERT INTO requests(visit_id,url,host,domain,third_party,rtype,method,frame_url,ts,post_data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.visit_id, url[:8000], host_of(url), dom, int(dom != self.site_domain),
             req.resource_type, req.method, frame_url, time.time(), post))
        self.req_rows[req] = cur.lastrowid
        if self.payload_capture:
            self.payload_capture.capture(cur.lastrowid, req)
            try:
                fid = self.frame_id(req.frame)
            except Exception:
                fid = None
            self.db.execute('INSERT INTO request_context VALUES(?,?)', (cur.lastrowid, fid))

    def on_request_failed(self, req):
        row = self.req_rows.pop(req, None)
        if row:
            self.db.execute("UPDATE requests SET failure=? WHERE id=?", (req.failure or "failed", row))

    def on_request_finished(self, req):
        self.req_rows.pop(req, None)

    def on_log(self, source, batch):
        if not isinstance(batch, list):
            return
        try:
            frame_url = source["frame"].url
        except Exception:
            frame_url = None
        rows, values = [], []
        for ev in batch:
            ts = (ev.get("ts") or 0) / 1000
            # Batches can arrive after a phase boundary: use event time, not
            # whichever visit happens to be active at delivery time.
            vid = next((v for v, start, end in reversed(self.intervals)
                        if start <= ts and (end is None or ts <= end)), None)
            if vid is None:
                continue
            script = ev.get("script") or ""
            sdom = etld1(script) if script.startswith("http") else ""
            args, ts = ev.get("args"), (ev.get("ts") or 0) / 1000
            args = dict(args) if isinstance(args, dict) else args
            if isinstance(args, dict) and isinstance(args.get('_tm'), dict):
                args['_tm'] = dict(args['_tm'], frame_id=self.frame_id(source.get('frame')))
            if isinstance(args, dict) and "value" in args:
                # Keep only match tokens and a short digest, never the raw canvas image.
                value = args.pop("value")
                kind = exfil.value_kind(ev.get("api"), args)
                if isinstance(value, str):
                    args["value_sha256"] = hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:16]
                for enc, tok, ci in exfil.value_tokens(kind, value) if kind else ():
                    values.append((vid, script[:1000], sdom, kind, enc, tok, int(ci), ts))
            rows.append((vid, ev.get("frame_url") or frame_url, script[:1000], sdom,
                         int(bool(sdom) and sdom != self.site_domain), ev.get("api"),
                         json.dumps(args) if args is not None else None, ts))
        self.db.executemany("INSERT INTO api_calls VALUES (?,?,?,?,?,?,?,?)", rows)
        if values:
            self.db.executemany("INSERT INTO fp_values VALUES (?,?,?,?,?,?,?,?)", values)


async def flush_frames(ctx):
    """Await binding delivery at phase boundaries, tolerating detached frames."""
    async def flush(frame):
        try:
            await asyncio.wait_for(frame.evaluate("() => window.__tmFlush && window.__tmFlush()"), 2)
        except Exception:
            pass
    await asyncio.gather(*(flush(frame) for page in ctx.pages for frame in page.frames))


async def interact(page, dwell, rng):
    t_end = time.monotonic() + dwell
    try:
        for _ in range(4):
            await page.mouse.move(rng.randint(50, 1200), rng.randint(50, 700), steps=6)
            await page.mouse.wheel(0, rng.randint(300, 900))
            await page.wait_for_timeout(600)
    except Exception:
        pass
    rem = t_end - time.monotonic()
    if rem > 0:
        await page.wait_for_timeout(rem * 1000)


async def goto(page, url, timeout):
    try:
        # Don't require every ad/image to finish loading to measure the document.
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        status = resp.status if resp else None
        return status, navigation_error(status)
    except Exception as e:
        message = str(e).splitlines()[0] if str(e) else type(e).__name__
        return None, message[:300]


async def navigate(page, url, phase, args, st, rng):
    """Persist the outcome before rejecting an unusable site measurement."""
    vid = st.new_visit(url, phase)
    status, error = await goto(page, url, args.timeout)
    if error is None:
        try:
            await interact(page, args.dwell, rng)
        except Exception as e:
            error = (str(e).splitlines()[0] if str(e) else type(e).__name__)[:300]
    await flush_frames(page.context)
    st.end_visit(vid, status, error, page.url)
    artifacts = getattr(args, "page_artifacts", None)
    if artifacts:
        directory = Path(artifacts)
        directory.mkdir(parents=True, exist_ok=True)
        stem = hashlib.sha256((st.run + ":" + st.site).encode()).hexdigest()[:16] + "-" + str(vid)
        evidence = dict(run=st.run, site=st.site, phase=phase, url=page.url, status=status, navigation_error=error)
        try:
            await page.screenshot(path=str(directory / (stem + ".png")), timeout=8000)
            evidence["screenshot"] = stem + ".png"
        except Exception as capture_error:
            evidence["capture_error"] = str(capture_error)[:200]
        (directory / (stem + ".json")).write_text(json.dumps(evidence, indent=2) + "\n")
    if error:
        raise RuntimeError(f"{phase} {url}: {error}")
    return page.url


async def pick_subpages(page, site_domain, n, rng):
    try:
        hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    except Exception:
        return []
    cur = urldefrag(page.url)[0]
    seen, out = set(), []
    for h in hrefs:
        u = urldefrag(h)[0]
        p = urlparse(u)
        if p.scheme not in ("http", "https") or u in seen or u == cur:
            continue
        if etld1(u) != site_domain or p.path in ("", "/"):
            continue
        low = u.lower()
        if low.endswith(SKIP_EXT) or any(w in low for w in SKIP_WORDS):
            continue
        seen.add(u)
        out.append(u)
    return rng.sample(out, min(n, len(out)))


def reused_urls(spec, site):
    path, run = spec.rsplit(":", 1)
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT url FROM visits WHERE run=? AND site=? AND phase='subpage' ORDER BY id",
                           (run, site)).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


async def crawl_site(pw, args, db, domain, category):
    url = site_url(domain)
    st = SiteState(db, args.run, domain, category, etld1(url),
                   capture_payloads=getattr(args, "capture_request_payloads", False))
    rng = random.Random(f"{args.seed}:{domain}")
    profile = Path(tempfile.mkdtemp(prefix="tm_profile_"))
    ctx = None
    capture = None
    extension_info = []
    try:
        ctx = await launch(pw, profile, headless=not args.headful, extensions=args.extension,
                           proxy=args.proxy, gpc=args.gpc, locale=args.locale, timezone=args.timezone,
                           extra_args=args.chromium_arg)
        if args.extension:
            await asyncio.sleep(args.ext_warmup)  # let filter lists / rulesets initialise
            extension_info = await wait_for_extensions(ctx, args.extension)
            print(f"[{args.run}] {domain} extensions: {json.dumps(extension_info)}", file=sys.stderr)
        if not getattr(args,'screen_only',False):
            await ctx.expose_binding("__tmLog", st.on_log)
            await ctx.add_init_script(instrument_source(getattr(args, 'attribute_evidence', False)))
            ctx.on("request", st.on_request)
        if getattr(args, "script_artifacts", None):
            capture = ScriptCapture(db, st, args.script_artifacts)
            ctx.on("response", capture.on_response)
            ctx.on("requestfailed", capture.on_failed)
        ctx.on("requestfailed", st.on_request_failed)
        ctx.on("requestfinished", st.on_request_finished)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        if getattr(args, 'attribute_evidence', False):
            await attach_initiators(ctx, page, st)

        # 1. landing
        final_home = await navigate(page, url, "landing", args, st, rng)
        # The site's own domain may redirect (e.g. foo.com -> foo.co.uk); measure against the final one.
        if etld1(final_home):
            st.site_domain = etld1(final_home)

        # 2. consent action on the same page
        consent_vid = st.new_visit(final_home, "after_consent")
        res = await consent.apply_consent(page, args.consent, wait=args.consent_wait)
        await consent.save_diagnostics(page, res, args.consent_artifacts,
                                       f"{args.run}:{domain}")
        res["gpc_requested"] = args.gpc
        res["gpc"] = None
        res["extensions"] = extension_info
        await page.wait_for_timeout(args.dwell * 500)
        await flush_frames(ctx)
        st.end_visit(consent_vid, None, None, page.url, consent.dumps(res))

        # 3. reload homepage with stored consent state
        await navigate(page, final_home, "home_reload", args, st, rng)
        await consent.recheck_consent(page, res)
        await consent.save_diagnostics(page, res, args.consent_artifacts,
                                       f"{args.run}:{domain}", phase="home_reload")
        db.execute("UPDATE visits SET consent=? WHERE id=?", (consent.dumps(res), consent_vid))
        db.commit()

        # 4. subpages
        subs = reused_urls(args.reuse_urls_from, domain) if args.reuse_urls_from else []
        if not subs:
            subs = await pick_subpages(page, st.site_domain, args.subpages, rng)
        for su in subs[: args.subpages]:
            await navigate(page, su, "subpage", args, st, rng)
            await consent.recheck_consent(page, res, phase="subpage")
            await consent.save_diagnostics(page, res, args.consent_artifacts,
                                           f"{args.run}:{domain}", phase="subpage")

        st.visit_id = None
        # Extensions such as Privacy Badger can enable GPC themselves. Read it
        # after measurement logging stops so this observation adds no FP calls.
        try:
            res["gpc"] = await page.evaluate("() => navigator.globalPrivacyControl === true")
        except Exception:
            res["gpc"] = None
        res["gpc_source"] = "navigator.globalPrivacyControl on final page"
        db.execute("UPDATE visits SET consent=? WHERE id=?", (consent.dumps(res), consent_vid))
        for c in ([] if getattr(args,'screen_only',False) else await ctx.cookies()):
            db.execute("INSERT INTO cookies VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (args.run, domain, c["name"], c["domain"], c["value"][:1000], c.get("expires"),
                        int(etld1(c["domain"]) != st.site_domain), int(c.get("httpOnly", False)),
                        int(c.get("secure", False)), c.get("sameSite")))
        db.commit()
    finally:
        st.visit_id = None
        for interval in st.intervals:
            if interval[2] is None:
                interval[2] = time.time()
        if capture:
            await capture.finish()
        if ctx:
            try:
                await asyncio.wait_for(ctx.close(), 20)
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sites", required=True)
    ap.add_argument("--run", required=True, help="label for this run, e.g. baseline / ubol / optout")
    ap.add_argument('--screen-only',action='store_true',help='access/consent diagnostics only: no fingerprint instrumentation, network logging or cookie-value capture')
    ap.add_argument("--out", default="data/crawl.sqlite")
    ap.add_argument("--consent", choices=["none", "accept", "reject"], default="none")
    ap.add_argument("--gpc", action="store_true", help="send Sec-GPC: 1 and expose navigator.globalPrivacyControl")
    ap.add_argument("--extension", action="append", default=[], help="unpacked extension dir (repeatable)")
    ap.add_argument("--ext-warmup", type=float, default=8)
    ap.add_argument("--reuse-urls-from", help="DB:RUN whose subpage URLs to revisit, e.g. data/crawl.sqlite:baseline")
    ap.add_argument("--subpages", type=int, default=3)
    ap.add_argument("--dwell", type=float, default=10, help="seconds per page after load")
    ap.add_argument("--timeout", type=float, default=45, help="navigation timeout (s)")
    ap.add_argument("--site-timeout", type=float, default=300)
    ap.add_argument("--consent-wait", type=float, default=10)
    ap.add_argument("--consent-artifacts", help="save failed/unknown consent screenshots and control labels here")
    ap.add_argument("--script-artifacts", help="save loaded script sources by SHA256 here (2 MiB/script, 20 MiB/site)")
    ap.add_argument("--capture-request-payloads", action="store_true",
                    help="record request bodies up to 64 KiB/request and 2 MiB/site; includes sensitive local evidence")
    ap.add_argument("--attribute-evidence", action="store_true", help="capture bounded primitive browser attribute values for local evidence review")
    ap.add_argument("--page-artifacts", help="save measured navigation screenshots and outcomes for manual challenge-page review")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--proxy", help="e.g. socks5://127.0.0.1:1080 (use an EU exit for GDPR banners)")
    ap.add_argument("--locale", default="en-US")
    ap.add_argument("--timezone")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--seed", default="0")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--chromium-arg", action="append", default=[])
    args = ap.parse_args()
    if args.screen_only and (args.attribute_evidence or args.capture_request_payloads or args.script_artifacts):
        ap.error('--screen-only cannot be combined with fingerprint/source/payload evidence capture')

    db = open_db(args.out)
    done = set()
    for (site,) in db.execute("SELECT site FROM sites_done WHERE run=? AND ok=1", (args.run,)).fetchall():
        visits = [dict(zip(("phase", "status", "error", "ended"), row)) for row in db.execute(
            "SELECT phase,status,error,ended FROM visits WHERE run=? AND site=? ORDER BY id",
            (args.run, site))]
        problem = measurement_error(visits)
        if problem:
            # Old versions could mark failed navigations as completed. Retry them.
            db.execute("UPDATE sites_done SET ok=0,error=? WHERE run=? AND site=?",
                       (problem, args.run, site))
        else:
            done.add(site)
    # Failed/partial sites get retried from scratch: drop their partial rows.
    for (site,) in db.execute("SELECT DISTINCT site FROM visits WHERE run=?", (args.run,)).fetchall():
        if site not in done:
            db.execute("DELETE FROM request_payloads WHERE request_id IN "
                       "(SELECT r.id FROM requests r JOIN visits v ON v.id=r.visit_id WHERE v.run=? AND v.site=?)",
                       (args.run, site))
            db.execute("DELETE FROM script_sources WHERE visit_id IN (SELECT id FROM visits WHERE run=? AND site=?)", (args.run, site))
            db.execute("DELETE FROM api_calls WHERE visit_id IN (SELECT id FROM visits WHERE run=? AND site=?)", (args.run, site))
            db.execute("DELETE FROM fp_values WHERE visit_id IN (SELECT id FROM visits WHERE run=? AND site=?)", (args.run, site))
            db.execute("DELETE FROM requests WHERE visit_id IN (SELECT id FROM visits WHERE run=? AND site=?)", (args.run, site))
            db.execute("DELETE FROM visits WHERE run=? AND site=?", (args.run, site))
            db.execute("DELETE FROM cookies WHERE run=? AND site=?", (args.run, site))
    db.commit()
    sites = [s for s in load_sites(args.sites) if s[0] not in done][: args.limit]
    print(f"[{args.run}] {len(sites)} sites to crawl ({len(done)} already done)", file=sys.stderr)
    sem = asyncio.Semaphore(args.workers)

    async with async_playwright() as pw:
        async def one(i, domain, cat):
            async with sem:
                t0 = time.time()
                ok, err = 1, None
                try:
                    await asyncio.wait_for(crawl_site(pw, args, db, domain, cat), args.site_timeout)
                except Exception as e:
                    ok, err = 0, f"{type(e).__name__}: {str(e).splitlines()[0][:300] if str(e) else ''}"
                db.execute("INSERT OR REPLACE INTO sites_done VALUES (?,?,?,?,?)",
                           (args.run, domain, ok, err, time.time() - t0))
                db.commit()
                status=site_status(db,args.run,domain,args.consent,bool(ok))
                print(f"[{args.run}] {i + 1}/{len(sites)} {domain} {progress_line(status)} "
                      f"{('error=' + err) if err else ''} "
                      f"({time.time() - t0:.0f}s)", file=sys.stderr)

        await asyncio.gather(*(one(i, d, c) for i, (d, c) in enumerate(sites)))
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
