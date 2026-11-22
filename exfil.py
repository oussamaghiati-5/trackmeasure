"""Value-level evidence that a fingerprint or identifier left the browser.

The API heuristics in analyze.py show that a script *computed* a fingerprint. Here we
look for the computed values themselves (raw, base64 or MD5/SHA-1/SHA-256 hex) in the
URL or body of later requests, and for identifier-like cookie values sent to a party
other than the cookie's owner. Results are a lower bound: values combined with other
attributes or hashed with custom functions (e.g. MurmurHash) cannot be recognised.
"""
import base64
import hashlib
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

MIN_RAW = 12
SLICE = 48
VALUE_KINDS = {"Canvas.toDataURL": "canvas"}
WEBGL_PARAMS = {37445: "webgl_vendor", 37446: "webgl_renderer"}
# Consent strings are sent to vendors by design; they are not tracking identifiers.
CONSENT_COOKIES = re.compile(
    r"^(euconsent(-v2)?|eupubconsent(-v2)?|optanon(consent|alertboxclosed)|cookieconsent|"
    r"cookiebot.*|usprivacy|didomi_token|_?uc_.*|addtl_consent|consentuuid|__cmpcc.*|gpp.*)$", re.I)
ID_TOKEN = re.compile(r"[A-Za-z0-9_-]{10,}")
EXFIL_COLUMNS = ["run", "site", "phase", "kind", "source_domain", "receiver_domain",
                 "receiver_third_party", "encoding", "requests", "example_url"]
SHARING_COLUMNS = ["run", "site", "phase", "cookie_name", "cookie_domain", "receiver_domain",
                   "requests", "example_url"]


def value_kind(api, args):
    if api in VALUE_KINDS:
        return VALUE_KINDS[api]
    if api == "WebGL.getParameter":
        return WEBGL_PARAMS.get((args or {}).get("p"))
    return None


def _hexes(text):
    data = text.encode("utf-8", "surrogatepass")
    return [(name, getattr(hashlib, name)(data).hexdigest()) for name in ("md5", "sha1", "sha256")]


def value_tokens(kind, value):
    """-> [(encoding, token, case_insensitive)] for one captured fingerprint value."""
    if not isinstance(value, str) or not value:
        return []
    out = []
    if kind == "canvas":
        payload = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
        if len(payload) >= 2 * SLICE:
            mid = len(payload) // 2
            out.append(("base64_slice", payload[mid:mid + SLICE], False))
        out += [(name, h, True) for name, h in _hexes(value)]
        out += [(name + "_payload", h, True) for name, h in _hexes(payload)]
    else:
        if len(value) >= MIN_RAW:
            out.append(("raw", value, False))
            out.append(("base64", base64.b64encode(value.encode()).decode().rstrip("="), False))
        out += [(name, h, True) for name, h in _hexes(value)]
    return out


def is_timestamp(tok, now=None):
    if not tok.isdigit() or not 9 <= len(tok) <= 13:
        return False
    now = now or time.time()
    v = int(tok)
    return abs(v - now) < 5 * 365 * 86400 or abs(v / 1000 - now) < 5 * 365 * 86400


def id_tokens(value):
    """Identifier-like substrings of a cookie value (>=10 chars, digit, varied, not a timestamp)."""
    v = unquote(value or "")
    return {t for t in ID_TOKEN.findall(v)
            if any(ch.isdigit() for ch in t) and len(set(t)) > 4 and not is_timestamp(t)}


def haystack(url, post):
    parts = [url or "", post or ""]
    parts += [unquote(p) for p in parts if "%" in p]
    text = "\n".join(parts)
    return text, text.lower()


def matches(token, ci, hay):
    text, low = hay
    return token.lower() in low if ci else token in text


def _table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


def _requests(con, run, site):
    """Requests that actually left the browser (not blocked by an extension)."""
    for url, post, domain, tp, ts, failure, phase in con.execute(
            "SELECT r.url, r.post_data, r.domain, r.third_party, r.ts, r.failure, v.phase "
            "FROM requests r JOIN visits v ON v.id=r.visit_id WHERE v.run=? AND v.site=?", (run, site)):
        if failure and "BLOCKED_BY_CLIENT" in failure:
            continue
        yield url, post, domain, tp, ts, phase


def _aggregate(hits, columns):
    counts, examples = defaultdict(int), {}
    for key, url in hits:
        counts[key] += 1
        examples.setdefault(key, (url or "")[:200])
    return [dict(zip(columns, (*key, n, examples[key]))) for key, n in counts.items()]


def find_exfil(con):
    """Fingerprint values (canvas, WebGL vendor/renderer) found in later requests."""
    if not _table_exists(con, "fp_values"):
        return []
    has_post = any(r[1] == "post_data" for r in con.execute("PRAGMA table_info(requests)"))
    if not has_post:
        return []
    tokens = defaultdict(list)
    for run, site, sdom, kind, enc, tok, ci, ts in con.execute(
            "SELECT v.run, v.site, f.script_domain, f.kind, f.encoding, f.token, f.ci, f.ts "
            "FROM fp_values f JOIN visits v ON v.id=f.visit_id"):
        tokens[(run, site)].append((sdom or "(inline/unknown)", kind, enc, tok, bool(ci), ts or 0))
    hits = []
    for (run, site), toks in tokens.items():
        for url, post, domain, tp, ts, phase in _requests(con, run, site):
            hay = haystack(url, post)
            for sdom, kind, enc, tok, ci, vts in toks:
                if (ts or 0) >= vts - 1 and matches(tok, ci, hay):
                    hits.append(((run, site, phase, kind, sdom, domain, tp, enc), url))
    return _aggregate(hits, EXFIL_COLUMNS[:-2])


def find_sharing(con, owner=None):
    """Identifier-like cookie values sent to a registrable domain other than the cookie's owner."""
    if owner is None:
        from common import etld1 as owner
    has_post = any(r[1] == "post_data" for r in con.execute("PRAGMA table_info(requests)"))
    cookies = defaultdict(list)
    for run, site, name, cdom, value in con.execute("SELECT run, site, name, domain, value FROM cookies"):
        if CONSENT_COOKIES.match(name or ""):
            continue
        toks = id_tokens(value)
        if toks:
            cookies[(run, site)].append((name, owner(cdom or ""), toks))
    hits = []
    for (run, site), items in cookies.items():
        for url, post, domain, tp, ts, phase in _requests(con, run, site):
            hay = haystack(url, post if has_post else None)
            for name, cown, toks in items:
                if domain and domain != cown and any(t in hay[0] for t in toks):
                    hits.append(((run, site, phase, name, cown, domain), url))
    return _aggregate(hits, SHARING_COLUMNS[:-2])


def analyze_db(path):
    if not Path(path).is_file():
        return [], []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return find_exfil(con), find_sharing(con)
    finally:
        con.close()
