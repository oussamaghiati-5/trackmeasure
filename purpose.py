"""Purpose labelling for detected fingerprinting scripts.

The detector in analyze.py says *that* a script used fingerprinting-style APIs.
This module estimates *why*, following the evidence ladder used in the paper:

  1. vendor signatures   known vendor domains and first-party path signatures
                         (hidden vendors served from the site's own domain)
  2. Tracker Radar       DuckDuckGo usage categories for third-party domains
  3. page context        FP-Redemption-style rules: present on every crawled page,
                         or only on login / checkout / payment pages
  4. behaviour           automation probes (navigator.webdriver) as a weak cue
  5. manual review       the stratified annotation sample (scripts/purpose_agreement.py)

Labels are multi-field: purpose_primary, purpose_secondary, page_context, party,
confidence (High / Medium / Low) and the evidence that produced them. "Unknown" is
a legitimate result and is preferred to a forced category.

Tracker Radar's 0-3 fingerprinting score measures how likely a domain is to use
fingerprinting APIs, not its purpose; only its categories feed the purpose.
Tracker Radar categories describe a domain *as a third party*, so they are never
applied to a site's own first-party scripts.
"""
import re
from urllib.parse import urlsplit

PURPOSES = ("Advertising", "Analytics", "Anti-fraud/security", "Functional", "CDN", "Unknown")
SECURITY, ADS, ANALYTICS, FUNCTIONAL, CDN, UNKNOWN = (
    "Anti-fraud/security", "Advertising", "Analytics", "Functional", "CDN", "Unknown")
STRONG = {"canvas", "canvas_font", "webrtc", "audio"}

# Tracker Radar category -> purpose, in priority order for the primary label.
# A domain that is both Advertising and Ad Fraud is primarily an ad-tech vendor
# whose fraud checks protect advertisers (Senol et al.'s "dual use").
TR_MAP = [
    (ADS, {"Advertising", "Ad Motivated Tracking", "Action Pixels"}),
    (ANALYTICS, {"Analytics", "Audience Measurement", "Third-Party Analytics Marketing",
                 "Session Replay", "Tag Manager"}),
    (SECURITY, {"Fraud Prevention", "Ad Fraud"}),
    (FUNCTIONAL, {"Online Payment", "SSO", "Federated Login", "Non-Tracking", "Social - Comment",
                  "Social - Share", "Social Network", "Support Chat Widget", "Consent Management Platform"}),
    (CDN, {"CDN", "Embedded Content"}),
]

# Vendor signatures: (vendor, primary, secondary, host regex, path regex).
# Host regexes match the script host; path regexes the URL path. Either may be None.
# Each entry was checked against the scripts observed in the EU batch.
SIGNATURES = [
    # Bot management / anti-fraud, third-party hosts
    ("HUMAN (PerimeterX)", SECURITY, None, r"(^|\.)(px-cloud\.net|perimeterx\.net|px-cdn\.net|pxchk\.net)$", None),
    ("DataDome", SECURITY, None, r"(^|\.)(datadome\.co|captcha-delivery\.com)$", None),
    ("AWS WAF Bot Control", SECURITY, None, r"(^|\.)awswaf\.com$", None),
    ("Cloudflare Turnstile/Bot Management", SECURITY, None, r"(^|\.)challenges\.cloudflare\.com$", None),
    ("Google reCAPTCHA", SECURITY, None, r"(^|\.)(gstatic\.com|google\.com|recaptcha\.net)$", r"/recaptcha/"),
    ("hCaptcha", SECURITY, None, r"(^|\.)hcaptcha\.com$", None),
    ("Arkose Labs", SECURITY, None, r"(^|\.)(arkoselabs\.com|funcaptcha\.com)$", None),
    ("ThreatMetrix", SECURITY, None, r"(^|\.)online-metrix\.net$", None),
    ("Forter", SECURITY, None, r"(^|\.)forter\.com$", None),
    ("Riskified", SECURITY, None, r"(^|\.)riskified\.com$", None),
    ("Signifyd", SECURITY, None, r"(^|\.)signifyd\.com$", None),
    ("Sift", SECURITY, None, r"(^|\.)(sift\.com|siftscience\.com)$", None),
    ("Stripe Radar (fraud signals)", SECURITY, FUNCTIONAL, r"(^|\.)m\.stripe\.network$", None),
    ("Spur Monocle (VPN/proxy detection)", SECURITY, None, r"(^|\.)spur\.us$", None),
    ("Alibaba security (AWSC/Baxia)", SECURITY, None, r"(^|\.)(alicdn\.com|aliexpress-media\.com)$",
     r"/(lzd_sec|AWSC|a227ca)/"),
    ("SHEIN Armor security SDK", SECURITY, None, r"(^|\.)ltwebstatic\.com$", r"/armor-libs/"),
    ("TikTok security SDK / captcha", SECURITY, None, r"(tiktokcdn|ttwstatic)", r"/(webmssdk|goofy-captcha|ttweb_webmssdk)"),
    # Ad-tech, including ad verification (fraud checks for advertisers = dual use)
    ("Integral Ad Science", ADS, SECURITY, r"(^|\.)adsafeprotected\.com$", None),
    ("Ad-Score (ad fraud detection)", ADS, SECURITY, r"(^|\.)ad-score\.com$", None),
    ("Invalid-traffic detection (ay.delivery)", ADS, SECURITY, r"(^|\.)ay\.delivery$", r"/ivt/"),
    ("X (Twitter) Ads pixel", ADS, None, r"(^|\.)ads-twitter\.com$", None),
    ("GumGum", ADS, None, r"(^|\.)gumgum\.com$", None),
    ("AnyClip / Prebid", ADS, None, r"(^|\.)anyclip\.com$", None),
    ("Wunderkind (BounceX)", ADS, ANALYTICS, r"(^|\.)bounceexchange\.com$", None),
    ("Mantis brand safety", ADS, None, r"(^|\.)mantis-intelligence\.com$", None),
    ("Prebid / header bidding", ADS, None, r"(^|\.)(script\.ac|fuseplatform\.net|yimg\.com)$", r"prebid"),
    ("InsurAds", ADS, None, r"(^|\.)insurads\.com$", None),
    ("Stay22 affiliate", ADS, None, r"(^|\.)stay22\.com$", None),
    ("Ad-recovery network (html-load family)", ADS, None,
     r"(^|\.)(html-load\.(cc|com)|artisticcoast\.(info|pro))$", None),
    # Analytics / marketing measurement
    ("AppsFlyer", ANALYTICS, ADS, r"(^|\.)(appsflyer\.com|appsflyersdk\.com)$", None),
    ("Adobe Experience Platform tags", ANALYTICS, ADS, r"(^|\.)adobedtm\.com$", None),
    ("Adobe Analytics (site tag manager)", ANALYTICS, None, None, r"Adobe-feature"),
    ("HubSpot analytics", ANALYTICS, None, r"(^|\.)hs-analytics\.net$", None),
    ("Microsoft Clarity", ANALYTICS, None, r"(^|\.)clarity\.ms$", None),
    ("Quantum Metric", ANALYTICS, None, r"(^|\.)quantummetric\.com$", None),
]

AD_RECOVERY = "Ad-recovery network (html-load family)"

# First-party path signatures: a vendor serving its script from the site's own
# domain ("hidden first-party vendor"). Corroborated by the vendor's cookies.
PATH_SIGNATURES = [
    ("Kasada bot defence", SECURITY, r"^/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/", "Kasada"),
    ("Akamai Bot Manager", SECURITY, r"^/akam/1\d/", "Akamai"),
    ("Cloudflare Bot Management", SECURITY, r"^/cdn-cgi/challenge-platform/", "Cloudflare"),
    ("Fastly bot challenge", SECURITY, r"^/_fs-ch-[A-Za-z0-9]+/", "Fastly"),
    ("HUMAN (PerimeterX) first-party", SECURITY, r"(^/px/|/PX[A-Za-z0-9]{8}/init\.js$|^/[A-Za-z0-9]{8}/init\.js$)", "HUMAN"),
    ("DataDome first-party", SECURITY, r"^/tags\.js$", "DataDome"),
    ("Forter (first-party/CDN mirror)", SECURITY, r"sha256-OBF", "Forter"),
    ("Groupon device profiling", SECURITY, r"^/(o/lite\.js|api/company_toolkit\.js)$", None),
]
# Akamai's sensor script lives on a random, extension-less path of >= 5 segments.
AKAMAI_RANDOM_PATH = re.compile(r"^(/[A-Za-z0-9_-]{1,40}){5,}$")
GUARD_HOSTS = re.compile(r"^(guard-svc|dp)\.")

COOKIE_VENDORS = {
    "Akamai": r"^(_abck|bm_sz|ak_bmsc|bm_sv|bm_mi|bm_so|bm_ss|bm_lso)$",
    "HUMAN": r"^(_px3|_pxvid|_pxhd|_px2|pxcts|_px)$",
    "DataDome": r"^datadome$",
    "Cloudflare": r"^(__cf_bm|cf_clearance)$",
    "Kasada": r"^(KP_UIDz.*|x-kpsdk.*)$",
    "AWS WAF": r"^aws-waf-token$",
    "Imperva": r"^(incap_ses_.*|visid_incap_.*|reese84)$",
    "Forter": r"^forterToken$",
    "Fastly": r"^_fs_ch_.*$",
}

PAGE_TYPES = [
    ("Login", r"(log-?in|sign-?in|sign-?up|register|/account|/auth|/my-?account)"),
    ("Checkout/payment", r"(checkout|/cart|basket|payment|/pay/|subscri|suscrip|/abo|premium|/plans?\b)"),
    ("Product", r"(/dp/|/product|/p/|/ip/|/item|/sku|/pd/)"),
    ("Article", r"(/20\d\d/\d\d/|/article|/story|/news/|/sites/[^/]+/20\d\d/|\.html?$|-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+)"),
]


# --- Harm: purpose x tracking capability -------------------------------------
# Harmful fingerprinting = fingerprinting whose purpose is to recognise the user
# for someone other than the service the user asked for (advertising, analytics,
# profiling). Useful fingerprinting serves the requested service itself
# (bot/fraud defence, functionality), the "strictly necessary" case of ePrivacy
# Art. 5(3). Purpose decides the class; capability ranks how much tracking a
# script can actually do and decides only scripts whose purpose is unknown.
TRACKING_PURPOSES = {ADS, ANALYTICS}
SERVICE_PURPOSES = {SECURITY, FUNCTIONAL}
TRACKING, DUAL_USE, SERVICE = "Tracking", "Dual-use", "Service"
UNDETERMINED_HIGH, UNDETERMINED_LOW = "Undetermined (high capability)", "Undetermined (low capability)"
HARM_CLASSES = (TRACKING, DUAL_USE, SERVICE, UNDETERMINED_HIGH, UNDETERMINED_LOW)
# Browser values the crawler controls and that only fingerprinting-style reads
# expose: the configured time zone and the Linux platform in the user agent.
# The time zone is the batch's --timezone setting (Europe/Amsterdam until 2026-11-22).
DEFAULT_TIMEZONE = "Europe/Amsterdam"
PLATFORM_VALUES = ("X11; Linux x86_64", "Linux x86_64")


def known_values(timezone=DEFAULT_TIMEZONE):
    return (timezone, *PLATFORM_VALUES)


KNOWN_VALUES = known_values()


def value_patterns(values=KNOWN_VALUES):
    """Raw, URL-encoded (once and twice), '+'-encoded and base64 forms of each value."""
    import base64
    from urllib.parse import quote
    pats = set()
    for v in values:
        for enc in (v, quote(v, safe=""), quote(quote(v, safe=""), safe=""), v.replace(" ", "+"),
                    quote(v, safe="").replace("%20", "+")):
            pats.add(enc)
        for pad in range(3):    # base64 of the value at each byte alignment, unstable edges dropped
            b = base64.b64encode(b"\0" * pad + v.encode()).decode().rstrip("=")
            # Leading chars that mix in preceding bytes, and the last two, which may
            # mix in following bytes, are not determined by the value alone.
            core = b[0 if pad == 0 else pad + 1: -2]
            if len(core) >= 8:
                pats.add(core)
    return sorted(pats, key=len, reverse=True)


def identifying_power(techniques):
    """High: a strong technique or >= 3 techniques combined; Medium: two weak ones."""
    techs = set(techniques or ())
    if techs & STRONG or len(techs) >= 3:
        return "High"
    return "Medium" if len(techs) == 2 else "Low"


def capability(power, cross_site, sends_attributes, writes_storage):
    """Tracking capability from identifying power, cross-site reach, observed
    attribute transmission and storage writes (the ingredients of an identifier)."""
    score = {"High": 2, "Medium": 1, "Low": 0}[power] + bool(cross_site) + bool(sends_attributes) \
        + bool(writes_storage)
    return "High" if score >= 3 else "Medium" if score == 2 else "Low"


def harm_class(primary, secondary, cap):
    if (primary in TRACKING_PURPOSES and secondary == SECURITY) or \
            (primary == SECURITY and secondary in TRACKING_PURPOSES):
        return DUAL_USE
    if primary in TRACKING_PURPOSES:
        return TRACKING
    if primary in SERVICE_PURPOSES:
        return SERVICE
    return UNDETERMINED_HIGH if cap == "High" else UNDETERMINED_LOW


def page_type(url):
    path = urlsplit(url or "").path.lower()
    if path in ("", "/"):
        return "Home"
    for name, rx in PAGE_TYPES:
        if re.search(rx, path):
            return name
    return "Other"


def path_template(url):
    """Collapse random-looking path segments so one vendor's URLs cluster together."""
    path = urlsplit(url or "").path
    out = []
    for seg in path.split("/")[1:]:
        hexfile = re.fullmatch(r"[0-9a-f]{8,}(\.[A-Za-z0-9]{1,5})?", seg, re.I)
        if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", seg, re.I):
            # UUIDs anywhere in the segment, e.g. a per-visit "<uuid>.js" file name.
            out.append(re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                              "{uuid}", seg, flags=re.I))
        elif hexfile:
            out.append("{hex}" + (hexfile.group(1) or ""))
        elif re.fullmatch(r"\d+", seg):
            out.append("{n}")
        elif re.fullmatch(r"[A-Za-z0-9_-]{6,}", seg) and re.search(r"\d", seg) and re.search(r"[A-Z]", seg) \
                and re.search(r"[a-z]", seg):
            out.append("{rand}")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def cookie_vendors(names):
    found = set()
    for name in names:
        for vendor, rx in COOKIE_VENDORS.items():
            if re.match(rx, name or ""):
                found.add(vendor)
    return found


def tr_purposes(categories):
    """Ordered, de-duplicated purposes for a list of Tracker Radar categories."""
    cats = set(categories or ())
    return [purpose for purpose, names in TR_MAP if cats & names]


def match_signature(url):
    parts = urlsplit(url or "")
    host, path = parts.hostname or "", parts.path
    for vendor, primary, secondary, host_rx, path_rx in SIGNATURES:
        if host_rx and not re.search(host_rx, host, re.I):
            continue
        if path_rx and not re.search(path_rx, path, re.I):
            continue
        return vendor, primary, secondary
    return None


def match_path_signature(url, site_cookie_vendors=()):
    parts = urlsplit(url or "")
    path, host = parts.path, parts.hostname or ""
    for vendor, primary, rx, cookie in PATH_SIGNATURES:
        if re.search(rx, path):
            return vendor, primary, cookie
    if GUARD_HOSTS.match(host) and "groupon" in host:
        return "Groupon device profiling", SECURITY, None
    # Corroborated by Akamai cookies or by an /akam/ beacon on the same site.
    if AKAMAI_RANDOM_PATH.match(path) and "Akamai" in site_cookie_vendors and len(path) >= 40:
        return "Akamai Bot Manager (sensor, random path)", SECURITY, "Akamai"
    return None


def label(script, radar=None, same_owner=False, site_cookie_vendors=(), cluster=None, site_vendors=()):
    """Label one script observation.

    script: dict with url, third_party (0/1), techniques (set), page_context,
            reads_webdriver (bool).
    radar:  compact Tracker Radar entry for the script domain, or None.
    cluster: dict(n_sites, vendor) when the script's path template is shared by
            >= 2 unrelated sites, else None.
    site_vendors: vendors matched by signatures on the same site in any run.
    """
    url = script.get("url") or ""
    third = bool(script.get("third_party")) and not same_owner
    evidence, vendor = [], None
    primary = secondary = None
    confidence = "Low"

    sig = match_signature(url)
    psig = None if sig else match_path_signature(url, site_cookie_vendors)
    party = "Third-party" if third else "First-party"

    if sig:
        vendor, primary, secondary = sig
        evidence.append("vendor signature")
        confidence = "Medium"
    elif psig:
        vendor, primary, cookie = psig
        evidence.append("path signature")
        confidence = "Medium"
        if not third:
            party = "Hidden first-party vendor"
        if cookie and cookie in site_cookie_vendors:
            evidence.append("vendor cookie")
            confidence = "High"

    tr = tr_purposes((radar or {}).get("categories")) if third else []
    if tr:
        evidence.append("Tracker Radar")
        if primary is None:
            primary = tr[0]
            secondary = tr[1] if len(tr) > 1 else None
            # CDN/embedded-content alone says where a script is hosted, not why it runs.
            confidence = "Low" if primary == CDN else "Medium"
        elif primary in tr:
            confidence = "High"

    if cluster and cluster.get("n_sites", 0) >= 2:
        evidence.append("path cluster")
        if not third and party == "First-party":
            party = "Hidden first-party vendor"
        if primary and confidence == "Medium" and cluster.get("vendor") == vendor:
            confidence = "High"

    ctx = script.get("page_context") or ""
    techs = set(script.get("techniques") or ())
    if primary is None:
        if ctx in ("Login only", "Checkout/payment only"):
            primary, evidence = SECURITY, evidence + ["page context"]
        elif ctx == "All pages" and script.get("reads_webdriver") and (techs & STRONG or len(techs) >= 3):
            primary, evidence = SECURITY, evidence + ["page context", "behaviour"]
        elif not third and path_template(url) == "/{uuid}" and AD_RECOVERY in site_vendors:
            # Extension-less UUID paths that appear when the ad-recovery domain is
            # blocked: the same network falling back to first-party URLs.
            primary, vendor = ADS, AD_RECOVERY + " (first-party fallback)"
            evidence = evidence + ["site co-occurrence"]
        elif techs == {"webgl"} and re.search(r"player", url, re.I):
            primary, evidence = FUNCTIONAL, evidence + ["page context"]
        if primary:
            confidence = "Low"
    elif ctx in ("Login only", "Checkout/payment only") and primary == SECURITY:
        evidence.append("page context")

    if primary is None:
        primary, confidence = UNKNOWN, "Low"
    if secondary == primary:
        secondary = None
    return dict(purpose_primary=primary, purpose_secondary=secondary or "", party=party,
                confidence=confidence, evidence="; ".join(dict.fromkeys(evidence)) or "none",
                vendor=vendor or "")
