#!/usr/bin/env python3
"""Browser integration checks for generic consent layouts. No real websites.

All HTTP requests are fulfilled by Playwright; use docker-compose.test.yml to
also disable the container's external network. Run with make verify-consent.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.async_api import async_playwright

from common import launch
import consent


TCF = """
window.choiceMade = false;
window.__tcfapi = (command, version, cb) => {
  if (command === 'removeEventListener') return cb(true);
  if (command !== 'addEventListener') return cb(null, false);
  const saved = (document.cookie.match(/(?:^|;\\s*)choice=([^;]+)/) || [])[1];
  const accepted = saved === 'accept';
  cb({listenerId: 1, cmpStatus: 'loaded', gdprApplies: true,
      eventStatus: window.choiceMade ? 'useractioncomplete' : saved ? 'tcloaded' : 'cmpuishown',
      purpose: {consents: {1: accepted, 3: accepted}}, vendor: {consents: {1: accepted}},
      specialFeatureOptins: {1: false}}, true);
};
"""


def cookiebot(method=True, persisted=False):
    initial = "document.cookie.includes('choice=reject')" if persisted else "false"
    return f"""
const saved = {initial};
window.Cookiebot = {{hasResponse: saved, consent: {{preferences: !saved, statistics: !saved, marketing: !saved}}}};
""" + ("""
Cookiebot.decline = () => {
  Cookiebot.hasResponse = true;
  Cookiebot.consent = {preferences: false, statistics: false, marketing: false};
  // This fixture also runs inside a cross-site iframe. Use a cookie that can
  // actually persist there, without disabling Chromium's privacy protections.
  document.cookie = 'choice=reject; Path=/; SameSite=None; Secure; Partitioned';
};
""" if method else "")


def fixture(kind, action):
    root_script, child_script = "", ""
    child = '<button onclick="parent.postMessage(\'choice\', \'https://publisher.test\')">Tout refuser</button>'
    if action == "accept":
        child = child.replace("Tout refuser", "Accetta tutto")
    if kind in ("parent-tcf", "two-layer", "defaults", "conflict"):
        root_script = TCF
        if kind != "defaults":
            root_script += f"""
addEventListener('message', e => {{
  if (e.origin !== 'https://cmp.test' || e.data !== 'choice') return;
  document.cookie = 'choice={action}; Path=/; SameSite=Lax; Secure';
  window.choiceMade = true;
  {"" if kind == "conflict" else "document.querySelector('iframe').remove();"}
}});
"""
        if kind == "two-layer":
            child = """<button id="manage" onclick="document.querySelector('#reject').hidden=false; this.remove()">Manage or reject</button>
<button id="reject" hidden onclick="parent.postMessage('choice', 'https://publisher.test')">Reject all cookies</button>"""
        elif kind == "defaults":
            child = '<button onclick="this.remove()">Reject all</button>'
        elif kind == "conflict":
            child_script = "window.Cookiebot = {hasResponse: true, consent: {preferences: true, statistics: true, marketing: true}};"
    elif kind in ("child-cookiebot", "lost-child-choice"):
        child_script = cookiebot(persisted=True)
        child = ""  # Only the child API can perform the action.
    elif kind == "delayed-api":
        root_script = "setTimeout(() => {" + cookiebot(persisted=True) + "}, 250);"
        child = ""
    elif kind == "unchanged":
        root_script = """window.Cookiebot = {hasResponse: true,
          consent: {preferences: true, statistics: true, marketing: true}, decline() {}};"""
        child = '<button onclick="this.remove()">Reject all</button>'
    return (f'<!doctype html><title>Publisher</title><script>{root_script}</script><iframe src="https://cmp.test/banner"></iframe>',
            f'<!doctype html><title>CMP</title><script>{child_script}</script>{child}')


async def main():
    cases = [("parent-tcf", "reject", "confirmed"), ("parent-tcf", "accept", "confirmed"),
             ("two-layer", "reject", "confirmed"), ("child-cookiebot", "reject", "confirmed"),
             ("lost-child-choice", "reject", "confirmed"),
             ("delayed-api", "reject", "confirmed"), ("defaults", "reject", "unknown"),
             ("conflict", "reject", "unknown"), ("unchanged", "reject", "failed")]
    report = []
    async with async_playwright() as pw:
        for kind, action, expected in cases:
            publisher, banner = fixture(kind, action)

            async def route_request(route):
                body = banner if route.request.url.startswith("https://cmp.test/") else publisher
                await route.fulfill(content_type="text/html", body=body)

            with tempfile.TemporaryDirectory(prefix="tm_consent_fixture_") as td:
                ctx = await launch(pw, Path(td))
                try:
                    await ctx.route("**/*", route_request)
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    await page.goto("https://publisher.test/", wait_until="load")
                    result = await consent.apply_consent(page, action, wait=3)
                    assert result["outcome"] == expected, (kind, action, result)
                    if expected == "confirmed":
                        if kind in ("child-cookiebot", "lost-child-choice"):
                            frame = next(f for f in page.frames if f.url.startswith("https://cmp.test/"))
                            stored = await frame.evaluate("() => document.cookie.includes('choice=reject')")
                            assert stored, (kind, "Fixture cookie was not stored in the child frame")
                        if kind == "lost-child-choice":
                            # Deliberately remove valid storage. Confirmation must
                            # be lost after reload, just as in the reported failure.
                            await ctx.clear_cookies()
                        await page.reload(wait_until="load")
                        await consent.recheck_consent(page, result)
                        reloaded = "unknown" if kind == "lost-child-choice" else "confirmed"
                        assert result["outcome"] == reloaded, (kind, "reload", result)
                    else:
                        await consent.save_diagnostics(page, result, "results/consent-fixtures", kind)
                        assert result.get("diagnostics"), (kind, "missing diagnostics")
                    report.append(dict(case=kind, action=action, initial_outcome=expected,
                                       outcome=result["outcome"]))
                    print(f"OK {kind}/{action}: {result['outcome']}", flush=True)
                finally:
                    await ctx.close()
    out = Path("results/consent-fixtures/report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"All {len(cases)} cross-frame consent fixture checks passed.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
