"""Consent-banner automation: reject (opt-out) or accept via CMP JS APIs, falling back to
clicking buttons by accessible name (EN/FR/DE/ES/IT/PT/NL), incl. two-layer banners.
The resulting TCF v2 state is read back so you can check the opt-out actually registered."""
import json
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
    r"^\s*(manage (options|preferences|cookies|settings|choices)|cookie (settings|preferences)|customi[sz]e|"
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
  let done = false; setTimeout(() => { if (!done) res('timeout'); }, 2000);
  try { window.__tcfapi('getTCData', 2, (d, ok) => { done = true;
    if (!ok || !d) return res('error');
    const pc = (d.purpose && d.purpose.consents) || {};
    const vc = (d.vendor && d.vendor.consents) || {};
    res({ cmpId: d.cmpId, gdprApplies: d.gdprApplies, eventStatus: d.eventStatus,
          purposesConsented: Object.keys(pc).filter(k => pc[k]).map(Number),
          vendorsConsented: Object.keys(vc).filter(k => vc[k]).length });
  }); } catch (e) { done = true; res('error'); }
})"""

USP_JS = """() => new Promise(res => {
  if (typeof window.__uspapi !== 'function') return res(null);
  setTimeout(() => res('timeout'), 2000);
  try { window.__uspapi('getUSPData', 1, (d, ok) => res(ok && d ? d.uspString : 'error')); } catch (e) { res('error'); }
})"""


async def _eval(frame, js):
    try:
        return await frame.evaluate(js)
    except Exception:
        return None


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
                        return {"frame": frame.url[:200], "label": label}
            except Exception:
                continue
    return None


async def apply_consent(page, action, wait=10):
    """action in {'none','accept','reject'}. Returns a JSON-serialisable result dict."""
    res = {"action": action, "method": None, "detail": None}
    if action == "none":
        res["tcf"] = await _eval(page, TCF_JS)
        return res
    pat = REJECT if action == "reject" else ACCEPT
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        apis = await _eval(page.main_frame, JS_API[action])
        if apis:
            res.update(method="api", detail=apis)
            break
        hit = await click_matching(page, pat)
        if hit:
            res.update(method="click", detail=hit)
            break
        if action == "reject":
            first = await click_matching(page, MANAGE)
            if first:
                await page.wait_for_timeout(1500)
                second = await click_matching(page, REJECT) or await click_matching(page, SAVE)
                res.update(method="click-2layer", detail=[first, second])
                break
        await page.wait_for_timeout(1000)
    await page.wait_for_timeout(2000)
    res["tcf"] = await _eval(page, TCF_JS)
    res["usp"] = await _eval(page, USP_JS)
    return res


def dumps(res):
    return json.dumps(res, default=str)
