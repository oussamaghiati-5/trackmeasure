# trackmeasure

An instrumented web crawler that measures **how prevalent advanced tracking is on popular sites, and how well blockers and consent opt-outs actually stop it.**

Course research project (web/device tracking, *Defensive: Detection/Transparency* track); the output is a short ACM AsiaCCS-format paper.

## Research questions

1. How common are fingerprinting (canvas, canvas-font, WebRTC, audio, WebGL, attribute enumeration), storage abuse and cookie respawning on ~150 popular news, e-commerce and social/affiliate sites?
2. How much of it survives a content blocker (uBlock Origin Lite, Privacy Badger)?
3. Does rejecting consent through the site's CMP (plus a Global Privacy Control signal) actually stop it? Accepting is measured as the upper bound.
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

### 2. Runs

| Run | Setup | Purpose |
|---|---|---|
| `baseline` | plain crawl | reference |
| `baseline2` | identical to baseline | noise floor (ad rotation etc.) |
| `ubol` | uBlock Origin Lite (MV3) | blocker effectiveness |
| `pbadger` | Privacy Badger | blocker effectiveness |
| `optout` | CMP reject + `Sec-GPC: 1` | does opt-out stop tracking? |
| `accept` | CMP accept | upper bound |

### 3. Consent automation (`consent.py`)
Tries the CMP's JavaScript API (OneTrust, Cookiebot, Didomi, Usercentrics v2/v3), then visible controls if the API does not produce confirmation: a role-based button click with anchored multilingual regexes (EN/FR/DE/ES/IT/PT/NL), or a two-layer "manage, then reject/save" flow.

APIs are tried in the main page and child frames; missing APIs are retried within the consent wait because CMP code may load late. State is read concurrently across the current main/child frames after an action and on reload/subpages. This handles a cross-origin banner whose button updates the parent's API, even when the banner removes its own frame. Supported reports that disagree keep the result unknown; a convenient frame cannot override a contradictory supported report. Existing verification requirements remain unchanged, so unsupported CMPs and incomplete TCF events/maps still remain unknown. This change does not claim universal banner support or address every TCF timing/vendor-scope issue.

Consent outcomes are `confirmed`, `failed`, or `unknown`; baseline/blocker runs use `not_requested` when no choice was requested. Confirmation requires a completed action plus readable saved state from TCF or Cookiebot. A vanished banner, successful function call, default false flags, or `gdprApplies=false` alone cannot confirm rejection. TCF is read using `addEventListener` (the old `getTCData` command is deprecated); its completed-action state must match the request. Cookiebot must report a response and all three optional categories must match. Other CMPs may be actuated but remain unknown unless a supported state reader confirms them. USP sale-opt-out data is retained separately and is not treated as proof of an all-cookie rejection.

Confirmed choices are checked again on the homepage reload and each subpage. A missing/loading CMP is polled with a two-second retry budget (individual API reads also have timeouts); this accommodates APIs that initialize after page load. Rechecks never repeat consent actions. A readable opposite choice or conflicting supported reports downgrade confirmation immediately; persistently missing state becomes unknown. The action, attempts, evidence, subsequent checks (including polling count/time), outcome/reason, and GPC flag are stored in the existing consent JSON. Confirmation describes the observed CMP state, not a guarantee about tracking behavior or legal compliance. Failed/unknown consent does not make a successfully loaded website a navigation failure; raw observations stay available, but these site/runs are excluded from verified consent comparisons. Old requested-consent recordings without verification are unknown; the crawler's normal resume behavior still skips completed crawls, so use a new run label or database to collect fresh consent evidence.

Verification references: [TCF CMP API](https://github.com/InteractiveAdvertisingBureau/GDPR-Transparency-and-Consent-Framework/blob/master/TCFv2/IAB%20Tech%20Lab%20-%20CMP%20API%20v2.md), [Cookiebot browser API](https://www.cookiebot.com/en/developer/).

To test the frame handling, run `make verify-consent`. It rebuilds the image, runs the consent unit tests, and tests nine local browser cases with external networking disabled: parent TCF with a cross-origin accept/reject button that removes its frame, a settings step, a child Cookiebot API, deliberate loss of its saved choice, delayed API loading, unchanged defaults, conflicting reports and a no-op API/button. Positive cases must retain confirmation after reload; clearing stored consent must downgrade confirmation to unknown. Fixture pages use intercepted HTTPS URLs and the child uses a `SameSite=None; Secure; Partitioned` cookie, following [Chrome's iframe cookie guidance](https://developer.chrome.com/docs/modern-web-guidance/guides/security/security). Browser cookie protections are left enabled. These are synthetic checks, not evidence of real-site coverage.

Then run `make consent-check SITE=theguardian.com ACTION=reject` (or `ACTION=accept`). Each invocation uses a new timestamped database under `data/consent-check/`, leaving the original pilot unchanged. Failed/unknown actions save a screenshot and per-frame control labels under `results/consent-check/`; diagnostics cannot change eligibility. The command reports the consent outcome, records settings/source hash/VPN exit, and checks the exit stays fixed. Use the same command on sites outside the development sample before reporting coverage. Generic crawler runs can opt into these artifacts with `--consent-artifacts results/<folder>`.

### 4. Respawning (`respawn.py`)
Per site, three browsers on the same IP:

- **A1**: profile A, fingerprint F1. Snapshot cookies, localStorage, IndexedDB.
- **A2**: revisit with F1 after `--clear cookies` (same profile: *stateful* respawning) or `--clear all` (brand-new profile: fresh-profile candidate check).
- **B**: control with a different fingerprint F2 (viewport, DPR, locale, timezone, hardwareConcurrency, deviceMemory, canvas noise).

A token is a *respawn candidate* if an ID-like value in an A1 cookie reappears in an A2 cookie and appears nowhere in B. A single fresh-profile match does not prove fingerprint derivation; server-side behavior, token parsing and visit variability need further checks.

### 5. Analysis (`analyze.py`)
Detects fingerprinting per (script, frame, visit) using heuristics after Englehardt & Narayanan (CCS'16) as adapted by FP-Inspector:

| Technique | Heuristic |
|---|---|
| `canvas` | >=10 distinct chars drawn and a non-lossy readback >=16x16 |
| `canvas_font` | >=50 distinct fonts in `measureText` |
| `webrtc` | `createDataChannel` + `createOffer` |
| `audio` | oscillator + compressor + readback |
| `webgl` | `getParameter(37445/37446)` (extra, weaker) |
| `attr_enum` | >=12 distinct navigator/screen attributes read (extra, weaker) |

Headline metric: **survival** of (script eTLD+1, technique) pairs vs the baseline in the post-consent phases. Report blocker and opt-out survival relative to the `baseline2` noise floor, not to 1.0.

Outputs in `results/`: `per_site.csv` (requests, third-party domains, blocked requests, FP calls, storage ops, cookies, per-technique counts, pre-consent FP scripts, consent action/method/outcome/reason, GPC, TCF purposes, and comparison eligibility), `fp_scripts.csv` (every detected script, including diagnostic observations), `comparison.csv` (eligible paired survival vs baseline), and `consent_coverage.csv` (counts of all site/runs by consent action and outcome, including exclusions). Coverage counts eligible site/runs, not the number of matched baseline pairs. GPC is recorded as enabled/disabled/unknown; the `optout` treatment remains rejection plus GPC, so do not attribute its effect to rejection alone.

GPC records the observed `navigator.globalPrivacyControl` value on the final page, not just the CLI flag. The consent JSON also retains `gpc_requested`, the observation source, and the actual initialized extension metadata/settings. Privacy Badger enables GPC itself even when the crawler was not passed `--gpc`; treat its run as the extension's full default behavior, not blocking alone.

## Repository layout

```
crawler.py  instrument.js  consent.py  respawn.py  analyze.py  common.py
sites.csv                 150-site seed list (news / e-commerce / social+affiliate)
scripts/
  get_extensions.sh/.py   install the hash-pinned Chromium MV3 packages into ext/
extensions.lock.json      official package URLs, versions and SHA-256 checksums
  check_tranco.py         cross-check sites.csv against a dated Tranco list
  pick_respawn_sites.py   choose respawn candidates from the baseline cookies table
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
python scripts/check_tranco.py --out data/sites_ranked.csv --save-zip data/tranco_latest.zip

make smoke                      # local fixture, no internet, no VPN
make baseline baseline2 blockers optout accept STUDY_SITES=data/sites_ranked.csv
make respawn
make analyze
```

For the full study, first finalize the site list and save it under `data/` (for
example, `python scripts/check_tranco.py --out data/sites_ranked.csv
--save-zip data/tranco_latest.zip`). Record the exact Tranco list ID and list
selection rules in the paper. The Make targets now use `scripts/run_study.py`:
they copy and hash the site list, record fixed browser settings and the VPN
exit in `results/study/manifest.json`, check the exit before and after each
condition, run headfully with one worker, and save failed-consent diagnostics.
Use `STUDY_BATCH=<new-name>` for a changed input or experimental setup.
Outputs are in `data/<batch>/` and `results/<batch>/`. A completed command can
still contain failed sites; inspect `per_site.csv` and `consent_coverage.csv`
before reporting any result. `make respawn` selects candidates from that
batch's baseline and writes its input where the container can read it.
For a diagnostic run without a VPN, `python scripts/run_study.py --sites
data/exploratory_input.csv --batch exploratory_name --network direct --modes
baseline baseline2 ubol pbadger optout accept` records the direct exit and keeps
its results separate. Treat consent actions without confirmed saved state as
diagnostic observations. This direct mode does not replace the EU VPN study.

### Real blocker setup

`extensions.lock.json` pins uBlock Origin Lite **2026.1110.1816** from its official Chromium release ZIP and Privacy Badger **2026.9.15.2** from EFF's official Chromium CRX3 download. The default Privacy Badger GitHub release source is MV2 and is not the Chromium build. The installer verifies the archive hash, manifest version/version number and worker before replacing installed directories; package code is not patched. Downloads are cached under gitignored `ext/.downloads/`. Keep this cache with the experiment: EFF's CRX URL is mutable, so a future changed download intentionally fails the pinned hash check rather than silently changing the experiment. To update versions, explicitly review/update the lock and rerun installation/verification.

The extension inputs stay mounted read-only. Each browser gets a private writable copy so Chromium can compile DNR filter metadata; copies are removed when the browser closes or launch fails. Readiness checks wait for enabled default rules and, for Privacy Badger, completed initialization and seed loading. Missing/incompatible extensions or incomplete initialization fail the site/run rather than proceeding without protection. Runtime IDs may differ between unpacked private copies; they are recorded with the settings.

`make verify-blockers` uses `docker-compose.test.yml` with `network_mode: none`. A local server impersonates the known tracker domain `adsrvr.org` through Chromium's resolver rules; no requests are sent to the real tracker. The baseline must execute both that script and a harmless first-party script. Each real blocker must prevent the tracker from reaching the server, produce `ERR_BLOCKED_BY_CLIENT`, and leave the harmless script working. The check also runs the actual crawler with Privacy Badger and verifies saved extension metadata and extension-enabled GPC. A report is written to `results/blocker_verification.json`; it is an installation/control check, not a measurement of real-site effectiveness. The regular smoke test still uses its separate artificial blocker.

In the pinned packages, observed uBOL defaults are **optimal** for all URLs with six enabled default static rulesets. Privacy Badger uses its bundled seed data with `learnLocally=false`, and sends its default privacy signals. The verifier records observed settings/rule counts rather than assuming them; rerun it after any package or browser change. Package sources: [uBOL release](https://github.com/uBlockOrigin/uBOL-home/releases/tag/2026.1110.1816), [EFF Chromium download](https://privacybadger.org/#direct-download).

### VPN setup (each teammate)

Generate your own WireGuard configuration for a fixed EU server (for example, a Proton VPN Free Netherlands server). Select Linux, leave port forwarding and VPN ad/tracker blocking off, and download the `.conf` outside this repository. Set `WIREGUARD_CONFIG_PATH` in your local `.env` to its absolute path, and `VPN_EXPECTED_COUNTRY=NL` for the Netherlands. Share code and results, never private keys or configuration files. Keep all experimental conditions on the same exit; separate teammates' exits should be recorded as separate batches.

An accountless OpenVPN alternative is supported by `docker-compose.vpn.openvpn.yml`. Download a fixed EU `.ovpn` profile from [VPNBook](https://www.vpnbook.com/freevpn/openvpn), replace the `remote` hostname with its resolved IPv4 address (required by Gluetun's custom-provider parser), and put its absolute path in `OPENVPN_CONFIG_PATH` in `.env`. Set `VPN_COMPOSE_FILE=docker-compose.vpn.openvpn.yml`, `OPENVPN_USER`, `OPENVPN_PASSWORD`, and `VPN_EXPECTED_COUNTRY=DE` (or the selected EU country). Obtain the current public credentials from VPNBook's page; they change periodically. The profile and `.env` stay out of Git. The free server may be slow or unavailable, so verify the exit and run all comparison conditions on the same endpoint. This OpenVPN path has the same Gluetun network isolation as the WireGuard overlay and uses DNS over HTTPS to accommodate providers that block DNS over TLS.

The VPN overlay uses Gluetun's [custom WireGuard configuration mount](https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/custom.md). Only Gluetun receives the file, read-only; the crawler shares its network but receives no VPN secrets. A missing path fails instead of creating an empty directory. The image is pinned by digest, VPN DNS blocklists are disabled to avoid adding an extra blocker to every condition, and `.conf` files are excluded from both Git and the crawler build context.

Run `make build` if the crawler image is missing, then `make preflight-vpn` **before starting a crawl**. This host-side script starts Gluetun, checks the exit country and that it differs from direct Docker egress, and keeps a crawler container running while stopping Gluetun. Both HTTPS and direct-IP probes must fail with the VPN stopped. It restores Gluetun even after a failed probe, verifies the original exit returns, and writes `results/vpn_verification.json`. A successful IPv6 connection before stopping is recorded when available; a failed IPv6 connection alone does not demonstrate working IPv6 through the tunnel. This test briefly interrupts the VPN, so never run it alongside an experiment. It tests egress loss, not every possible LAN or DNS-leak scenario.

### Five-site diagnostic pilot

`pilot_sites.csv` is a convenience sample from the seed list, covering news, shopping, social and affiliate sites; it is not a representative study sample. Run `make pilot-baseline`, inspect the failures, then `make pilot-conditions`. The latter runs baseline2, reject+GPC, accept, uBOL and Privacy Badger only on successful baseline sites. Each browser runs headfully under Xvfb, with one worker, five-second dwell, one subpage at most, a 45-second navigation timeout and a 180-second site timeout. Subpage URLs are reused from baseline where available. The full study's default commands and database stay separate.

The pilot writes SQLite and input copies under `data/pilot/`, and logs, CSVs and settings/exit metadata under `results/pilot/`. It requires a Netherlands exit, checks that the exit stays fixed before/after each condition, and refuses to mix changed settings or exits into the same batch. It does not run the disruptive VPN-loss test during crawling. Baseline failures and unconfirmed consent remain diagnostic exclusions, not successful protection. Review the raw visit URLs for unexpected redirects or HTTP-200 challenge pages before interpreting any tracking counts.

Crawls resume: completed sites are skipped, failed ones are retried from scratch. The base image tag must match the pinned `playwright` version in `requirements.txt`.

A completed navigation requires a successful HTTP document response and the DOM-content-loaded event; a timeout, missing response, HTTP error, or empty 204/205 response makes that site/run unsuccessful. Failed visits retain their status and error for diagnosis. Any attempted subpage failure invalidates the site/run, so an incomplete crawl cannot look like successful blocking. `per_site.csv` includes `measurement_ok` and `measurement_error`; comparisons and headline summaries require successful execution, valid visits, and either confirmed or unrequested consent. This also applies to older databases. Challenge pages served with HTTP 200 are not yet detected.

### Isolation checklist (before any real crawl)

The shared browser launcher explicitly sets `chromium_sandbox=True` for the crawler and respawn experiments, including extension runs, and rejects sandbox-disabling custom flags. The container runs as non-root `pwuser` with `init`, `shm_size: 2gb` (not `ipc: host`) and `cap_drop: [ALL]`. (`no-new-privileges` is not set: it broke every `exec` on the dev host.) Only `./data` (rw), `./ext` (ro) and `./results` (rw) are mounted; there is no Docker socket and no host network. The VPN overlay deliberately sets no `FIREWALL_OUTBOUND_SUBNETS`, so crawled pages can't reach your LAN.

Compose uses `security/seccomp_profile.json`, based on [Playwright v1.63.0's profile](https://github.com/microsoft/playwright/blob/v1.63.0/utils/docker/seccomp_profile.json) (upstream SHA-256 `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`). It allows namespace creation as described in the [Playwright Docker guide](https://playwright.dev/python/docs/docker#crawling-and-scraping). Our sole addition permits the `chroot` syscall: with all container capabilities dropped, the upstream capability-gated rule is omitted, even though Chromium needs chroot after creating its own user namespace. Kernel permission checks still apply; the crawler receives no additional container capabilities, and the profile retains its default-deny policy.

1. **Snapshot the VM** (`VBoxManage snapshot "<vm>" take pre-crawl` or `virsh snapshot-create-as <vm> pre-crawl`). Disable writable shared folders, clipboard sharing and drag-and-drop, keeping only the folder used to export `data/`.
2. **`make preflight`** checks non-root uid, no Docker socket, no `.env` in the image, and zero effective container capabilities. It uses the actual shared launcher, checks Chromium's effective command line, and requires `chrome://sandbox` to report layer-1 isolation, PID/network namespaces, and Seccomp-BPF enabled. Missing/disabled status fails the check. It also prints the configured mounts for inspection. If preflight fails, fix the runtime's namespace/seccomp support; do not fall back to `--no-sandbox`, privileged mode, or an unconfined seccomp profile.
3. **`make preflight-vpn`** checks the exit country/IP, loss of crawler connectivity when Gluetun stops, and recovery with the same exit. It briefly interrupts the VPN: run only before crawling. See the VPN setup section and `results/vpn_verification.json`.
4. Use the same exit for every run of one experiment, and record it.

Without Docker: `pip install -r requirements.txt && python -m playwright install --with-deps chromium`.

**Timing.** The direct crawler defaults are 10 s dwell, 3 subpages and up to 10 s consent wait. The full-study runner uses headful browsers with configurable `--workers` (1–4), 5 s dwell, one subpage and up to 20 s consent wait. Keep the worker count fixed within a batch. Budget time from a pilot on the final setup; the six conditions and retries can take hours.

After a condition finishes, run `python scripts/paired_uncertainty.py results/BATCH/comparison.csv` to write `paired_uncertainty.json`. It compares each condition with the repeat baseline on common sites with detected baseline pairs and gives a 95% site-bootstrap interval for the difference in pair survival. This quantifies variation across measured sites; it does not resolve sampling or fingerprint-heuristic errors.

On this Windows/Docker setup, the live SQLite database became corrupt on both OneDrive and local Windows bind mounts. Use `docker-compose.volume.yml` to keep the live batch database on Docker's Linux filesystem, and export a copy to the workspace only after the crawler exits. Inspect the run's `.log` file while it writes; open the exported SQLite file after completion. `scripts/resume_local_batch.py` resumes a frozen batch with the volume overlay and runs the offline analysis on that same volume.

## Smoke test

`make smoke` (or `python test/smoke.py`) starts the fixture server, runs every crawl mode and both respawn modes, and asserts: all 6 techniques detected on `tracker.test`; blocker survival 0.0 with blocked requests > 0; opt-out survival 1.0 (the fixture tracker ignores consent); respawn finds `_trk` (localStorage-backed) and, with `--clear all`, the fixture's known fingerprint-derived `_fpid` token.

`make smoke` first runs the regression tests (`python -m unittest discover -s test -p 'test_*.py'`). The browser smoke test also visits deliberate HTTP 404/503 pages and checks they are recorded as failures and excluded from comparisons. Consent fixtures cover a confirmed decision, a button that leaves consent enabled, a click with no readable state, and an incomplete settings flow. Their raw observations are retained while unverified consent comparisons are excluded. Regression tests cover legacy records, default versus saved decisions, API-to-button fallback, conflicting evidence, and state loss after navigation.

## Status

The six-condition EU study batch `full_eu_20261122` completed on 2026-11-22. Its shareable, privacy-reduced outputs are in [`public_results/full_eu_20261122/`](public_results/full_eu_20261122/). The full local batch remains under ignored `results/` and its SQLite database under ignored `data/`; neither should be committed as-is.

The baseline yielded 93 usable visits out of 149 attempts. In paired comparisons with repeat-baseline variation, observed domain–technique pair survival decreased by 27.7 percentage points with uBlock Origin Lite (56 sites) and 19.8 points with Privacy Badger (57 sites). Consent choices were confirmed for only two rejection attempts and one acceptance attempt, so the batch does not support a general consent-effect estimate. See [`PAPER_DRAFT.md`](PAPER_DRAFT.md) for the research-paper draft and [`ETUDE_RAPPORT.md`](ETUDE_RAPPORT.md) for the detailed French report.

To recreate the shareable bundle from the private batch, run:

```sh
python scripts/export_public_results.py results/full_eu_20261122 --sites data/full_eu_20261122/sites.csv --overwrite
```

The exporter removes script URLs that may contain session tokens, the exact VPN exit IP, and credential-bearing VPN profile hashes. It omits raw crawl logs and databases.

## Pitfalls (read before the real crawl)

1. **Geolocation.** From a Moroccan IP most CMPs show no GDPR banner and `gdprApplies` is false. Run *all* runs through the same EU exit (gluetun sidecar), plus a separate US exit for CCPA "do not sell". Only confirmed choices support verified opt-out claims; inspect `consent_coverage.csv` for failures and unknown cases. A non-empty `consent_method` alone is insufficient.
2. **MV2 extensions won't load** in current Chromium. Use the pinned MV3 Chromium packages for uBlock Origin Lite and Privacy Badger, not Privacy Badger's default MV2 source branch. Filtering modes change across releases: this pinned uBOL uses optimal mode. Inspect `blocker_verification.json` and saved run metadata rather than assuming an older basic-mode default. MV3 blockers can't do everything MV2 uBO could.
3. **Noise floor.** Always run `baseline2`.
4. **Bot detection.** Headless runs can change tracker behaviour; use `--headful` under `xvfb-run` for final runs. Verify extensions block requests (> 0) under your chosen mode.
5. **Blind spots.** Web/Service Workers aren't instrumented. Scripts can detect the hooks (`toString` is spoofed; other checks aren't). Bundled or eval'd code is attributed to the bundle's host. CNAME-cloaked trackers look first-party.
6. **Heuristics.** Thresholds follow E&N/FP-Inspector, minus the save/restore/animation exclusion. Changing a threshold changes the paper's numbers: document any change and spot-check detections by hand.

## Ethics and data

Public pages only, no logins, no form submission beyond consent buttons, low rate (<=4 parallel browsers, one profile per site). `data/` contains cookie values and is gitignored; do not publish it. Local consent screenshots, labels, crawl logs, and the raw `results/` tree are also ignored. Publish only the privacy-reduced bundle generated by `scripts/export_public_results.py`.
