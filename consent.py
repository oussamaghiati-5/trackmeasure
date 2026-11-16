"""Consent-banner automation: reject (opt-out) or accept via CMP JS APIs, falling back to
clicking buttons by accessible name (EN/FR/DE/ES/IT/PT/NL), incl. two-layer banners.
Choices are confirmed only from readable TCF or Cookiebot state, not button clicks."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import time

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

JS_API = {
    "reject": """() => { const r = [];
      try { if (window.OneTrust && OneTrust.RejectAll) { OneTrust.RejectAll(); r.push('onetrust'); } } catch (e) {}
      try { if (window.Cookiebot && Cookiebot.decline) { Cookiebot.decline(); r.push('cookiebot'); } } catch (e) {}
      try { if (window.Didomi && Didomi.setUserDisagreeToAll) { Didomi.setUserDisagreeToAll(); r.push('didomi'); } } catch (e) {}
      try { if (window.UC_UI && UC_UI.denyAllConsents) { UC_UI.denyAllConsents(); UC_UI.closeCMP && UC_UI.closeCMP(); r.push('usercentrics2'); } } catch (e) {}
      try { if (window.__ucCmp && __ucCmp.denyAllConsents) { __ucCmp.denyAllConsents(); __ucCmp.saveConsents && __ucCmp.saveConsents(); r.push('usercentrics3'); } } catch (e) {}
      return r; }""",
    "accept": """() => { const r = [];
      try { if (window.OneTrust && OneTrust.AllowAll) { OneTrust.AllowAll(); r.push('onetrust'); } } catch (e) {}
      try { if (window.Cookiebot && Cookiebot.submitCustomConsent) { Cookiebot.submitCustomConsent(true, true, true); r.push('cookiebot'); } } catch (e) {}
      try { if (window.Didomi && Didomi.setUserAgreeToAll) { Didomi.setUserAgreeToAll(); r.push('didomi'); } } catch (e) {}
      try { if (window.UC_UI && UC_UI.acceptAllConsents) { UC_UI.acceptAllConsents(); UC_UI.closeCMP && UC_UI.closeCMP(); r.push('usercentrics2'); } } catch (e) {}
      try { if (window.__ucCmp && __ucCmp.acceptAllConsents) { __ucCmp.acceptAllConsents(); __ucCmp.saveConsents && __ucCmp.saveConsents(); r.push('usercentrics3'); } } catch (e) {}
      return r; }""",
}

TCF_JS = """() => new Promise(res => {
  if (typeof window.__tcfapi !== 'function') return res(null);
  let done = false, id = null;
  const finish = value => {
    if (done) return; done = true; clearTimeout(timer);
    if (id != null) { try { window.__tcfapi('removeEventListener', 2, () => {}, id); } catch (e) {} }
    res(value);
  };
  const timer = setTimeout(() => finish('timeout'), 2000);
  try { window.__tcfapi('addEventListener', 2, (d, ok) => {
    if (d && d.listenerId != null) id = d.listenerId;
    if (!ok || !d) return finish('error');
    const pc = d.purpose && d.purpose.consents;
    const vc = d.vendor && d.vendor.consents;
    finish({ cmpId: d.cmpId, cmpStatus: d.cmpStatus,
          gdprApplies: d.gdprApplies, eventStatus: d.eventStatus,
          purposeConsents: pc, vendorConsents: vc, specialFeatureOptins: d.specialFeatureOptins,
          purposesConsented: Object.keys(pc || {}).filter(k => pc[k]).map(Number),
          vendorsConsented: Object.keys(vc || {}).filter(k => vc[k]).length });
  }); } catch (e) { finish('error'); }
})"""

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
    tcf, cookiebot, usp = await asyncio.gather(
        _eval(frame, TCF_JS), _eval(frame, COOKIEBOT_JS), _eval(frame, USP_JS))
    return dict(frame=frame.url[:200], tcf=tcf, cookiebot=cookiebot, usp=usp)


def page_frames(page):
    """Main frame first; re-snapshot after each action because banners detach."""
    frames = []
    for frame in (page.main_frame, *page.frames):
        if not any(frame is other for other in frames):
            frames.append(frame)
    return frames


async def read_page_state(page):
    """Keep every frame's evidence; a button's frame need not own the CMP API."""
    gate = asyncio.Semaphore(8)

    async def read(frame):
        async with gate:
            return await read_state(frame)

    states = await asyncio.gather(*(read(frame) for frame in page_frames(page)))
    result = dict(frame=page.main_frame.url[:200], states=states)
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
    expected = action == "accept"
    checks = []
    tcf = state.get("tcf")
    if isinstance(tcf, dict):
        pc, vc = tcf.get("purposeConsents"), tcf.get("vendorConsents")
        event_ok = tcf.get("eventStatus") == "useractioncomplete" or (
            not require_action_event and tcf.get("eventStatus") == "tcloaded")
        maps_known = (isinstance(pc, dict) and bool(pc) and isinstance(vc, dict)
                      and all(type(v) is bool for v in [*pc.values(), *vc.values()]))
        if tcf.get("gdprApplies") is True and tcf.get("cmpStatus") == "loaded" and event_ok and maps_known:
            values = [*pc.values(), *vc.values()]
            # Rejection must also leave no explicit special-feature opt-ins.
            features = tcf.get("specialFeatureOptins")
            if isinstance(features, dict) and all(type(v) is bool for v in features.values()):
                matches = all(v is expected for v in values)
                if action == "reject":
                    matches = matches and not any(features.values())
                checks.append(("TCF", matches))
    cb = state.get("cookiebot")
    if isinstance(cb, dict) and cb.get("hasResponse") is True:
        values = [cb.get(k) for k in ("preferences", "statistics", "marketing")]
        if all(type(v) is bool for v in values):
            checks.append(("Cookiebot", all(v is expected for v in values)))
    if not checks:
        return "unknown", "No supported CMP reports a complete saved choice"
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
                        label = (await el.inner_text(timeout=1000)).strip()[:60]
                        await el.click(timeout=3000)
                        return {"frame": frame.url[:200], "label": label, "_frame": frame}
            except Exception:
                continue
    return None


async def apply_consent(page, action, wait=10):
    """action in {'none','accept','reject'}. Returns a JSON-serialisable result dict."""
    res = {"action": action, "method": None, "detail": None, "action_completed": False,
           "attempts": []}
    if action == "none":
        res.update(await read_page_state(page))
        res["outcome"], res["reason"] = assess_choice(action, False, res)
        return res
    pat = REJECT if action == "reject" else ACCEPT
    deadline = time.monotonic() + wait
    api_tried = []
    while time.monotonic() < deadline:
        apis = None
        for frame in page_frames(page):
            if any(frame is previous for previous in api_tried):
                continue
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
                second = await click_matching(page, REJECT) or await click_matching(page, SAVE)
                if second:
                    second.pop("_frame")
                res.update(method="click-2layer", detail=[first, second], action_completed=bool(second))
                res["attempts"].append(dict(method="click-2layer", detail=[first, second]))
                break
        await page.wait_for_timeout(1000)
    await page.wait_for_timeout(2000)
    res.update(await read_page_state(page))
    res["outcome"], res["reason"] = assess_choice(action, res["action_completed"], res)
    return res


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
        remaining = deadline - time.monotonic()
        # Retry inconclusive loading/missing state only. A readable opposite
        # choice or conflicting CMPs must not be waited away or clicked again.
        if outcome != "unknown" or "disagree" in reason or remaining <= 0:
            break
        await asyncio.sleep(min(0.2, remaining))
    res.setdefault("checks", []).append(dict(state, phase=phase, outcome=outcome, reason=reason,
                                            polls=polls, seconds=time.monotonic() - started))
    if outcome != "confirmed":
        res.update(outcome=outcome, reason=f"{phase}: " + reason)


async def save_diagnostics(page, res, directory, key, phase="after_consent"):
    """Optional local evidence for unsuccessful actions; never changes outcome."""
    if not directory or res.get("action") == "none" or res.get("outcome") == "confirmed":
        return
    stem = hashlib.sha256(key.encode()).hexdigest()[:16] + f"-{phase}-{time.time_ns()}"
    root = Path(directory)
    evidence = {"phase": phase, "url": page.url, "outcome": res.get("outcome"),
                "reason": res.get("reason"), "frames": []}
    try:
        root.mkdir(parents=True, exist_ok=True)
        for frame in page_frames(page):
            # Include labels that did not match our patterns; that is the point
            # of diagnostics. Limit size, but never use this list as verification.
            controls = await _eval(frame, """() => [...document.querySelectorAll(
                'button, a, [role="button"], input[type="button"], input[type="submit"]')]
                .filter(el => { const r = el.getBoundingClientRect();
                  const s = getComputedStyle(el);
                  return r.width && r.height && s.visibility !== 'hidden' && s.display !== 'none'; })
                .map(el => ({label: (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 200),
                             tag: el.tagName})).filter(el => el.label).slice(0, 200)""")
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
