# Improvements and next steps

Updated: 16 November 2026. This is the handoff point for continuing the group project.

**Start here:** test consent on a small variety of websites, confirm the ranked website list and run the container checks before starting the complete experiment. Long crawls should be run manually, then their output reviewed.

## What is already implemented

- **Page-load checks:** HTTP errors, timeouts and incomplete visits are recorded as failures and excluded from comparisons. See [navigation.py](navigation.py), [crawler.py](crawler.py) and [analyze.py](analyze.py).
- **Consent verification:** the crawler tries supported consent-manager APIs, then visible buttons/settings. It reads TCF or Cookiebot state across the main page and embedded frames. A click alone is insufficient. Saved choices are checked after reload and on subpages; missing APIs get a short loading grace period. Conflicts, lost choices and unsupported cases remain failed or unknown. See [consent.py](consent.py).
- **Failure evidence:** optional screenshots and button labels help explain failed/unknown consent. The one-site check enables this and creates fresh data on every invocation. See [scripts/check_consent.py](scripts/check_consent.py).
- **Browser isolation:** Chromium's sandbox is explicitly enabled inside Docker, with a non-root crawler and a compatible seccomp profile. See [scripts/preflight.py](scripts/preflight.py) and [security/seccomp_profile.json](security/seccomp_profile.json).
- **Real blockers:** uBlock Origin Lite and Privacy Badger packages are pinned and checked before use. Startup checks confirm that their rules are ready. The recorded GPC signal reflects the browser's actual state, including Privacy Badger's own defaults. See [extensions.lock.json](extensions.lock.json) and [extension_state.py](extension_state.py).
- **VPN:** the crawler shares the VPN container's network. The private WireGuard file is mounted read-only into the VPN container, outside the repository. A preflight checks the exit country, connectivity loss when the VPN stops, and recovery. See [docker-compose.vpn.yml](docker-compose.vpn.yml) and [scripts/verify_vpn.py](scripts/verify_vpn.py).
- **Separate diagnostic runs:** the five-site pilot and individual consent checks do not overwrite the full-study database. See [scripts/run_pilot.py](scripts/run_pilot.py).

## What has actually been verified

| Check | Result and limit |
| --- | --- |
| Docker sandbox, earlier full smoke test and controlled real-blocker test | Passed earlier. The full smoke test must be repeated after the latest consent changes. |
| VPN | Netherlands exit verified; HTTPS/direct IPv4 connections stopped when the VPN stopped, and the same exit returned. IPv6 tunnel operation was not verified. |
| Latest consent tests | User ran `make verify-consent` successfully: consent unit tests and all nine controlled browser cases passed. Cases include embedded frames, delayed APIs, conflicting reports and deliberately lost storage. |
| Cookiebot website | User ran a real rejection check: navigation completed, Cookiebot reported the optional categories rejected, and the saved choice remained after reload. This does not establish that all tracking stopped. |
| The Guardian | Navigation completed, but rejection did not. The saved banner offered “Accept all” or “Reject all and subscribe” (€5/month), with no free reject-all control shown. The crawler did not enter the subscription flow. |
| Earlier five-site pilot | The Guardian, Le Monde and Reddit completed browsing; IKEA and Slickdeals returned HTTP 403 errors. This pilot preceded the consent improvements and is diagnostic, not a representative effectiveness study. |

**The complete website-set experiment has not run.** Passing fixtures and one real Cookiebot check do not establish broad consent coverage. On 22 November, the host unit suite and full local smoke test passed. A dated Tranco list (ID `46YLX`) retained 149 of 150 seed sites; `wirecutter.com` was absent. The downloaded list and ranked CSV are under ignored `data/` on the machine where this check ran.

A separate five-site direct-Morocco exploratory batch completed all six conditions on 22 November. Its blocker and noise-floor results are in [NASROU_REPORT.md](NASROU_REPORT.md). Neither consent action was confirmed on any of its five sites, so it does not answer the opt-out question. The full EU VPN study still needs a local WireGuard configuration.

## Remaining work, in order

### 1. Check broader consent coverage

- [ ] Test a small variety of sites, for example 5–10, covering different consent managers and languages. Include a real TCF-based site, not just Cookiebot.
- [ ] Check both rejection and acceptance where available. Inspect saved screenshots/labels when an action fails or remains unknown.
- [ ] Keep some sites outside the development sample and test them after changes, to check generalization.
- [ ] Record paid subscription prompts separately from button-detection failures. **This separate classification is not implemented yet; The Guardian currently retains the generic failed-action outcome.**
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
- [x] Exercise [scripts/check_tranco.py](scripts/check_tranco.py) against the dated 22 November list (ID `46YLX`); preserve its archive and ranked CSV under `data/`.
- [x] Allow the ranked CSV in the full-run commands with `STUDY_SITES=data/sites_ranked.csv`. The study runner copies and hashes it in the mounted batch directory.
- [ ] Agree as a group that the 149-site ranked output is the final study list and document this selection in the paper.

### 3. Prepare consistent full-run commands

- [x] Align the full-run Make targets with the tested headful, one-worker setup and consent-failure artifacts using `scripts/run_study.py`.
- [ ] Choose and record browser mode, worker count, dwell time, subpage count, timeouts, consent wait, language and timezone. Keep the same settings across conditions.
- [x] The study runner records/checks the same VPN exit before and after every condition.
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
- [ ] Run the conditions: baseline → repeated baseline → uBOL → Privacy Badger → reject + GPC → accept.
- [ ] Analyze results and inspect both navigation exclusions and `consent_coverage.csv`. Compare blocker/opt-out changes with normal variation between the two baselines.
- [ ] Afterwards, exercise [scripts/pick_respawn_sites.py](scripts/pick_respawn_sites.py), select cookie-respawn candidates and run the separate respawn experiment. Ensure its generated CSV is visible inside Docker too.

## Continuing on another teammate's machine

Use Docker, Python 3 and Make. On a new checkout, copy `.env.example` to `.env`, set `WIREGUARD_CONFIG_PATH` to that teammate's own private configuration file, and set the expected exit country. Install the pinned blockers with `make extensions`, then follow the checks above. Keep all conditions of a measurement batch on the same exit; record separately collected batches separately.

Share code, tests, lock files, `.env.example` and documentation through Git. `.env`, private `.conf` files, `data/`, `results/` and downloaded extensions are ignored. Raw databases contain cookie values; local results/evidence are not included in a code push.

See [README.md](README.md) for the component explanations and methodology limits. In particular, detected browser API use is not automatically proof of tracking, and HTTP-200 bot/challenge pages are not yet detected automatically.
