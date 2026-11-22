# Improvements and next steps

Updated: 18 November 2026. This is the handoff point for continuing the group project.

**Start here:** test consent on a small variety of websites, finalize the website list, and align the full-run commands before starting the complete experiment. Long crawls should be run manually, then their output reviewed.

## What is already implemented

- **Page-load checks:** HTTP errors, timeouts and incomplete visits are recorded as failures and excluded from comparisons. See [navigation.py](navigation.py), [crawler.py](crawler.py) and [analyze.py](analyze.py).
- **Consent verification:** the crawler tries supported consent-manager APIs, then visible buttons/settings. It reads TCF or Cookiebot state across the main page and embedded frames. A click alone is insufficient. Saved choices are checked after reload and on subpages; missing APIs get a short loading grace period. Conflicts, lost choices and unsupported cases remain failed or unknown. See [consent.py](consent.py).
- **Failure evidence:** optional screenshots and button labels help explain failed/unknown consent. The one-site check enables this and creates fresh data on every invocation. See [scripts/check_consent.py](scripts/check_consent.py).
- **Browser isolation:** Chromium's sandbox is explicitly enabled inside Docker, with a non-root crawler and a compatible seccomp profile. See [scripts/preflight.py](scripts/preflight.py) and [security/seccomp_profile.json](security/seccomp_profile.json).
- **Real blockers:** uBlock Origin Lite and Privacy Badger packages are pinned and checked before use. Startup checks confirm that their rules are ready. The recorded GPC signal reflects the browser's actual state, including Privacy Badger's own defaults. See [extensions.lock.json](extensions.lock.json) and [extension_state.py](extension_state.py).
- **VPN:** the crawler shares the VPN container's network. The private WireGuard file is mounted read-only into the VPN container, outside the repository. A preflight checks the exit country, connectivity loss when the VPN stops, and recovery. See [docker-compose.vpn.yml](docker-compose.vpn.yml) and [scripts/verify_vpn.py](scripts/verify_vpn.py).
- **Separate diagnostic runs:** the five-site pilot and individual consent checks do not overwrite the full-study database. See [scripts/run_pilot.py](scripts/run_pilot.py).

### Methodology additions (18 November 2026)

These change what the crawler records, so they had to land before the full experiment. Existing databases are migrated (new `post_data` column), but values are only captured by new crawls.

- **Exfiltration evidence (fingerprint actually leaves the browser).** `instrument.js` now ships the return values of `toDataURL` and of WebGL's unmasked vendor/renderer (first 10 per API per frame); the crawler keeps only match tokens (a 48-char slice of the canvas image, raw/base64 strings, MD5/SHA-1/SHA-256 hex), never the image itself, in a new `fp_values` table. Request bodies (POST/beacon, first 64 KB) are stored in `requests.post_data`; URLs are kept up to 8,000 chars (was 2,000). [exfil.py](exfil.py) searches later requests that were not blocked and writes `fp_exfil.csv`; `comparison.csv` gains `exfil_survival` over (receiver eTLD+1, value kind) pairs. This turns "a script called the API" into "the value was sent to domain X". It is a lower bound: values combined with others or hashed with custom functions (e.g. MurmurHash) are not recognised.
- **Identifier sharing.** `id_sharing.csv` lists identifier-like cookie values (>=10 chars, digit, varied, not a timestamp) found in requests to a registrable domain other than the cookie's owner. Consent-string cookies (`euconsent-v2`, OneTrust, Cookiebot, usprivacy, ...) are excluded because they are sent to vendors by design.
- **Behavioural signals.** Per script, registrations of `mousemove`/`pointer*`, `key*`, `touch*`, `wheel`/`scroll` and `devicemotion`/`deviceorientation` listeners (including `on*` properties), plus Generic Sensor API constructors. Written to `behavior.csv`; `per_site.csv` adds `behavior_tp_domains` and `behavior_tp_multi_family` (third parties collecting >=2 families). These are categorised as `behavior`, not fingerprinting calls. Registration is not proof of biometrics; combine with exfiltration evidence before claiming tracking.
- **Full Englehardt–Narayanan canvas rule.** `Canvas2D.save`/`restore` and canvas event listeners are now hooked, and a script that uses them is no longer counted as canvas fingerprinting (E&N's animation exclusion). **This changes canvas counts relative to earlier pilot output.**
- **Pay-or-OK walls.** When a rejection fails, visible controls are classified: an accept control plus a paid option ("subscribe", "abo", "€/month", ...) and no free reject control is recorded as `pay_or_ok` (outcome stays `failed`, reason starts with `Pay-or-OK`). `per_site.csv` has `consent_pay_or_ok`; `consent_coverage.csv` has a `pay_or_ok` count. This is a candidate label: confirm each case with the saved screenshot (a newsletter "Subscribe" link next to a broken banner could be misread).
- **Separating the opt-out signals.** New Make targets `rejectonly` (CMP reject, no GPC) and `gpconly` (GPC, no banner action), so the effect of `optout` (both) can be attributed. `baseline3` adds a third identical baseline to estimate the noise floor's own variability.
- **Statistics and validation.** [scripts/stats.py](scripts/stats.py) (`make stats`) pairs each condition's per-site survival with the same site's `baseline2` survival: median difference, bootstrap 95% CI, exact sign test, for both fingerprint and exfiltration survival. [scripts/validation.py](scripts/validation.py) (`make validation-sample`) draws a stratified sample of detections to label by hand and scores precision per technique with Wilson intervals.
- **Container user matches the host user.** `make` builds the image with `PW_UID/PW_GID = id -u/-g`. Before this, on a host whose uid was not 1001, the crawler could not write the bind-mounted `data/` or `results/` (observed on a uid-1000 Linux host: consent diagnostics silently missing, and full crawls would have failed to write the database). Plain `docker compose build` keeps the previous uid 1001.

Considered and deliberately **not** done now: a cache/ETag-only respawn mode (the `--clear all` fingerprint verdict is already free of cache effects), Web/Service Worker instrumentation (Playwright cannot inject into workers before their code runs; stays a documented blind spot), and a per-site HTML transparency report (analysis-only, can be built after the crawl from the CSVs).

## What has actually been verified

| Check | Result and limit |
| --- | --- |
| Docker sandbox, full smoke test and controlled real-blocker test | Re-run on 18 November 2026 after the consent changes and the methodology additions, on a uid-1000 Linux host: `make preflight`, `make smoke` (76 unit tests plus the browser smoke test, including exfiltration, ID sharing, behavioural listeners and pay-or-OK assertions), `make verify-consent` (all nine cases) and `make verify-blockers` all passed. |
| VPN | Netherlands exit verified; HTTPS/direct IPv4 connections stopped when the VPN stopped, and the same exit returned. IPv6 tunnel operation was not verified. |
| Latest consent tests | User ran `make verify-consent` successfully: consent unit tests and all nine controlled browser cases passed. Cases include embedded frames, delayed APIs, conflicting reports and deliberately lost storage. |
| Cookiebot website | User ran a real rejection check: navigation completed, Cookiebot reported the optional categories rejected, and the saved choice remained after reload. This does not establish that all tracking stopped. |
| The Guardian | Navigation completed, but rejection did not. The saved banner offered “Accept all” or “Reject all and subscribe” (€5/month), with no free reject-all control shown. The crawler did not enter the subscription flow. |
| Earlier five-site pilot | The Guardian, Le Monde and Reddit completed browsing; IKEA and Slickdeals returned HTTP 403 errors. This pilot preceded the consent improvements and is diagnostic, not a representative effectiveness study. |

**The complete website-set experiment has not run.** Passing fixtures and one real Cookiebot check do not establish broad consent coverage.

## Remaining work, in order

### 1. Check broader consent coverage

- [ ] Test a small variety of sites, for example 5–10, covering different consent managers and languages. Include a real TCF-based site, not just Cookiebot.
- [ ] Check both rejection and acceptance where available. Inspect saved screenshots/labels when an action fails or remains unknown.
- [ ] Keep some sites outside the development sample and test them after changes, to check generalization.
- [x] Record paid subscription prompts separately from button-detection failures. Implemented as `pay_or_ok` (see Methodology additions); verified on a fixture only. Re-run `make consent-check SITE=theguardian.com ACTION=reject` to confirm it on the real banner, and check the screenshot for every `pay_or_ok` case.
- [ ] Choose part of the site list *for* consent coverage: include enough sites whose CMP exposes TCF or Cookiebot state, otherwise the verified opt-out comparison may end up with very few sites (in the pilot, no rejection was confirmed). Record this selection rule in the paper.
- [ ] Report how many actions are confirmed, failed or unknown. Unsupported sites can remain unknown; they must stay excluded from verified consent comparisons.

Commands, run from the repository folder:

```bash
make verify-consent
make consent-check SITE=cookiebot.com ACTION=reject
make consent-check SITE=cookiebot.com ACTION=accept
```

Replace `SITE` to test another website. Each check creates timestamped folders under `data/consent-check/` and `results/consent-check/`. A completed navigation and a confirmed consent action are separate results.

### 2. Finalize and freeze the website list

- [ ] Agree on the final sites/categories as a group and record the selection method.
- [ ] Exercise [scripts/check_tranco.py](scripts/check_tranco.py), which checks the seed sites against a popularity ranking. Preserve the exact ranking archive, date and matching list ID.
- [ ] Wire the chosen output into the actual crawl commands. **The current full-run Make targets still read `sites.csv`, not the generated ranked list.**
- [ ] Put generated input files in a mounted location such as `data/`. A new CSV written at the repository root after an image build is not automatically visible inside the crawler container.

### 3. Prepare consistent full-run commands

- [ ] Align the full-run settings with the tested setup. The current Make targets still use older defaults: headless browsing, four workers, and no consent-failure artifacts.
- [ ] Choose and record browser mode, worker count, dwell time, subpage count, timeouts, consent wait, language and timezone. Keep the same settings across conditions.
- [ ] Keep the same verified VPN exit across a batch; record/check it for full runs as the pilot helper does.
- [ ] Record software/extension versions and observed extension settings, and enable consent diagnostics.
- [ ] Use a fresh full-study database and preserve the old pilot. Reusing a completed run label can skip sites and leave old evidence in place.
- [ ] Reuse baseline subpage URLs across conditions and verify that comparable visits actually occurred.

### 4. Final checks, then the complete experiment

After the preceding changes, run these checks yourself:

```bash
make preflight
make preflight-vpn
make verify-consent
make verify-blockers
make smoke
```

Run `preflight-vpn` before crawling: it briefly stops the VPN to test connectivity loss.

- [ ] Resolve failures in these final checks before starting the full set.
- [ ] Run the conditions: baseline → repeated baseline (`baseline2`, `baseline3`) → uBOL → Privacy Badger → reject + GPC → reject only → GPC only → accept.
- [ ] Analyze results and inspect both navigation exclusions and `consent_coverage.csv`. Compare blocker/opt-out changes with normal variation between the two baselines: run `make stats` and report the paired differences and confidence intervals, not raw totals.
- [ ] Manual validation: `make validation-sample`, then two people label each sampled script (`fp`/`not_fp`/`unsure`) independently, resolve disagreements, and run `python scripts/validation.py score results/validation_sample.csv`. Report precision per technique in the paper.
- [ ] Spot-check `fp_exfil.csv` and `id_sharing.csv` (a sample of example URLs) before reporting them.
- [ ] Afterwards, exercise [scripts/pick_respawn_sites.py](scripts/pick_respawn_sites.py), select cookie-respawn candidates and run the separate respawn experiment. Ensure its generated CSV is visible inside Docker too.

## Continuing on another teammate's machine

Use Docker, Python 3 and Make. Build through `make` (not plain `docker compose build`) so the container user matches your uid. On a new checkout, copy `.env.example` to `.env`, set `WIREGUARD_CONFIG_PATH` to that teammate's own private configuration file, and set the expected exit country. Install the pinned blockers with `make extensions`, then follow the checks above. Keep all conditions of a measurement batch on the same exit; record separately collected batches separately.

Share code, tests, lock files, `.env.example` and documentation through Git. `.env`, private `.conf` files, `data/`, `results/` and downloaded extensions are ignored. Raw databases contain cookie values; local results/evidence are not included in a code push.

See [README.md](README.md) for the component explanations and methodology limits. In particular, detected browser API use is not automatically proof of tracking, and HTTP-200 bot/challenge pages are not yet detected automatically.
