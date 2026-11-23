# trackmeasure

An instrumented web crawler that measures **how prevalent advanced tracking is on popular sites, and how well blockers and consent opt-outs actually stop it.**

Course research project (web/device tracking, *Defensive: Detection/Transparency* track); the output is a short ACM AsiaCCS-format paper.

## Research questions

1. How common are fingerprinting (canvas, canvas-font, WebRTC, audio, WebGL, attribute enumeration), behavioural-signal collection, storage abuse and cookie respawning on ~150 popular news, e-commerce and social/affiliate sites, and how often do fingerprint values and identifiers actually leave the browser, and to whom?
2. How much of it survives a content blocker (uBlock Origin Lite, Privacy Badger)?
3. Does rejecting consent through the site's CMP stop it, and does a Global Privacy Control signal? Both are measured separately and together; accepting is the upper bound.
4. Do identifiers respawn after cookies are cleared, including identifiers re-derived purely from the browser fingerprint?

Related work: FPTrace (fingerprinting bypasses opt-outs), Englehardt & Narayanan CCS'16 (detection heuristics), FP-Inspector S&P'21, Fouad et al. (cookie respawning, including fingerprint-based).

## How it works

```
sites.csv ──► crawler.py ──► SQLite ──► analyze.py ──► results/*.csv
                 │  ▲                       
     instrument.js  consent.py       respawn.py ──► respawn.sqlite
   (hooks ~70 APIs) (CMP opt-out)   (3-browser protocol)
```

### 1. Crawl (`crawler.py`, `instrument.js`)
One fresh Chromium profile per site. Every run uses the same four phases, so runs are directly comparable:

| Phase | What happens |
|---|---|
| `landing` | Homepage load, no banner interaction (pre-consent tracking) |
| `after_consent` | Accept / reject / nothing, depending on the run |
| `home_reload` | Homepage reloaded with the consent state stored |
| `subpage` x N | N same-site subpages (later runs reuse the baseline's URLs) |

`instrument.js` is injected into every frame before page scripts run. It wraps ~70 fingerprinting and storage APIs (canvas 2D/WebGL, audio, WebRTC, navigator/screen getters, fonts, `matchMedia`, localStorage/IndexedDB, `document.cookie`, `sendBeacon`) and attributes each call to the calling script via the `Error().stack`. Wrapped functions keep a native-looking `toString()`. Records are batched to Python every 250 ms.

It also records, per script: the *values* returned by `toDataURL` and WebGL's unmasked vendor/renderer (first 10 per API per frame; Python keeps only match tokens, never the image), canvas `save`/`restore` and canvas event listeners (for the E&N animation exclusion), and registrations of behavioural listeners (`mousemove`/`pointer*`, `key*`, `touch*`, `wheel`/`scroll`, `devicemotion`/`deviceorientation`, including `on*` properties) and Generic Sensor constructors. The crawler stores request bodies (POST/beacon, first 64 KB) and URLs up to 8,000 chars so values can be found in outgoing traffic.

### 2. Runs

| Run | Setup | Purpose |
|---|---|---|
| `baseline` | plain crawl | reference |
| `baseline2` | identical to baseline | noise floor (ad rotation etc.) |
| `ubol` | uBlock Origin Lite (MV3) | blocker effectiveness |
| `pbadger` | Privacy Badger | blocker effectiveness |
| `optout` | CMP reject + `Sec-GPC: 1` | does opt-out stop tracking? |
| `rejectonly` | CMP reject, no GPC | effect of the banner choice alone |
| `gpconly` | `Sec-GPC: 1`, no banner action | effect of GPC alone |
| `accept` | CMP accept | upper bound |
| `baseline3` | identical to baseline (optional) | variability of the noise floor |

### 3. Consent automation (`consent.py`)
Tries the CMP's JavaScript API (OneTrust, Cookiebot, Didomi, Usercentrics v2/v3), then visible controls if the API does not produce confirmation: a role-based button click with anchored multilingual regexes (EN/FR/DE/ES/IT/PT/NL), or a two-layer "manage, then reject/save" flow.

APIs are tried in the main page and child frames; missing APIs are retried within the consent wait because CMP code may load late. State is read concurrently across the current main/child frames after an action and on reload/subpages. This handles a cross-origin banner whose button updates the parent's API, even when the banner removes its own frame. Supported reports that disagree keep the result unknown; a convenient frame cannot override a contradictory supported report. Existing verification requirements remain unchanged, so unsupported CMPs and incomplete TCF events/maps still remain unknown. This change does not claim universal banner support or address every TCF timing/vendor-scope issue.

Consent outcomes are `confirmed`, `failed`, or `unknown`; baseline/blocker runs use `not_requested` when no choice was requested. Confirmation requires a completed action plus readable saved state from TCF or Cookiebot. A vanished banner, successful function call, default false flags, or `gdprApplies=false` alone cannot confirm rejection. TCF is read using `addEventListener` (the old `getTCData` command is deprecated); its completed-action state must match the request. Cookiebot must report a response and all three optional categories must match. Other CMPs may be actuated but remain unknown unless a supported state reader confirms them. USP sale-opt-out data is retained separately and is not treated as proof of an all-cookie rejection.

Confirmed choices are checked again on the homepage reload and each subpage. A missing/loading CMP is polled with a two-second retry budget (individual API reads also have timeouts); this accommodates APIs that initialize after page load. Rechecks never repeat consent actions. A readable opposite choice or conflicting supported reports downgrade confirmation immediately; persistently missing state becomes unknown. The action, attempts, evidence, subsequent checks (including polling count/time), outcome/reason, and GPC flag are stored in the existing consent JSON. Confirmation describes the observed CMP state, not a guarantee about tracking behavior or legal compliance. Failed/unknown consent does not make a successfully loaded website a navigation failure; raw observations stay available, but these site/runs are excluded from verified consent comparisons. Old requested-consent recordings without verification are unknown; the crawler's normal resume behavior still skips completed crawls, so use a new run label or database to collect fresh consent evidence.

**Pay-or-OK walls.** When a rejection fails, the visible controls are classified. An accept control plus a paid option ("subscribe", "abo", "€/month", ...) and no free reject control is recorded as `pay_or_ok` (the outcome stays `failed`; the reason starts with `Pay-or-OK`). This separates a site design (The Guardian's "Reject all and subscribe") from a detection gap. It is a candidate label: confirm each case with the saved screenshot.

Verification references: [TCF CMP API](https://github.com/InteractiveAdvertisingBureau/GDPR-Transparency-and-Consent-Framework/blob/master/TCFv2/IAB%20Tech%20Lab%20-%20CMP%20API%20v2.md), [Cookiebot browser API](https://www.cookiebot.com/en/developer/).

To test the frame handling, run `make verify-consent`. It rebuilds the image, runs the consent unit tests, and tests nine local browser cases with external networking disabled: parent TCF with a cross-origin accept/reject button that removes its frame, a settings step, a child Cookiebot API, deliberate loss of its saved choice, delayed API loading, unchanged defaults, conflicting reports and a no-op API/button. Positive cases must retain confirmation after reload; clearing stored consent must downgrade confirmation to unknown. Fixture pages use intercepted HTTPS URLs and the child uses a `SameSite=None; Secure; Partitioned` cookie, following [Chrome's iframe cookie guidance](https://developer.chrome.com/docs/modern-web-guidance/guides/security/security). Browser cookie protections are left enabled. These are synthetic checks, not evidence of real-site coverage.

Then run `make consent-check SITE=theguardian.com ACTION=reject` (or `ACTION=accept`). Each invocation uses a new timestamped database under `data/consent-check/`, leaving the original pilot unchanged. Failed/unknown actions save a screenshot and per-frame control labels under `results/consent-check/`; diagnostics cannot change eligibility. The command reports the consent outcome, records settings/source hash/VPN exit, and checks the exit stays fixed. Use the same command on sites outside the development sample before reporting coverage. Generic crawler runs can opt into these artifacts with `--consent-artifacts results/<folder>`.

### 4. Respawning (`respawn.py`)
Per site, three browsers on the same IP:

- **A1**: profile A, fingerprint F1. Snapshot cookies, localStorage, IndexedDB.
- **A2**: revisit with F1 after `--clear cookies` (same profile: *stateful* respawning) or `--clear all` (brand-new profile: *fingerprint-derived* respawning).
- **B**: control with a different fingerprint F2 (viewport, DPR, locale, timezone, hardwareConcurrency, deviceMemory, canvas noise).

A token is *respawned* if an ID-like value in an A1 cookie reappears in an A2 cookie and appears nowhere in B.

### 5. Analysis (`analyze.py`)
Detects fingerprinting per (script, frame, visit) using heuristics after Englehardt & Narayanan (CCS'16) as adapted by FP-Inspector:

| Technique | Heuristic |
|---|---|
| `canvas` | >=10 distinct chars drawn and a non-lossy readback >=16x16, and the script never calls `save`/`restore` or adds a canvas event listener (E&N animation exclusion, added 18 Nov 2026) |
| `canvas_font` | >=50 distinct fonts in `measureText` |
| `webrtc` | `createDataChannel` + `createOffer` |
| `audio` | oscillator + compressor + readback |
| `webgl` | `getParameter(37445/37446)` (extra, weaker) |
| `attr_enum` | >=12 distinct navigator/screen attributes read (extra, weaker) |

Headline metric: **survival** of (script eTLD+1, technique) pairs vs the baseline in the post-consent phases. Report blocker and opt-out survival relative to the `baseline2` noise floor, not to 1.0.

Value-level evidence ([exfil.py](exfil.py)) answers whether tracking actually happened, not just whether an API was called:

- **Exfiltration** (`fp_exfil.csv`): a captured canvas or WebGL value found, raw, base64 or as MD5/SHA-1/SHA-256 hex, in the URL or body of a later request that was not blocked. `comparison.csv` adds **exfiltration survival** over (receiver eTLD+1, value kind) pairs. This is a lower bound: values combined with other attributes or hashed with custom functions are not recognised.
- **Identifier sharing** (`id_sharing.csv`): identifier-like cookie values (>=10 chars, a digit, varied, not a timestamp) sent to a registrable domain other than the cookie's owner. Consent-string cookies are excluded.
- **Behavioural listeners** (`behavior.csv`): which scripts register input/motion listeners, grouped into pointer/keyboard/touch/scroll/motion families. Registration alone is not proof of behavioural tracking.

**Statistics.** `make stats` ([scripts/stats.py](scripts/stats.py)) pairs every site's survival in each condition with the same site's `baseline2` survival and reports the median difference with a bootstrap 95% CI and an exact sign test, for fingerprint and exfiltration survival. **Validation.** `make validation-sample` ([scripts/validation.py](scripts/validation.py)) draws a stratified sample of detections to label by hand; `python scripts/validation.py score ...` reports precision per technique with Wilson intervals.

Outputs in `results/`: `per_site.csv` (requests, third-party domains, blocked requests, FP calls, storage ops, cookies, per-technique counts, pre-consent FP scripts, consent action/method/outcome/reason, GPC, TCF purposes, and comparison eligibility), `fp_scripts.csv` (every detected script, including diagnostic observations), `comparison.csv` (eligible paired survival and exfiltration survival vs baseline), `consent_coverage.csv` (counts of all site/runs by consent action and outcome, including exclusions and pay-or-OK walls), `fp_exfil.csv`, `id_sharing.csv` and `behavior.csv`. Coverage counts eligible site/runs, not the number of matched baseline pairs. GPC is recorded as enabled/disabled/unknown; the `optout` treatment remains rejection plus GPC, so do not attribute its effect to rejection alone.

GPC records the observed `navigator.globalPrivacyControl` value on the final page, not just the CLI flag. The consent JSON also retains `gpc_requested`, the observation source, and the actual initialized extension metadata/settings. Privacy Badger enables GPC itself even when the crawler was not passed `--gpc`; treat its run as the extension's full default behavior, not blocking alone.

## Repository layout

```
crawler.py  instrument.js  consent.py  respawn.py  analyze.py  common.py
exfil.py                  value-level evidence: fingerprint exfiltration, identifier sharing
sites.csv                 150-site seed list (news / e-commerce / social+affiliate)
scripts/
  get_extensions.sh/.py   install the hash-pinned Chromium MV3 packages into ext/
  check_tranco.py         cross-check sites.csv against a dated Tranco list
  pick_respawn_sites.py   choose respawn candidates from the baseline cookies table
  stats.py                paired tests vs the baseline2 noise floor (make stats)
  validation.py           sample detections for manual labelling, score precision
extensions.lock.json      official package URLs, versions and SHA-256 checksums
paper/                    anonymised draft paper (ACM sigconf); make -C paper pdf
test/                     local fixture: publisher, aggressive tracker, consent-respecting
                          tracker, MV3 test blocker, and smoke.py (end-to-end assertions)
Dockerfile  docker-compose.yml  docker-compose.vpn.yml  Makefile  requirements.txt
```

## Running it

The crawl executes untrusted third-party JavaScript from ad-heavy sites, so run it in the container (ideally inside a VM), never directly on your host.

```bash
cp .env.example .env            # set your private WireGuard file's absolute path
make extensions                # installs pinned MV3 packages; portable on macOS/Linux
make verify-blockers           # actual extension startup/blocking, local fixture, no internet
python scripts/check_tranco.py  # record the printed Tranco list ID in the paper

make smoke                      # local fixture, no internet, no VPN
make baseline baseline2 baseline3 blockers optout rejectonly gpconly accept
python scripts/pick_respawn_sites.py data/crawl.sqlite --run baseline   # writes respawn_sites.csv
make respawn
make analyze stats validation-sample
```

### Real blocker setup

`extensions.lock.json` pins uBlock Origin Lite **2026.1110.1816** from its official Chromium release ZIP and Privacy Badger **2026.9.15.2** from EFF's official Chromium CRX3 download. The default Privacy Badger GitHub release source is MV2 and is not the Chromium build. The installer verifies the archive hash, manifest version/version number and worker before replacing installed directories; package code is not patched. Downloads are cached under gitignored `ext/.downloads/`. Keep this cache with the experiment: EFF's CRX URL is mutable, so a future changed download intentionally fails the pinned hash check rather than silently changing the experiment. To update versions, explicitly review/update the lock and rerun installation/verification.

The extension inputs stay mounted read-only. Each browser gets a private writable copy so Chromium can compile DNR filter metadata; copies are removed when the browser closes or launch fails. Readiness checks wait for enabled default rules and, for Privacy Badger, completed initialization and seed loading. Missing/incompatible extensions or incomplete initialization fail the site/run rather than proceeding without protection. Runtime IDs may differ between unpacked private copies; they are recorded with the settings.

`make verify-blockers` uses `docker-compose.test.yml` with `network_mode: none`. A local server impersonates the known tracker domain `adsrvr.org` through Chromium's resolver rules; no requests are sent to the real tracker. The baseline must execute both that script and a harmless first-party script. Each real blocker must prevent the tracker from reaching the server, produce `ERR_BLOCKED_BY_CLIENT`, and leave the harmless script working. The check also runs the actual crawler with Privacy Badger and verifies saved extension metadata and extension-enabled GPC. A report is written to `results/blocker_verification.json`; it is an installation/control check, not a measurement of real-site effectiveness. The regular smoke test still uses its separate artificial blocker.

In the pinned packages, observed uBOL defaults are **optimal** for all URLs with six enabled default static rulesets. Privacy Badger uses its bundled seed data with `learnLocally=false`, and sends its default privacy signals. The verifier records observed settings/rule counts rather than assuming them; rerun it after any package or browser change. Package sources: [uBOL release](https://github.com/uBlockOrigin/uBOL-home/releases/tag/2026.1110.1816), [EFF Chromium download](https://privacybadger.org/#direct-download).

### VPN setup (each teammate)

Generate your own WireGuard configuration for a fixed EU server (for example, a Proton VPN Free Netherlands server). Select Linux, leave port forwarding and VPN ad/tracker blocking off, and download the `.conf` outside this repository. Set `WIREGUARD_CONFIG_PATH` in your local `.env` to its absolute path, and `VPN_EXPECTED_COUNTRY=NL` for the Netherlands. Share code and results, never private keys or configuration files. Keep all experimental conditions on the same exit; separate teammates' exits should be recorded as separate batches.

The VPN overlay uses Gluetun's [custom WireGuard configuration mount](https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/custom.md). Only Gluetun receives the file, read-only; the crawler shares its network but receives no VPN secrets. A missing path fails instead of creating an empty directory. The image is pinned by digest, VPN DNS blocklists are disabled to avoid adding an extra blocker to every condition, and `.conf` files are excluded from both Git and the crawler build context.

Run `make build` if the crawler image is missing, then `make preflight-vpn` **before starting a crawl**. This host-side script starts Gluetun, checks the exit country and that it differs from direct Docker egress, and keeps a crawler container running while stopping Gluetun. Both HTTPS and direct-IP probes must fail with the VPN stopped. It restores Gluetun even after a failed probe, verifies the original exit returns, and writes `results/vpn_verification.json`. A successful IPv6 connection before stopping is recorded when available; a failed IPv6 connection alone does not demonstrate working IPv6 through the tunnel. This test briefly interrupts the VPN, so never run it alongside an experiment. It tests egress loss, not every possible LAN or DNS-leak scenario.

### Five-site diagnostic pilot

`pilot_sites.csv` is a convenience sample from the seed list, covering news, shopping, social and affiliate sites; it is not a representative study sample. Run `make pilot-baseline`, inspect the failures, then `make pilot-conditions`. The latter runs baseline2, reject+GPC, accept, uBOL and Privacy Badger only on successful baseline sites. Each browser runs headfully under Xvfb, with one worker, five-second dwell, one subpage at most, a 45-second navigation timeout and a 180-second site timeout. Subpage URLs are reused from baseline where available. The full study's default commands and database stay separate.

The pilot writes SQLite and input copies under `data/pilot/`, and logs, CSVs and settings/exit metadata under `results/pilot/`. It requires a Netherlands exit, checks that the exit stays fixed before/after each condition, and refuses to mix changed settings or exits into the same batch. It does not run the disruptive VPN-loss test during crawling. Baseline failures and unconfirmed consent remain diagnostic exclusions, not successful protection. Review the raw visit URLs for unexpected redirects or HTTP-200 challenge pages before interpreting any tracking counts.

Crawls resume: completed sites are skipped, failed ones are retried from scratch. The base image tag must match the pinned `playwright` version in `requirements.txt`.

Build through `make`: it passes your uid/gid (`PW_UID`/`PW_GID`) so the container user can write the bind-mounted `data/` and `results/`. With plain `docker compose build` the user stays uid 1001, which cannot write those folders on a host whose uid differs (e.g. most single-user Linux machines, uid 1000).

A completed navigation requires a successful HTTP document response and the DOM-content-loaded event; a timeout, missing response, HTTP error, or empty 204/205 response makes that site/run unsuccessful. Failed visits retain their status and error for diagnosis. Any attempted subpage failure invalidates the site/run, so an incomplete crawl cannot look like successful blocking. `per_site.csv` includes `measurement_ok` and `measurement_error`; comparisons and headline summaries require successful execution, valid visits, and either confirmed or unrequested consent. This also applies to older databases. Challenge pages served with HTTP 200 are not yet detected.

### Isolation checklist (before any real crawl)

The shared browser launcher explicitly sets `chromium_sandbox=True` for the crawler and respawn experiments, including extension runs, and rejects sandbox-disabling custom flags. The container runs as non-root `pwuser` with `init`, `shm_size: 2gb` (not `ipc: host`) and `cap_drop: [ALL]`. (`no-new-privileges` is not set: it broke every `exec` on the dev host.) Only `./data` (rw), `./ext` (ro) and `./results` (rw) are mounted; there is no Docker socket and no host network. The VPN overlay deliberately sets no `FIREWALL_OUTBOUND_SUBNETS`, so crawled pages can't reach your LAN.

Compose uses `security/seccomp_profile.json`, based on [Playwright v1.63.0's profile](https://github.com/microsoft/playwright/blob/v1.63.0/utils/docker/seccomp_profile.json) (upstream SHA-256 `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`). It allows namespace creation as described in the [Playwright Docker guide](https://playwright.dev/python/docs/docker#crawling-and-scraping). Our sole addition permits the `chroot` syscall: with all container capabilities dropped, the upstream capability-gated rule is omitted, even though Chromium needs chroot after creating its own user namespace. Kernel permission checks still apply; the crawler receives no additional container capabilities, and the profile retains its default-deny policy.

1. **Snapshot the VM** (`VBoxManage snapshot "<vm>" take pre-crawl` or `virsh snapshot-create-as <vm> pre-crawl`). Disable writable shared folders, clipboard sharing and drag-and-drop, keeping only the folder used to export `data/`.
2. **`make preflight`** checks non-root uid, no Docker socket, no `.env` in the image, and zero effective container capabilities. It uses the actual shared launcher, checks Chromium's effective command line, and requires `chrome://sandbox` to report layer-1 isolation, PID/network namespaces, and Seccomp-BPF enabled. Missing/disabled status fails the check. It also prints the configured mounts for inspection. If preflight fails, fix the runtime's namespace/seccomp support; do not fall back to `--no-sandbox`, privileged mode, or an unconfined seccomp profile.
3. **`make preflight-vpn`** checks the exit country/IP, loss of crawler connectivity when Gluetun stops, and recovery with the same exit. It briefly interrupts the VPN: run only before crawling. See the VPN setup section and `results/vpn_verification.json`.
4. Use the same exit for every run of one experiment, and record it.

Without Docker: `pip install -r requirements.txt && python -m playwright install --with-deps chromium`.

**Timing.** Defaults are 10 s dwell, 3 subpages and up to 10 s consent wait, about 1.5-2.5 min per site. With `--workers 4`, 150 sites take roughly 1-1.5 h per run.

## Smoke test

`make smoke` (or `python test/smoke.py`) starts the fixture server, runs every crawl mode and both respawn modes, and asserts: all 6 techniques detected on `tracker.test`; blocker survival 0.0 with blocked requests > 0; opt-out survival 1.0 (the fixture tracker ignores consent); respawn finds `_trk` (localStorage-backed) and, with `--clear all`, `_fpid` as fingerprint-derived.

`make smoke` first runs the regression tests (`python -m unittest discover -s test -p 'test_*.py'`). The browser smoke test also visits deliberate HTTP 404/503 pages and checks they are recorded as failures and excluded from comparisons. Consent fixtures cover a confirmed decision, a button that leaves consent enabled, a click with no readable state, and an incomplete settings flow. Their raw observations are retained while unverified consent comparisons are excluded. Regression tests cover legacy records, default versus saved decisions, API-to-button fallback, conflicting evidence, and state loss after navigation.

## Status

**Where we are (23 November 2026): methodology stage.** The tooling is built and verified on local fixtures; no real-site experiment has run yet. We will run the full crawl once the group has agreed on the methodology. The paper draft holds placeholders (`[TBD]`) for every result. See [IMPROVEMENTS.md](IMPROVEMENTS.md) for the detailed handoff and ordered tasks.

| Area | State |
|---|---|
| Crawler, instrumentation, analysis | Done. Includes the 18 Nov additions: fingerprint exfiltration, identifier sharing, behavioural listeners, E&N canvas exclusion, pay-or-OK, `rejectonly`/`gpconly`/`baseline3`, `make stats`, `make validation-sample`, host-uid container user |
| Isolation (container, sandbox, seccomp, VPN) | Done and verified: `make preflight` and `make preflight-vpn` pass (Netherlands exit; IPv6 through the tunnel not verified) |
| Blockers | Pinned uBOL and Privacy Badger initialise and block the controlled request (`make verify-blockers`) |
| Consent verification | `make verify-consent` passes all nine fixtures; one real Cookiebot rejection confirmed after reload |
| Tests | `make smoke` passes (76 unit tests plus the browser smoke test), last on 18 Nov on a uid-1000 Linux host |
| Five-site pilot | Ran 16 Nov, diagnostic only, before the consent and canvas-rule updates (details below) |
| Site list, Tranco cross-check, respawn subset | Not done: `check_tranco.py` and `pick_respawn_sites.py` not yet exercised |
| Full experiment (150 sites, all runs) | Not run |
| Paper (`paper/`) | Draft compiles (5 pages, acmart sigconf). Authors anonymised for now (`anonymous` class option); results are placeholders; three citations marked `VERIFY` plus two missing references (behavioural tracking, pay-or-OK) |

**Next steps:** agree on the methodology as a group, then
1. Test consent on 5-10 varied real sites (including a TCF site), and re-check the Guardian for `pay_or_ok`.
2. Finalise the site list with `check_tranco.py`, making sure enough sites expose TCF or Cookiebot state so the verified opt-out comparison has enough sites.
3. Re-run the pilot with the current code, then the full runs, `make analyze stats validation-sample`, and manual labelling.
4. Fill the paper's results, verify citations and add authors.

**Pilot details (16 Nov 2026).** The Guardian, Le Monde and Reddit completed baseline and all five comparison conditions; IKEA failed on a homepage reload with HTTP 403 and Slickdeals failed on landing with HTTP 403. All three reject actions failed to complete; all three accept actions clicked a control but remained unconfirmed, so these consent conditions are excluded from verified comparisons. uBOL and Privacy Badger recorded 69 and 6 blocked requests across the three sites. These are diagnostic observations, not effectiveness estimates. Artifacts are in `data/pilot/` and `results/pilot/`. A later Guardian check still did not complete rejection: its banner offered a paid subscription instead of a free reject-all button, which the new pay-or-OK classification is meant to label.

## Pitfalls (read before the real crawl)

1. **Geolocation.** From a Moroccan IP most CMPs show no GDPR banner and `gdprApplies` is false. Run *all* runs through the same EU exit (gluetun sidecar), plus a separate US exit for CCPA "do not sell". Only confirmed choices support verified opt-out claims; inspect `consent_coverage.csv` for failures and unknown cases. A non-empty `consent_method` alone is insufficient.
2. **MV2 extensions won't load** in current Chromium. Use the pinned MV3 Chromium packages for uBlock Origin Lite and Privacy Badger, not Privacy Badger's default MV2 source branch. Filtering modes change across releases: this pinned uBOL uses optimal mode. Inspect `blocker_verification.json` and saved run metadata rather than assuming an older basic-mode default. MV3 blockers can't do everything MV2 uBO could.
3. **Noise floor.** Always run `baseline2`.
4. **Bot detection.** Headless runs can change tracker behaviour; use `--headful` under `xvfb-run` for final runs. Verify extensions block requests (> 0) under your chosen mode.
5. **Blind spots.** Web/Service Workers aren't instrumented. Scripts can detect the hooks (`toString` is spoofed; other checks aren't). Bundled or eval'd code is attributed to the bundle's host. CNAME-cloaked trackers look first-party.
6. **Heuristics.** Thresholds follow E&N/FP-Inspector, including the save/restore/animation exclusion (since 18 Nov 2026; pilot canvas counts predate it). Changing a threshold changes the paper's numbers: document any change, and measure precision with `make validation-sample`.
7. **API use is not tracking.** Prefer claims backed by `fp_exfil.csv` (value sent to a party) over API counts. Exfiltration matching is a lower bound.

## Ethics and data

Public pages only, no logins, no form submission beyond consent buttons, low rate (<=4 parallel browsers, one profile per site). `data/` contains cookie values and request bodies sent by page scripts (first 64 KB) and is gitignored; do not publish it. Local consent screenshots and labels are also kept in ignored `results/`.
