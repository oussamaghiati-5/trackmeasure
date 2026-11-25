"""TCF v2 consent evidence and versioned GVL scope (not a legal-compliance test).

Format: IAB Tech Lab, Consent string and vendor list formats v2.
Only consent signals are verified; legitimate-interest objections are recorded
separately and are not implied by rejection of consent.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.request

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_GVL_CACHE = {}
_GVL_ERRORS = {}
_GVL_LOCK = threading.Lock()


class Bits:
    def __init__(self, value):
        if not value or any(c not in ALPHABET for c in value):
            raise ValueError("Invalid TC string alphabet")
        self.bits = "".join(f"{ALPHABET.index(c):06b}" for c in value)
        self.pos = 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.bits):
            raise ValueError("Truncated TC string")
        value = int(self.bits[self.pos:self.pos + n] or "0", 2)
        self.pos += n
        return value

    def flags(self, n):
        return {i for i in range(1, n + 1) if self.take(1)}

    def ranges(self, maximum, entries):
        result, last = set(), 0
        for _ in range(entries):
            multiple = self.take(1)
            start = self.take(16)
            end = self.take(16) if multiple else start
            if not last < start <= end <= maximum:
                raise ValueError("Invalid or overlapping TC vendor range")
            result.update(range(start, end + 1))
            last = end
        return result

    def vendors(self):
        maximum = self.take(16)
        return self.ranges(maximum, self.take(12)) if self.take(1) else self.flags(maximum)

    def finish(self):
        tail = self.bits[self.pos:]
        # The IAB's published example itself contains extra zero padding.
        # Accept zero padding, but never silently discard nonzero data.
        if "1" in tail:
            raise ValueError("Unexpected TC string trailing bits")


def decode(value):
    if not isinstance(value, str) or len(value) > 131072:
        raise ValueError("Missing or oversized TC string")
    parts = value.split(".")
    core = Bits(parts[0])
    if core.take(6) != 2:
        raise ValueError("Unsupported TC string version")
    created, updated = core.take(36), core.take(36)
    cmp_id, cmp_version = core.take(12), core.take(12)
    core.take(6)  # consent screen
    language = [core.take(6), core.take(6)]
    version, policy = core.take(12), core.take(6)
    specific = bool(core.take(1))
    core.take(1)  # non-standard texts
    features, purposes, interests = core.flags(12), core.flags(24), core.flags(24)
    treatment = bool(core.take(1))
    country = [core.take(6), core.take(6)]
    if not cmp_id or not version or not specific or any(v > 25 for v in language + country):
        raise ValueError("Invalid or non-service-specific TC string")
    vendors, vendor_interests = core.vendors(), core.vendors()
    restrictions = {}
    for _ in range(core.take(12)):
        purpose, kind = core.take(6), core.take(2)
        if not 1 <= purpose <= 24 or kind > 2:
            raise ValueError("Invalid publisher restriction")
        ids = core.ranges(65535, core.take(12))
        target = restrictions.setdefault(purpose, {})
        if set(target) & ids:
            raise ValueError("Duplicate publisher restriction")
        target.update({i: kind for i in ids})
    core.finish()
    disclosed, publisher = None, None
    seen = set()
    for value in parts[1:]:
        segment = Bits(value)
        kind = segment.take(3)
        if kind in seen:
            raise ValueError("Duplicate TC segment")
        seen.add(kind)
        if kind in (1, 2):  # 2 = legacy allowed-vendors, not disclosed-vendors
            ids = segment.vendors()
            if kind == 1:
                disclosed = ids
        elif kind == 3:
            publisher_purposes = segment.flags(24)
            publisher_interests = segment.flags(24)
            custom = segment.take(6)
            publisher = dict(purposes=publisher_purposes, interests=publisher_interests,
                             custom=segment.flags(custom), custom_interests=segment.flags(custom), custom_count=custom)
        else:
            raise ValueError("Unsupported TC segment")
        segment.finish()
    return dict(cmp_id=cmp_id, cmp_version=cmp_version, vendor_list_version=version, policy=policy,
                created=created, updated=updated, purposes=purposes, vendors=vendors, features=features,
                purpose_interests=interests, vendor_interests=vendor_interests, disclosed=disclosed,
                restrictions=restrictions, publisher=publisher, purpose_one_treatment=treatment)


def true_ids(mapping, maximum):
    if not isinstance(mapping, dict):
        raise ValueError("Missing TC consent map")
    result = set()
    for key, value in mapping.items():
        if not str(key).isdigit() or not 1 <= int(key) <= maximum or type(value) is not bool:
            raise ValueError("Invalid TC consent map")
        if value:
            result.add(int(key))
    return result


def validated(state):
    decoded = decode(state.get("tcString"))
    for field, key, maximum in (("purposeConsents", "purposes", 24),
                                ("vendorConsents", "vendors", 65535),
                                ("specialFeatureOptins", "features", 12)):
        if true_ids(state.get(field), maximum) != decoded[key]:
            raise ValueError("API consent maps disagree with the TC string")
    if state.get("cmpId") != decoded["cmp_id"]:
        raise ValueError("API CMP ID disagrees with the TC string")
    disclosed = state.get("disclosedVendors")
    if disclosed is not None:
        ids = true_ids(disclosed, 65535)
        if decoded["disclosed"] is not None and ids != decoded["disclosed"]:
            raise ValueError("Disclosed vendor maps disagree with the TC string")
        decoded["disclosed"] = ids
    return decoded


def load_gvl(version, policy):
    """Fetch an immutable archived GVL server-side; never via instrumented pages."""
    key = (version, 3 if policy >= 4 else 2)
    with _GVL_LOCK:
        if key in _GVL_CACHE:
            return _GVL_CACHE[key]
        error = _GVL_ERRORS.get(key)
        if error and time.monotonic() - error[0] < 30:
            raise ValueError("Matching GVL temporarily unavailable: " + error[1])
        root = Path(os.environ.get("TRACKMEASURE_GVL_CACHE", str(Path(__file__).resolve().parent / "data/gvl")))
        path = root / f"v{key[1]}-vendor-list-v{version}.json"
        url = f"https://vendor-list.consensu.org/v{key[1]}/archives/vendor-list-v{version}.json"
        raw = path.read_bytes() if path.exists() else None
        if raw is None:
            request = urllib.request.Request(url, headers={"User-Agent": "trackmeasure-research/1.0"})
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=8) as response:
                    raw = response.read(10_000_001)
            except Exception as exc:
                _GVL_ERRORS[key] = (time.monotonic(), type(exc).__name__)
                raise
        if len(raw) > 10_000_000:
            raise ValueError("Oversized GVL")
        data = json.loads(raw)
        if data.get("vendorListVersion") != version or not isinstance(data.get("vendors"), dict):
            raise ValueError("GVL does not match the TC string version")
        if not path.exists():
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as f:
                f.write(raw)
                temporary = f.name
            os.replace(temporary, path)
        result = data, dict(version=version, url=url, cache_path=str(path), sha256=hashlib.sha256(raw).hexdigest())
        _GVL_CACHE[key] = result
        return result


def vendor_scope(decoded, gvl):
    disclosed = decoded["disclosed"]
    if not disclosed:
        raise ValueError("No disclosed-vendor scope available for acceptance")
    required, purposes = set(), set()
    for vendor_id in disclosed:
        vendor = gvl["vendors"].get(str(vendor_id))
        if not isinstance(vendor, dict) or not isinstance(vendor.get("purposes"), list):
            raise ValueError("Disclosed vendor missing from the matching GVL")
        consent = set(vendor["purposes"])
        flexible = set(vendor.get("flexiblePurposes", []))
        if any(type(p) is not int or not 1 <= p <= 24 for p in consent | flexible):
            raise ValueError("Invalid GVL purpose declaration")
        for purpose, vendors in decoded["restrictions"].items():
            restriction = vendors.get(vendor_id)
            if restriction in (0, 2):
                consent.discard(purpose)
            elif restriction == 1 and purpose in consent | flexible:
                consent.add(purpose)
        if decoded["purpose_one_treatment"]:
            consent.discard(1)
        if consent:
            required.add(vendor_id)
            purposes.update(consent)
    if not required or not purposes:
        raise ValueError("No consent-requiring vendor scope available")
    return dict(vendors=sorted(required), purposes=sorted(purposes), disclosed=sorted(disclosed))


def assess(action, state, require_action_event=True):
    if state.get("gdprApplies") is not True or state.get("cmpStatus") != "loaded":
        return "unknown", "TCF is not loaded/applicable"
    event = state.get("eventStatus")
    if event != "useractioncomplete" and not (
            event == "tcloaded" and (not require_action_event or state.get("actionObserved") is True)):
        return "unknown", "TCF has no completed-choice event"
    try:
        decoded = validated(state)
    except ValueError as exc:
        return "unknown", "TCF state unreadable: " + str(exc)
    if action == "reject":
        publisher = decoded["publisher"] or {}
        if decoded["purposes"] or decoded["vendors"] or decoded["features"] or publisher.get("purposes") or publisher.get("custom"):
            return "failed", "TCF saved consent/feature flags do not match rejection"
        return "confirmed", "Saved choice matches request: TCF"
    scope = state.get("vendorScope")
    if not isinstance(scope, dict) or not scope.get("vendors") or not scope.get("purposes"):
        return "unknown", "TCF acceptance scope unavailable"
    if set(scope.get("disclosed", [])) != decoded["disclosed"] or not set(scope["vendors"]) <= decoded["disclosed"]:
        return "unknown", "TCF acceptance scope does not match disclosed vendors"
    publisher = decoded["publisher"] or {}
    if publisher.get("custom_count", 0) != len(publisher.get("custom", [])):
        return "failed", "TCF publisher custom consents do not match acceptance"
    if set(scope["vendors"]) <= decoded["vendors"] and set(scope["purposes"]) <= decoded["purposes"]:
        return "confirmed", "Saved choice matches request: TCF"
    return "failed", "TCF saved consent flags do not match acceptance in disclosed scope"


def decision(state):
    decoded = validated(state)
    result = {key: sorted(decoded[key]) for key in ("purposes", "vendors", "features")}
    publisher = decoded["publisher"] or {}
    result["publisher_purposes"] = sorted(publisher.get("purposes", []))
    result["publisher_custom"] = sorted(publisher.get("custom", []))
    return result
