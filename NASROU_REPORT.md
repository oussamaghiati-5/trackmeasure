# Nasrou branch: improvement report

> **Archive — 22 November batch.** This change report is retained for provenance and is not the
> current result source. Use `paper/main.tex` and `public_results/full_eu_20261125/analyze.md`.

## Update of 24 November 2026: purpose labelling and consent fix

This update follows the review of the EU batch, which found that the detector said *that* scripts fingerprint but not *why*, and that the consent question had been dropped because of verifier bugs. The full review, with the final results and the status of each planned item, is in [`docs/review.html`](docs/review.html). This update implements its Tier 1 plan and needs no new crawl. Everything is recomputed from `data/full_eu_20261122/crawl.sqlite`.

**Changes**

- `consent.py` has two fixes and new offline checks:
  - **Fix 1.** An empty TCF purpose map after a user action is now a rejection, where it used to be "unknown".
  - **Fix 2.** An acceptance no longer requires every vendor on the Global Vendor List to be true. It needs at least 9 of purposes 1–10 and at least one vendor.
  - **New: `reassess`.** Re-checks stored CMP states with the current rules.
  - **New: `decode_tcf` and `cookie_verdict`.** Decode the stored TCF, OneTrust and Cookiebot consent cookies.
  - **New: `combine_outcome`.** Merges the live-API and cookie evidence.

  `analyze.py` uses all of these, and 8 regression tests were added.
- `purpose.py` and `scripts/label_purposes.py` label each fingerprinting script with a purpose, using vendor signatures, Tracker Radar categories, page context, hidden-vendor path clusters and vendor cookies. They output a primary and secondary purpose, party, confidence and evidence. 14 tests were added.
- `scripts/fetch_tracker_radar.py` fetches a pinned Tracker Radar subset, committed in `reference/`.
- `scripts/purpose_results.py` gives blocker and consent results per purpose with bootstrap intervals, runs validity checks, and writes the 80-script validation sheet. `scripts/purpose_agreement.py` and `docs/annotation_guide.md` cover the two-annotator κ and precision step.
- The public bundle is refreshed with the purpose tables and without URLs. `PAPER_DRAFT.md` is reframed around the three research questions: who fingerprints and why, does rejecting stop it, and what blockers stop. The README is updated.

**Harmful vs useful fingerprinting (added later on 24 November).** `purpose.harm_class` turns purpose into harm:

- **Tracking (harmful):** advertising or analytics purpose.
- **Service (useful):** security or functional purpose.
- **Dual-use:** ad verification and similar.
- **Undetermined:** split by tracking capability.

Capability counts identifying power (strong or ≥3 combined techniques), cross-site reach, attribute transmission and storage writes. Attribute transmission means the browser's own time zone or platform string seen in request URLs to the vendor, raw, URL-encoded or base64. 5 tests were added. The study now focuses on the harmful class:

- Harmful fingerprinting is 11% of baseline pairs on 17 sites, up to 19% on 23 sites under the upper bound. It is mostly attribute collection: 3 strong pairs.
- It runs before consent on all 17 sites.
- 15 of 16 harmful pairs survive a verified rejection (8 of 36 sites).
- uBlock removes 88 points of it. Privacy Badger removes 40.
- The X Ads pixel sent the browser's time zone and platform to X on 9 sites.

**Results (EU batch)**

| | Before | After |
|---|---|---|
| Verified rejections / acceptances | 2 / 1 | 36 / 44 (30 sites both ways) |
| Purpose of baseline fingerprinting pairs | not known | 63% security, 11% ads/analytics, 26% unknown/other |
| Strong-technique pairs from security vendors | not known | 84% |
| Pairs from hidden first-party vendors | counted as "first-party" | 27% (Akamai, Cloudflare, Kasada, DataDome, Fastly) |
| uBlock Origin Lite vs repeat baseline | −27.7 pp overall | ads/analytics −88.0 pp, security −19.8 pp |
| Verified reject vs repeat baseline | not estimable | +1.7 pp [−6.4, +11.9]: no reduction |
| Third-party domains after accept vs reject (30 sites) | not estimable | median 63 vs 22 |

**Validity:**

- Vendor signatures and Tracker Radar agree on 8 of 12 shared domains (κ = 0.49). The disagreements are secondary purposes.
- 93% of security scripts probe `navigator.webdriver`, against 68% of ad/analytics scripts (p < 10⁻⁵).

- The TCF decoder agrees with IAB's reference library on all 21 stored TC strings (`scripts/check_tcf_official.mjs`, 25 Nov). It also matches the live API on 20 of 20 visits. Six strings were cut at the crawler's 1,000-character cookie limit, after the fields we decode.

**Still open:**

- Two annotators must fill the 80-script sheet to get κ and precision per technique and per purpose.

Host unit suite: 90 tests pass.

---

Date: 22 November 2026. Changes are uncommitted.

## Original project and research problem

The original `trackmeasure` project is an instrumented Chromium crawler for a roughly 150-site study of browser fingerprinting, storage and cookie respawning. It records page-script API calls, network contacts and consent state across a plain baseline, a repeat baseline, two browser blockers, reject plus Global Privacy Control, and accept. The research problem is that users and researchers cannot infer whether a site's tracking actually stops from the presence of a consent banner or a blocker icon. The project aims to measure observed behavior under controlled, comparable browser visits.

## Contribution of this branch

The `Nasrou` branch turns the original measurement design into a reproducible end-to-end study workflow: it fixes input-list provenance and browser/storage reliability, verifies real blocker operation and VPN egress, records consent confirmation and exclusions, and adds a runner that freezes inputs and produces analysis after each condition. It also adds Windows/OneDrive fixes needed to operate this study on the present machine. These changes make it possible to report which site/runs were actually measured and to separate verified consent comparisons from failed or unknown choices. The branch does not change the underlying research question or make heuristic API use proof of tracking intent.

## Academic assessment

The idea is strong for a course research paper: it asks a concrete, measurable question about whether practical defenses reduce observed tracking, and the repeat baseline and confirmed-consent exclusions improve its credibility. Provisional rating: **7/10 for the idea, 5/10 for submission readiness** until the full EU comparisons, uncertainty estimates, and validation are finished. The strongest contribution is the paired, multi-condition measurement workflow, rather than a claim to invent a new fingerprinting detector.

The main weaknesses are (1) API-use heuristics can count benign uses as fingerprinting, (2) the ranked convenience sample and VPN access failures limit generalization, (3) a single visit date misses temporal variation, (4) many CMP choices may remain unconfirmed and cannot support opt-out conclusions, and (5) token matches after cookie clearing do not by themselves prove fingerprint-derived respawning. The paper should report per-condition eligible denominators, a category-stratified access table, paired effect sizes with uncertainty intervals, repeat-baseline variability, and a manually checked sample of script classifications. A later replicate day and a controlled respawn experiment would strengthen causal claims. If confirmed opt-outs are sparse, frame that as a coverage finding and avoid a consent-effectiveness headline.

## Changes

- Added `scripts/run_study.py` and connected the full-study Make targets to it. It freezes the site list, source hashes and browser settings, checks the VPN exit before and after each condition, supports one to four headful workers, records consent diagnostics and regenerates analysis after each run.
- Made the respawn target create its candidate CSV inside the mounted study directory so the container can read it.
- Fixed `scripts/check_tranco.py` to record the permanent ID of the downloaded list and reject an ID change during download. The 22 November list is `46YLX`: 149 of 150 seed sites were retained; `wirecutter.com` was absent. The ranked CSV and original ZIP are under ignored `data/`.
- Updated the README and handoff notes. Excluded the local Python virtual environment from Git and Docker builds.
- Fixed Windows exit-IP probing (`select()` cannot watch subprocess pipes there), and replaced SQLite WAL journaling with DELETE journaling so successive containers can reopen a crawl database on this OneDrive bind mount. Analysis now opens completed databases immutably.
- The respawn visitor now rejects failed document loads instead of silently snapshotting partial pages. Its fresh-profile verdict is worded as a candidate rather than proof of fingerprint derivation.

## Verification

- Host unit suite: 68 tests passed, including stable Tranco ID, repeated exit-probe reads and respawn navigation validity.
- Host end-to-end smoke test: passed all assertions, including six fingerprinting techniques, blocked requests and zero blocker survival, verified consent exclusions, HTTP failures, and both respawn modes.
- `scripts/run_study.py` compiles and displays its command-line help; `git diff --check` passed.
- Docker image built successfully after excluding `.venv/` from the build context. Container preflight passed with Chromium namespaces and Seccomp-BPF enabled.
- Both pinned real extensions passed their controlled browser blocking checks. Privacy Badger's crawler integration and GPC observation passed.
- All nine cross-frame consent browser fixtures passed in the container.
- The full end-to-end smoke test passed again in the final container image, including its output CSV and respawn assertions. After the storage fix, all six conditions of the live exploratory batch completed and all 68 container unit tests passed.

## Real-site exploratory results

`data/exploratory_nasrou3/crawl.sqlite` and `results/exploratory_nasrou3/` contain the completed five-site batch. The convenience sample was The Guardian, Le Monde, Reddit, CNN and Amazon. All six conditions completed browsing on all five sites. The exit was `196.200.150.221` in Morocco, directly through Docker; this is **not** the planned EU VPN study. Settings were one headful worker, one subpage and five seconds of dwell per page. The manifest records source hashes and the exit.

| Condition | Baseline FP pairs surviving | Median contacted third-party domains | Blocked requests |
| --- | ---: | ---: | ---: |
| Baseline | 8 observed | 8 | 0 |
| Repeat baseline | 7/8 (87.5%) | 8 | 0 |
| uBlock Origin Lite | 2/8 (25%) | 3 | 155 |
| Privacy Badger | 2/8 (25%) | 4 | 111 |

Seven of the eight baseline script-domain/technique pairs came from CNN and one from Le Monde. The pair survival result therefore reflects mostly CNN, and `webgl`/attribute enumeration are weaker signals than the canvas/font and WebRTC heuristics. Privacy Badger also produced three new pairs. The drop from 7/8 in the repeat baseline to 2/8 under each blocker is a descriptive result for this small sample, not a population estimate or proof that all surviving API use is tracking. Contacted third-party domains also fell sharply on CNN (193 at baseline, 13 with uBOL and 39 with Privacy Badger).

Reject+GPC had zero confirmed choices (four failed, one unknown). Accept also had zero confirmed choices (three failed, two unknown). Those ten site/runs remain in the raw per-site output but are excluded from verified consent comparisons. No consent-effectiveness conclusion follows from this direct-Morocco batch.

Manual headful probes showed a normal Amazon storefront despite its HTTP 202 landing response, and a normal Reddit feed despite challenge parameters in its final URL. URL/status alone would have falsely excluded these pages. Challenge-page detection remains a limitation because the crawler does not save or classify each visit's visible content.

The real baseline selected CNN and Amazon for a separate two-site respawn diagnostic. In the rerun with navigation validation (`data/exploratory_nasrou3/respawn_validated.sqlite`), clearing cookies produced 28 distinct matching token strings across nine CNN cookie names, including seven token strings also found in localStorage; Amazon produced none. With a brand-new A2 profile, one CNN token matched while absent in one changed-fingerprint control, and Amazon produced none. An earlier unvalidated pass instead produced one Amazon fresh-profile candidate and none on CNN. This instability, plus multiple token strings per cookie, prevents a claim that fingerprint-derived cookie respawning was established on these sites.

## Completed EU full-study results

The six-condition 149-site EU study finished through a verified Germany OpenVPN exit (`51.75.145.220`). Usable navigation counts: baseline 93/149, repeat 91/93, uBlock 91/93, Privacy Badger 92/93, reject plus GPC 91/93, accept 92/93. The baseline had 56 navigation failures, mostly HTTP 401/403/429 responses. The site list has 50 news, 50 e-commerce, 37 social and 12 affiliate sites; EU access was especially low for e-commerce (22/50). Its first baseline analysis finds heuristic fingerprinting scripts on 64/93 usable sites, with 59/93 before any consent interaction, median 15 contacted third-party domains, and median 1,384 fingerprint API calls. These are observations from instrumented API heuristics, not validated tracking intent. The local VPN profile and public shared credentials are ignored by Git; `results/full_eu_20261122/vpn_provenance.json` contains their nonsecret provenance hashes.

The EU repeat baseline completed 91/93 attempted sites. On those 91 paired sites, 183/192 (95.3%) baseline script-domain/technique pairs reappeared; 11 new pairs appeared. This is the observed short-term noise floor for interpreting the blocker and consent conditions.

The EU uBlock condition completed 91/93 attempted sites. It retained 129/192 baseline pairs (67.2%), blocked 3,630 requests, and had a median of seven contacted third-party domains versus 15 in the EU baseline. On the 56 sites with baseline detections and usable repeat/uBlock visits, uBlock retention was 27.7 percentage points below the repeat baseline (95% site-bootstrap interval -40.0 to -16.3). Its paired median contacted-domain change versus repeat baseline was -6.5 domains on 90 sites (95% site-bootstrap interval -8.5 to -4).

EU Privacy Badger completed 92/93 attempted sites after recovery to a Linux volume. It retained 145/196 baseline pairs (74.0%). On 57 sites with baseline detections and usable repeat/Privacy Badger visits, the paired retention difference was -19.8 percentage points (95% site-bootstrap interval -31.7 to -8.6). Its paired median contacted-domain change was zero on 91 sites (interval -2 to 0), so the domain-contact evidence is weaker than for uBlock. The consent conditions are complete; confirmed-choice coverage is low, as detailed below.

The direct-Morocco full baseline yielded 110/149 complete sites. Access differs by category in the EU batch: news 42/50, social 22/37, e-commerce 22/50, affiliate 7/12. HTTP-rejected and failed pages make the usable sample nonrepresentative of all 149 attempted sites, particularly for e-commerce. Final blocker and consent claims must use paired eligible sites, report this attrition, and avoid extrapolating prevalence to all popular sites. The paper still needs a clear site-selection rationale, uncertainty estimates, and manual validation of a sample of heuristic fingerprinting detections.

Of the EU baseline's 56 failures, 51 involved an HTTP 401, 403, or 429 response. Heuristic fingerprinting scripts appeared on 26/42 usable news sites, 21/22 e-commerce sites, 14/22 social sites, and 3/7 affiliate sites. The very high e-commerce figure may include anti-fraud or functional API use; manual script inspection is needed before calling these trackers.

In the direct batch, the repeat baseline retained 310/334 (92.8%) observed script-domain/technique pairs on 110 paired sites. uBlock Origin Lite retained 152/323 (47.1%) on 109 paired sites, blocked 5,712 requests, and reduced median contacted third-party domains from the baseline's 32.5 to 7.0. Because the repeat and blocker denominators differ by one site, the retention percentages are descriptive rather than a matched effect estimate. The direct batch is a separate Moroccan-egress demonstration, not evidence about EU consent behavior.

On the 94 sites with detectable baseline pairs in both direct comparisons, the site-bootstrap paired difference (uBlock minus repeat baseline) is -45.5 percentage points, with a 95% percentile interval of -54.9 to -36.4 points (`scripts/paired_uncertainty.py`, 2,000 resamples, fixed seed). This interval measures variation across the observed sites; it does not correct for the nonrandom site sample or heuristic classification errors.

For contacted third-party domains, the site-level median change versus repeat baseline was -28 under uBlock (109 paired sites; 95% site-bootstrap interval -40 to -12) and -13.5 under Privacy Badger (110 paired sites; interval -28.5 to -6). These are descriptive differences in contacted domains, not a measure of how many trackers were prevented from obtaining data.

Privacy Badger completed all 110 direct paired sites after SQLite recovery. It retained 183/334 (54.8%) baseline pairs and had a median of 15.5 contacted third-party domains. On the 95 sites with detected baseline pairs, its difference from the repeat baseline is -38.0 percentage points (95% site-bootstrap interval -45.7 to -30.5). The Windows bind-mounted databases produced corruption; the last clean backup was moved into a Docker-managed Linux volume, where the six remaining Privacy Badger sites and both consent conditions completed. The final volume passed SQLite's full integrity check, and its exported workspace copy passed `quick_check`.

All six direct conditions finished. The site counts (attempted/usable) are baseline 149/110, repeat 110/110, uBlock 110/109, Privacy Badger 110/110, reject plus GPC 110/110, and accept 110/108. Reject yielded 72 failed and 38 unknown choices; accept yielded 80 failed, 29 unknown, and one navigation failure. There were **zero confirmed choices** in either direct consent condition, so no direct consent-effectiveness comparison is valid. The complete exported database is `data/full_direct_20261122/crawl.sqlite`; CSV summaries are in `results/full_direct_20261122/`.


The EU reject plus GPC condition yielded 2 confirmed, 36 failed and 53 unknown consent outcomes among usable site visits. The accept condition yielded 1 confirmed, 65 failed and 26 unknown choices; one additional site failed navigation. Only one site per condition entered the verified consent comparison, so these data do not support a general estimate of opt-out effectiveness. Treat consent coverage itself as the result.

The Windows-mounted EU database was recovered using SQLite recovery; baseline and repeat rows matched saved CSV results, while two uBlock site request records were remeasured. Privacy Badger was rerun on all 93 sites. The resulting Linux-volume database passed `PRAGMA integrity_check`, and the exported `data/full_eu_20261122/crawl.sqlite` passed `quick_check`. The final CSVs and uncertainty output are in `results/full_eu_20261122/`. The VPN DNS resolver change and interrupted consent retry are recorded in `vpn_provenance.json`.
