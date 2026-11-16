"""Validate extension inputs and read actual MV3 initialization/settings."""
import asyncio
import json
import time
from pathlib import Path


def manifest_at(path):
    path = Path(path)
    try:
        manifest = json.loads((path / "manifest.json").read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"Extension manifest missing or invalid: {path}") from error
    if manifest.get("manifest_version") != 3:
        raise ValueError(f"Chromium requires an MV3 extension: {path}")
    return manifest


STATE_JS = """async () => {
  const m = chrome.runtime.getManifest();
  const dnr = chrome.declarativeNetRequest;
  const local = await chrome.storage.local.get(['filteringModeDetails']);
  const session = await chrome.storage.session.get(['filteringModeDetails']);
  const modes = session.filteringModeDetails || local.filteringModeDetails;
  const state = { id: chrome.runtime.id, name: m.name, version: m.version,
    manifest_version: m.manifest_version,
    enabled_rulesets: await dnr.getEnabledRulesets(),
    dynamic_rules: (await dnr.getDynamicRules()).length };
  if (modes) state.filtering_modes = modes;
  if (globalThis.badger) {
    const b = globalThis.badger;
    state.initialized = b.INITIALIZED === true;
    if (state.initialized) {
      state.seed_loaded = b.getPrivateSettings().getItem('doneLoadingSeed') === true;
      const s = b.getSettings();
      state.settings = Object.fromEntries(['learnLocally','sendDNTSignal','checkForDNTPolicy']
        .map(k => [k, s.getItem(k)]));
    }
  }
  return state;
}"""


async def wait_for_extensions(ctx, paths, timeout=30):
    reports = []
    for path in paths:
        manifest = manifest_at(path)
        expected_name = manifest["name"]
        if expected_name.startswith("__MSG_") and expected_name.endswith("__"):
            messages = json.loads((Path(path) / "_locales" / manifest["default_locale"] / "messages.json").read_text())
            expected_name = messages[expected_name[6:-2]]["message"]
        worker_path = manifest.get("background", {}).get("service_worker")
        base = dict(path=str(path), version=manifest.get("version"), manifest_version=3)
        if not worker_path:
            reports.append(dict(base, worker=False))  # e.g. the static fixture blocker
            continue
        end = time.monotonic() + timeout
        last_error = "service worker not found"
        while time.monotonic() < end:
            for worker in ctx.service_workers:
                if not worker.url.endswith("/" + worker_path.lstrip("/")):
                    continue
                try:
                    state = await asyncio.wait_for(worker.evaluate(STATE_JS),
                                                   timeout=min(5, max(0.1, end - time.monotonic())))
                    if state["version"] != manifest["version"] or state["name"] != expected_name:
                        last_error = "service worker does not match the requested extension name/version"
                        continue
                    required = {r["id"] for r in manifest.get("declarative_net_request", {}).get("rule_resources", [])
                                if r.get("enabled")}
                    if not required.issubset(state["enabled_rulesets"]):
                        last_error = "default static filters are not initialized"
                        continue
                    if Path(path).name == "ubol" and not state.get("filtering_modes"):
                        last_error = "uBOL filtering mode is not initialized"
                        continue
                    if Path(path).name == "privacybadger" and not (
                            state.get("initialized") and state.get("seed_loaded") and state["dynamic_rules"] > 0):
                        last_error = "Privacy Badger seed/filter initialization is incomplete"
                        continue
                    reports.append(dict(base, **state, worker=True))
                    break
                except Exception as error:
                    last_error = str(error).splitlines()[0][:200] if str(error) else type(error).__name__
            else:
                await asyncio.sleep(0.25)
                continue
            break
        else:
            raise RuntimeError(f"Extension failed to initialize: {path}: {last_error}")
    return reports
