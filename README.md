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
Tries, in order: the CMP's JavaScript API (OneTrust, Cookiebot, Didomi, Usercentrics v2/v3); a role-based button click with anchored multilingual regexes (EN/FR/DE/ES/IT/PT/NL); a two-layer "manage, then reject/save" flow. The resulting TCF v2 / USP state is read back so you can verify the opt-out registered.

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
| `canvas` | >=10 distinct chars drawn and a non-lossy readback >=16x16 |
| `canvas_font` | >=50 distinct fonts in `measureText` |
| `webrtc` | `createDataChannel` + `createOffer` |
| `audio` | oscillator + compressor + readback |
| `webgl` | `getParameter(37445/37446)` (extra, weaker) |
| `attr_enum` | >=12 distinct navigator/screen attributes read (extra, weaker) |

Headline metric: **survival** of (script eTLD+1, technique) pairs vs the baseline in the post-consent phases. Report blocker and opt-out survival relative to the `baseline2` noise floor, not to 1.0.

Outputs in `results/`: `per_site.csv` (requests, third-party domains, blocked requests, FP calls, storage ops, cookies, per-technique counts, pre-consent FP scripts, consent method, TCF purposes), `fp_scripts.csv` (every detected script), `comparison.csv` (survival vs baseline).

## Repository layout

```
crawler.py  instrument.js  consent.py  respawn.py  analyze.py  common.py
sites.csv                 150-site seed list (news / e-commerce / social+affiliate)
scripts/
  get_extensions.sh       fetch uBOL (MV3) and Privacy Badger into ext/
  check_tranco.py         cross-check sites.csv against a dated Tranco list
  pick_respawn_sites.py   choose respawn candidates from the baseline cookies table
test/                     local fixture: publisher, aggressive tracker, consent-respecting
                          tracker, MV3 test blocker, and smoke.py (end-to-end assertions)
Dockerfile  docker-compose.yml  docker-compose.vpn.yml  Makefile  requirements.txt
```

## Running it

The crawl executes untrusted third-party JavaScript from ad-heavy sites, so run it in the container (ideally inside a VM), never directly on your host.

```bash
cp .env.example .env            # VPN credentials (only for real crawls)
scripts/get_extensions.sh       # populates ext/
python scripts/check_tranco.py  # record the printed Tranco list ID in the paper

make smoke                      # local fixture, no internet, no VPN
make baseline baseline2 blockers optout accept
python scripts/pick_respawn_sites.py data/crawl.sqlite --run baseline   # writes respawn_sites.csv
make respawn
make analyze
```

Crawls resume: completed sites are skipped, failed ones are retried from scratch. The container runs as the non-root `pwuser` (no `--no-sandbox`) with `--init --ipc=host`. Only `./data` (rw), `./ext` (ro) and `./results` are mounted. The base image tag must match the pinned `playwright` version in `requirements.txt`.

Without Docker: `pip install -r requirements.txt && python -m playwright install --with-deps chromium`.

**Timing.** Defaults are 10 s dwell, 3 subpages and up to 10 s consent wait, about 1.5-2.5 min per site. With `--workers 4`, 150 sites take roughly 1-1.5 h per run.

## Smoke test

`make smoke` (or `python test/smoke.py`) starts the fixture server, runs every crawl mode and both respawn modes, and asserts: all 6 techniques detected on `tracker.test`; blocker survival 0.0 with blocked requests > 0; opt-out survival 1.0 (the fixture tracker ignores consent); respawn finds `_trk` (localStorage-backed) and, with `--clear all`, `_fpid` as fingerprint-derived.

## Status

Built and passing a local end-to-end smoke test with the original (non-container) setup. The container setup, extension fetch, Tranco and respawn-candidate scripts are written but not yet exercised. **Nothing has been run against real websites yet**; next is a 5-site pilot through an EU exit to tune consent regexes, timeouts and attribution.

## Pitfalls (read before the real crawl)

1. **Geolocation.** From a Moroccan IP most CMPs show no GDPR banner and `gdprApplies` is false. Run *all* runs through the same EU exit (gluetun sidecar), plus a separate US exit for CCPA "do not sell". Sites with an empty `consent_method` in the opt-out run never got an opt-out; exclude them from opt-out claims.
2. **uBlock Origin (MV2) won't load** in current Chromium. Use uBlock Origin Lite (MV3) and/or Privacy Badger. uBOL defaults to "basic" filtering; document the mode you use. MV3 blockers can't do everything MV2 uBO could.
3. **Noise floor.** Always run `baseline2`.
4. **Bot detection.** Headless runs can change tracker behaviour; use `--headful` under `xvfb-run` for final runs. Verify extensions block requests (> 0) under your chosen mode.
5. **Blind spots.** Web/Service Workers aren't instrumented. Scripts can detect the hooks (`toString` is spoofed; other checks aren't). Bundled or eval'd code is attributed to the bundle's host. CNAME-cloaked trackers look first-party.
6. **Heuristics.** Thresholds follow E&N/FP-Inspector, minus the save/restore/animation exclusion. Changing a threshold changes the paper's numbers: document any change and spot-check detections by hand.

## Ethics and data

Public pages only, no logins, no form submission beyond consent buttons, low rate (<=4 parallel browsers, one profile per site). `data/*.sqlite` contains cookie values and is gitignored; do not publish it.
