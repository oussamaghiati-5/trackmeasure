"""Consent-banner automation: reject (opt-out) or accept via CMP JS APIs, falling back to
clicking buttons by accessible name (EN/FR/DE/ES/IT/PT/NL), incl. two-layer banners.
Choices are confirmed only from readable TCF or Cookiebot state, not button clicks."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import time
import tcf

REJECT = re.compile(
    r"^\s*(reject( all)?( cookies)?|refuse( all)?|decline( all)?|deny( all)?|disagree|"
    r"i do not (accept|agree)|do not sell( or share)?( my( personal)? info(rmation)?)?|"
    r"opt[- ]out|continue without (accepting|agreeing)|(use |accept )?(only )?(strictly )?necessary( cookies)?( only)?|"
    r"tout refuser|refuser( tout)?|continuer sans accepter|alle ablehnen|ablehnen|nur notwendige( cookies)?|"
    r"rechazar( todo| todas)?|rifiuta( tutto)?|rejeitar( tudo)?|alles weigeren|weigeren)\s*$", re.I)
ACCEPT = re.compile(
    r"^\s*(accept( all)?( cookies)?|agree( (and|&) (close|continue))?|i agree|i accept|allow( all)?( cookies)?|"
    r"tout accepter|accepter( et (fermer|continuer))?|j'accepte|alle akzeptieren|akzeptieren|zustimmen|"
    r"aceptar( todo| todas)?|accetta( tutto)?|aceitar( tudo)?|alles accepteren)\s*$", re.I)
MANAGE = re.compile(
    r"^\s*(manage (options|preferences|cookies|settings|choices|or reject( cookies)?)|cookie (settings|preferences)|customi[sz]e|"
    r"more options|options|paramétrer( les cookies)?|personnaliser|gérer (les )?(options|cookies|préférences)|"
    r"einstellungen|configurar|impostazioni)\s*$", re.I)
SAVE = re.compile(
    r"^\s*(save( (and|&) exit| (my )?(choices|preferences|settings))?|confirm( my)? (choices|selection)|"
    r"enregistrer( et fermer)?|speichern|guardar|salva)\s*$", re.I)

# Observe explicit rejection + payment/subscription labels. Never click these.
# "Offered" deliberately does not assert that every free route is unavailable.
PAID_REJECT = re.compile(
    r"^\s*(?:reject(?: all)?(?: cookies)?\s+(?:and|&)\s+(?:subscribe|pay)\b|"
    r"refuser(?: tout| tous les cookies)?\s+et\s+(?:s['’]abonner|payer)\b|"
    r"(?:alle )?ablehnen\s+und\s+(?:abonnieren|bezahlen)\b|"
    r"rechazar(?: todo| todas)?\s+y\s+(?:suscribirse|pagar)\b|"
    r"rifiuta(?: tutto)?\s+e\s+(?:abbonati|paga)\b|"
    r"rejeitar(?: tudo)?\s+e\s+(?:assinar|pagar)\b|"
    r"(?:alles )?weigeren\s+en\s+(?:abonneren|betalen)\b).*\s*$", re.I)

SIGNALS_JS = """() => [
  ['cookiebot', !!window.Cookiebot], ['onetrust', !!(window.OneTrust || window.Optanon)],
  ['didomi', !!window.Didomi], ['usercentrics', !!(window.UC_UI || window.__ucCmp)],
  ['sourcepoint', !!window._sp_], ['tcf_api', typeof window.__tcfapi === 'function']
].filter(item => item[1]).map(item => item[0])"""

# Pay-or-OK ("consent or pay") walls: the only alternative to accepting is a subscription.
PAYWALL = re.compile(
    r"(subscri|abonn|\babo\b|pur[- ]?abo|werbefrei|ohne werbung|ad[- ]?free|without (ads|advertising)|"
    r"sans (pub|publicit)|suscri|assin|€|£|\$\s?\d|/\s?(month|mois|monat|mes|mese)\b|per month|par mois|pro monat)", re.I)
ACCEPT_LOOSE = re.compile(
    r"\b(accept|agree|allow|akzeptier|zustimm|einverstanden|accepter|j'accepte|acepta|accett|aceit|accepteren)", re.I)
CONTROLS_JS = """() => [...document.querySelectorAll(
    'button, a, [role="button"], input[type="button"], input[type="submit"]')]
    .filter(el => { const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width && r.height && s.visibility !== 'hidden' && s.display !== 'none'; })
    .map(el => ({label: (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 200),
                 tag: el.tagName})).filter(el => el.label).slice(0, 200)"""


def classify_pay_or_ok(labels):
    """True when visible controls offer accept or a paid option, but no free rejection."""
    labels = [label for label in labels if isinstance(label, str)]
    paid = [label for label in labels if PAYWALL.search(label)]
    accept = any(ACCEPT_LOOSE.search(label) and not PAYWALL.search(label) for label in labels)
    free_reject = any(REJECT.match(label) for label in labels)
    return bool(paid and accept and not free_reject), paid[:3]

JS_API = {
    "reject": """async () => { const r = [];
      try { if (window.OneTrust && OneTrust.RejectAll) { await OneTrust.RejectAll(); r.push('onetrust'); } } catch (e) {}
      try { if (window.Cookiebot && Cookiebot.decline) { await Cookiebot.decline(); r.push('cookiebot'); } } catch (e) {}
      try { if (window.Didomi && Didomi.setUserDisagreeToAll) { await Didomi.setUserDisagreeToAll(); r.push('didomi'); } } catch (e) {}
      try { if (window.UC_UI && UC_UI.denyAllConsents) { await UC_UI.denyAllConsents(); if (UC_UI.closeCMP) await UC_UI.closeCMP(); r.push('usercentrics2'); } } catch (e) {}
      try { if (window.__ucCmp && __ucCmp.denyAllConsents) { await __ucCmp.denyAllConsents(); if (__ucCmp.saveConsents) await __ucCmp.saveConsents(); r.push('usercentrics3'); } } catch (e) {}
      return r; }""",
    "accept": """async () => { const r = [];
      try { if (window.OneTrust && OneTrust.AllowAll) { await OneTrust.AllowAll(); r.push('onetrust'); } } catch (e) {}
      try { if (window.Cookiebot && Cookiebot.submitCustomConsent) { await Cookiebot.submitCustomConsent(true, true, true); r.push('cookiebot'); } } catch (e) {}
      try { if (window.Didomi && Didomi.setUserAgreeToAll) { await Didomi.setUserAgreeToAll(); r.push('didomi'); } } catch (e) {}
      try { if (window.UC_UI && UC_UI.acceptAllConsents) { await UC_UI.acceptAllConsents(); if (UC_UI.closeCMP) await UC_UI.closeCMP(); r.push('usercentrics2'); } } catch (e) {}
      try { if (window.__ucCmp && __ucCmp.acceptAllConsents) { await __ucCmp.acceptAllConsents(); if (__ucCmp.saveConsents) await __ucCmp.saveConsents(); r.push('usercentrics3'); } } catch (e) {}
      return r; }""",
}

TCF_BRIDGE = (Path(__file__).parent / "consent_tcf.js").read_text()
TCF_JS = f"() => ({TCF_BRIDGE})('read')"
TCF_OPERATIONS = {op: f"() => ({TCF_BRIDGE})('{op}')" for op in ("start", "arm", "stop")}

COOKIEBOT_JS = """() => {
  const c = window.Cookiebot;
  if (!c) return null;
  const s = c.consent || {};
  return { hasResponse: c.hasResponse, preferences: s.preferences,
           statistics: s.statistics, marketing: s.marketing };
}"""

USP_JS = """() => new Promise(res => {
  if (typeof window.__uspapi !== 'function') return res(null);
  setTimeout(() => res('timeout'), 2000);
  try { window.__uspapi('getUSPData', 1, (d, ok) => res(ok && d ? d.uspString : 'error')); } catch (e) { res('error'); }
})"""


async def _eval(frame, js):
    try:
        return await asyncio.wait_for(frame.evaluate(js), timeout=5)
    except Exception:
        return None


async def read_state(frame):
    tc, cookiebot, usp, signals = await asyncio.gather(
        _eval(frame, TCF_JS), _eval(frame, COOKIEBOT_JS), _eval(frame, USP_JS),
        _eval(frame, SIGNALS_JS))
    if isinstance(tc, dict) and any((tc.get("purposeConsents") or {}).values()):
        try:
            decoded = tcf.validated(tc)
            gvl, provenance = await asyncio.to_thread(tcf.load_gvl, decoded["vendor_list_version"], decoded["policy"])
            tc["vendorScope"] = tcf.vendor_scope(decoded, gvl)
            tc["vendorScope"]["gvl"] = provenance
        except Exception as exc:
            tc["scopeError"] = str(exc)[:200]
    return dict(frame=frame.url[:200], tcf=tc, cookiebot=cookiebot, usp=usp,
                cmp_signals=signals if isinstance(signals, list) else [])


def page_frames(page):
    """Main frame first; re-snapshot after each action because banners detach."""
    frames = []
    for frame in (page.main_frame, *page.frames):
        if not any(frame is other for other in frames):
            frames.append(frame)
    return frames


async def observe(page, operation):
    await asyncio.gather(*(_eval(frame, TCF_OPERATIONS[operation]) for frame in page_frames(page)))


async def read_page_state(page):
    """Keep every frame's evidence; a button's frame need not own the CMP API."""
    gate = asyncio.Semaphore(8)

    async def read(frame):
        async with gate:
            return await read_state(frame)

    states = await asyncio.gather(*(read(frame) for frame in page_frames(page)))
    result = dict(frame=page.main_frame.url[:200], states=states)
    result["cmp_signals"] = sorted({signal for state in states
                                    for signal in state.get("cmp_signals", [])})
    # Retain legacy summary fields for CSV readers. Assessment uses all states,
    # so selecting a convenient summary can never hide a contradictory CMP.
    for key in ("tcf", "cookiebot", "usp"):
        values = [state.get(key) for state in states if state.get(key) is not None]
        result[key] = next((v for v in values if isinstance(v, dict)), values[0] if values else None)
    return result


def assess_choice(action, completed, state, *, require_action_event=True):
    """Verify observed CMP flags. This does not establish tracking compliance.

    TCF defaults, stubs, missing fields and non-applicable GDPR are not evidence
    of a saved decision. USP's sale opt-out flag is not an all-cookie rejection.
    """
    if action == "none":
        return "not_requested", "No consent action requested"
    if not completed:
        return "failed", "Consent action was not completed"
    if "states" in state:
        outcomes = [assess_choice(action, completed, item, require_action_event=require_action_event)
                    for item in state["states"]]
        if any(outcome == "unknown" and "disagree" in reason for outcome, reason in outcomes):
            return "unknown", "Supported CMP reports disagree"
        known = [item for item in outcomes if item[0] in ("confirmed", "failed")]
        if not known:
            return "unknown", "No supported CMP reports a complete saved choice"
        if len({outcome for outcome, _ in known}) > 1:
            return "unknown", "Supported CMP reports disagree across frames"
        return known[0]
    checks = []
    tc = state.get("tcf")
    inconclusive = None
    if isinstance(tc, dict):
        outcome, reason = tcf.assess(action, tc, require_action_event)
        if outcome == "unknown":
            inconclusive = reason
        else:
            checks.append(("TCF", outcome == "confirmed"))
    cb = state.get("cookiebot")
    if isinstance(cb, dict) and cb.get("hasResponse") is True:
        values = [cb.get(k) for k in ("preferences", "statistics", "marketing")]
        if all(type(v) is bool for v in values):
            checks.append(("Cookiebot", all(v is (action == "accept") for v in values)))
    if not checks:
        return "unknown", inconclusive or "No supported CMP reports a complete saved choice"
    if all(matches for _, matches in checks):
        return "confirmed", "Saved choice matches request: " + ", ".join(name for name, _ in checks)
    if any(matches for _, matches in checks):
        return "unknown", "Supported CMP reports disagree"
    return "failed", "Saved CMP choice does not match the requested action"


def consent_record(value):
    """Read current/legacy JSON safely; a legacy click is never confirmation."""
    try:
        record = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        record = None
    if not isinstance(record, dict):
        return dict(action="unknown", outcome="unknown", reason="No readable consent record")
    result = dict(record)
    if result.get("action") not in ("none", "accept", "reject"):
        result.update(action="unknown", outcome="unknown", reason="No recorded consent action")
    elif result.get("action") == "none":
        result.update(outcome="not_requested", reason="No consent action requested")
    elif result.get("outcome") not in ("confirmed", "failed", "unknown"):
        result.update(outcome="unknown", reason="No recorded consent verification")
    return result


async def click_matching(page, pattern):
    """Click the first visible button/link whose accessible name matches, in any frame."""
    for frame in page.frames:
        for role in ("button", "link"):
            try:
                loc = frame.get_by_role(role, name=pattern)
                n = await loc.count()
                for i in range(min(n, 8)):
                    el = loc.nth(i)
                    if await el.is_visible():
                        label = (await el.get_attribute("aria-label", timeout=1000)
                                 or await el.inner_text(timeout=1000)
                                 or await el.get_attribute("value", timeout=1000) or "").strip()[:200]
                        await observe(page, "arm")
                        await el.click(timeout=3000)
                        return {"frame": frame.url[:200], "label": label, "_frame": frame}
            except Exception:
                continue
    return None


async def label_paid_rejection(page, res):
    """Annotate an unconfirmed rejection from visible labels, without clicking."""
    if res.get("action") != "reject" or res.get("outcome") == "confirmed":
        return
    controls = []
    for frame in page_frames(page):
        for role in ("button", "link"):
            try:
                loc = frame.get_by_role(role, name=PAID_REJECT)
                for i in range(min(await loc.count(), 8)):
                    el = loc.nth(i)
                    if await el.is_visible():
                        label = (await el.get_attribute("aria-label", timeout=1000)
                                 or await el.inner_text(timeout=1000)
                                 or await el.get_attribute("value", timeout=1000) or "").strip()[:200]
                        controls.append(dict(frame=frame.url[:200], label=label))
            except Exception:
                continue
    if controls:
        res["obstacle"] = "paid_rejection_offered"
        res["paid_rejection_controls"] = controls


def in_scope(state):
    for item in state.get("states", [state]):
        if isinstance(item.get("cookiebot"), dict):
            return True
        tc = item.get("tcf")
        if isinstance(tc, dict) and tc.get("gdprApplies") is True and tc.get("cmpStatus") == "loaded":
            return True
    return False


def decisions(state):
    result = {}
    for item in state.get("states", [state]):
        tc = item.get("tcf")
        if isinstance(tc, dict):
            try:
                value = tcf.decision(tc)
                key = "tcf:" + str(tc["cmpId"])
                if key in result and result[key] != value:
                    raise RuntimeError("TCF reports disagree across frames")
                result[key] = value
            except ValueError:
                pass
        cb = item.get("cookiebot")
        if isinstance(cb, dict) and cb.get("hasResponse") is True:
            value = {k: cb.get(k) for k in ("preferences", "statistics", "marketing")}
            if all(type(v) is bool for v in value.values()):
                if "cookiebot" in result and result["cookiebot"] != value:
                    raise RuntimeError("Cookiebot reports disagree across frames")
                result["cookiebot"] = value
    return result


def reason_code(outcome, reason):
    if outcome == "confirmed":
        return "confirmed"
    if "disagree" in reason:
        return "conflicting_state"
    if "action was not completed" in reason:
        return "action_not_completed"
    if "scope" in reason:
        return "scope_unreadable"
    if outcome == "failed":
        return "choice_mismatch"
    return "state_unreadable"


async def _actuate(page, action, res, deadline):
    pat = REJECT if action == "reject" else ACCEPT
    api_tried = []
    while time.monotonic() < deadline:
        await observe(page, "start")
        apis = None
        for frame in page_frames(page):
            if any(frame is previous for previous in api_tried):
                continue
            await observe(page, "arm")
            found = await _eval(frame, JS_API[action])
            if found:
                api_tried.append(frame)
                apis = found
                res["attempts"].append(dict(method="api", detail=apis, frame=frame.url[:200]))
                break
            # Missing APIs are probed again: CMP code may load after the page.
        if apis:
            res.update(method="api", detail=apis, action_completed=True)
            await page.wait_for_timeout(500)
            state = await read_page_state(page)
            if assess_choice(action, True, state)[0] == "confirmed":
                break
            # A callable API is not proof of success. Try the visible controls.
        hit = await click_matching(page, pat)
        if hit:
            hit.pop("_frame")
            res.update(method="click", detail=hit, action_completed=True)
            res["attempts"].append(dict(method="click", detail=hit))
            break
        if action == "reject":
            first = await click_matching(page, MANAGE)
            if first:
                first.pop("_frame")
                await page.wait_for_timeout(1500)
                second = await click_matching(page, REJECT)
                if second:
                    second.pop("_frame")
                res.update(method="click-2layer", detail=[first, second], action_completed=bool(second))
                res["attempts"].append(dict(method="click-2layer", detail=[first, second]))
                if not second:
                    res["obstacle"] = "settings_incomplete"
                break
        await page.wait_for_timeout(1000)
    await page.wait_for_timeout(2000)
    res.update(await read_page_state(page))
    res["outcome"], res["reason"] = assess_choice(action, res["action_completed"], res)
    if res["outcome"] == "confirmed":
        try:
            res["decision"] = decisions(res)
        except RuntimeError as exc:
            res.update(outcome="unknown", reason=str(exc))
    res["reason_code"] = reason_code(res["outcome"], res["reason"])
    await label_paid_rejection(page, res)
    if action == "reject" and not res["action_completed"]:
        labels = [c.get("label") for frame in page_frames(page)
                  for c in (await _eval(frame, CONTROLS_JS) or []) if isinstance(c, dict)]
        pay_or_ok, paid = classify_pay_or_ok(labels)
        if pay_or_ok:
            # Still a failed rejection, but a site design, not a detection gap.
            res.update(pay_or_ok=True, obstacle="pay_or_ok", reason_code="pay_or_ok",
                       pay_or_ok_labels=paid,
                       reason="Pay-or-OK: no free reject control (" + "; ".join(paid) + ")")
    return res


async def apply_consent(page, action, wait=10):
    """Only Cookiebot/loaded GDPR-applicable TCF is in consent experiment scope."""
    res = dict(action=action, method=None, detail=None, action_completed=False, attempts=[], obstacle=None)
    if action == "none":
        res.update(await read_page_state(page), scope="not_requested", outcome="not_requested",
                   reason="No consent action requested", reason_code="not_requested")
        return res
    deadline = time.monotonic() + max(0, wait)
    try:
        while True:
            await observe(page, "start")
            state = await read_page_state(page)
            if in_scope(state):
                res.update(scope="cookiebot_tcf", before=state)
                break
            if time.monotonic() >= deadline:
                states = state.get("states", [state])
                not_applicable = any(isinstance(s.get("tcf"), dict) and
                                     s["tcf"].get("gdprApplies") is False for s in states)
                tcf_present = any(s.get("tcf") or "tcf_api" in s.get("cmp_signals", []) for s in states)
                code = "gdpr_not_applicable" if not_applicable else "state_unreadable" if tcf_present else "unsupported_cmp"
                res.update(state, scope="not_applicable" if not_applicable else "unsupported", outcome="unknown",
                           obstacle=code, reason="Outside Cookiebot/loaded GDPR-applicable TCF scope",
                           reason_code=code)
                await label_paid_rejection(page, res)
                return res
            await asyncio.sleep(min(0.2, max(0, deadline - time.monotonic())))
        return await _actuate(page, action, res, deadline)
    finally:
        await observe(page, "stop")


async def recheck_consent(page, res, phase="home_reload", wait=2):
    """Allow a missing/loading CMP to appear, without reapplying the choice."""
    if res["action"] == "none" or res["outcome"] != "confirmed":
        return
    started = time.monotonic()
    deadline = started + max(0, wait)
    polls = 0
    while True:
        state = await read_page_state(page)
        polls += 1
        outcome, reason = assess_choice(res["action"], True, state, require_action_event=False)
        if outcome == "confirmed" and res.get("decision"):
            try:
                current = decisions(state)
                if any(current.get(k) != v for k, v in res["decision"].items()):
                    outcome, reason = "unknown", "Saved consent decision changed or disappeared"
            except RuntimeError as exc:
                outcome, reason = "unknown", str(exc)
        remaining = deadline - time.monotonic()
        # Retry inconclusive loading/missing state only. A readable opposite
        # choice or conflicting CMPs must not be waited away or clicked again.
        if outcome != "unknown" or "disagree" in reason or remaining <= 0:
            break
        await asyncio.sleep(min(0.2, remaining))
    res.setdefault("checks", []).append(dict(state, phase=phase, outcome=outcome, reason=reason,
                                            polls=polls, seconds=time.monotonic() - started))
    if outcome != "confirmed":
        res.update(outcome=outcome, reason=f"{phase}: " + reason,
                   reason_code="state_lost" if "disappeared" in reason else reason_code(outcome, reason))


async def save_diagnostics(page, res, directory, key, phase="after_consent"):
    """Optional local evidence for unsuccessful actions; never changes outcome."""
    if not directory or res.get("action") == "none" or res.get("outcome") == "confirmed":
        return
    stem = hashlib.sha256(key.encode()).hexdigest()[:16] + f"-{phase}-{time.time_ns()}"
    root = Path(directory)
    evidence = {"phase": phase, "url": page.url, "outcome": res.get("outcome"),
                "reason": res.get("reason"), "obstacle": res.get("obstacle"),
                "cmp_signals": res.get("cmp_signals", []),
                "paid_rejection_controls": res.get("paid_rejection_controls", []), "frames": []}
    try:
        root.mkdir(parents=True, exist_ok=True)
        for frame in page_frames(page):
            # Include labels that did not match our patterns; that is the point
            # of diagnostics. Limit size, but never use this list as verification.
            controls = await _eval(frame, CONTROLS_JS)
            evidence["frames"].append(dict(frame=frame.url[:200], controls=controls))
        try:
            screenshot = root / (stem + ".png")
            await page.screenshot(path=str(screenshot), timeout=5000)
            evidence["screenshot"] = str(screenshot)
        except Exception as exc:
            evidence["screenshot_error"] = type(exc).__name__
        path = root / (stem + ".json")
        path.write_text(json.dumps(evidence, indent=2) + "\n")
        res.setdefault("diagnostics", []).append(str(path))
    except Exception as exc:
        res.setdefault("diagnostic_errors", []).append(type(exc).__name__)


def dumps(res):
    return json.dumps(res, default=str)
