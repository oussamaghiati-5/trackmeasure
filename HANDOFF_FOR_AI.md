# TrackMeasure — handoff for a new AI session

Read this file first when continuing work on this repository. It summarizes the project state as of 2026-11-23; verify live files and Git status before making assumptions.

## Project in one paragraph

This is an academic Information Security course project on **defensive measurement of advanced web tracking**. An instrumented Chromium crawler observes browser API patterns associated with fingerprinting, third-party requests, storage and consent state. It compares a baseline, a repeated baseline, uBlock Origin Lite, Privacy Badger, attempted rejection plus GPC, and attempted acceptance. The course brief asks for related scientific work, a clear research question, a meaningful implementation, experimental evaluation, limitations, ethics, and a concise paper in ACM AsiaCCS style.

## User preferences and working context

- The user communicates mainly in French; the paper draft is in English.
- The user expects concrete progress and concise explanations.
- The user emphasized that the role is to fix and improve scripts so the scripts work, rather than manually visiting websites. Avoid manual website browsing unless specifically requested.
- Keep analysis claims tied to saved artifacts. Do not call heuristic API detections proof of tracking, identity creation, or malicious intent.
- Do not expose or commit VPN credentials, `.env` secrets, cookie values, private browser profiles, or raw sensitive crawl data.
- The user is working on branch `Nasrou` in `C:\Users\nasre\OneDrive\Bureau\trackmeasure` (check `git status` before editing; the report and paper draft were untracked at handoff).

## Main study and verified results

Primary batch: `full_eu_20261122`, with a Germany network exit. The input list has 149 sites: 50 news, 50 commerce, 37 social, 12 affiliate. Six conditions were run: `baseline`, `baseline2`, `ubol`, `pbadger`, `optout` (reject + GPC), `accept`.

- Usable baseline measurements: 93/149 (62%). Access differed by category: news 42/50, commerce 22/50, social 22/37, affiliate 7/12. There were 56 unusable baseline visits; 51 involved HTTP 401/403/429. Do not treat failed visits as zero tracking or generalize the successful subset to all sites.
- Heuristics detected at least one fingerprinting-related domain–technique pair on 64/93 baseline sites; 59/93 had one before consent interaction. These are operational API-pattern detections, not validated ground-truth tracker labels.
- Repeat baseline retained 183/192 detected pairs (95.3%) over 91 sites and introduced 11 new pairs. It is the short-term variation control.
- Relative to repeat baseline, paired site-bootstrap change in pair survival was −27.7 percentage points for uBlock Origin Lite (95% interval [−40.0, −16.3], 56 paired sites) and −19.8 points for Privacy Badger (95% interval [−31.7, −8.6], 57 sites).
- Paired median change in contacted third-party domains was −6.5 with uBlock (90 sites; interval [−8.5, −4]) and 0 with Privacy Badger (91 sites; interval [−2, 0]). Blocked-request totals were 3,624 and 2,376, respectively; they are request counts, not counts of trackers/identifiers prevented.
- Consent verification, **updated 2026-11-24.** The crawl-time verifier confirmed only 2 rejections and 1 acceptance, because of two bugs: an empty TCF purpose map after rejection was treated as unreadable, and acceptance required every Global Vendor List vendor to be true. After the fix (`consent.tcf_matches`), offline re-assessment of the stored states (`consent.reassess`) and decoding of the stored consent cookies (`consent.cookie_verdict`), there are **36 verified rejections and 44 verified acceptances, 30 sites both ways.** Do not repeat the old "n = 1 per action" claim. Reject and GPC are still combined.
- Consent result: a verified rejection cuts third-party domains (median 22 against 63 after accept, 30 sites) and third-party cookies (1 against 35), but does **not** reduce fingerprinting: +1.7 pp vs the repeat baseline, 95% CI [−6.4, +11.9].
- Purpose labels (`purpose.py`, `scripts/label_purposes.py`, `scripts/purpose_results.py`):
  - 63% of baseline pairs are security or anti-fraud and 11% ads/analytics; security vendors produce 84% of strong-technique pairs.
  - 27% of pairs come from hidden first-party vendors.
  - uBlock: ads/analytics −88 pp, security −19.8 pp.
  - 10 of 36 verified-reject sites keep ad/analytics fingerprinting patterns, almost all weak.
- Harm classes (`purpose.harm_class`): Tracking (ads/analytics, harmful) / Dual-use / Service (security, functional; useful) / Undetermined (high/low capability). Capability combines identifying power, cross-site reach, attribute transmission seen in URLs, and storage writes.
  - The study focuses on Tracking: 11% of pairs on 17 sites, upper bound 19% on 23 sites.
  - All harmful sites run it before consent.
  - 15 of 16 harmful pairs survive a verified rejection (8 of 36 sites).
  - uBlock removes 88 points of it.
- Main defensible claims: consent and blockers act on different fingerprinting populations. Most strong fingerprinting is bot and fraud management. Purpose labels are evidence-based estimates with confidence levels, and the manual two-annotator validation is still pending.

## Important files

- `PAPER_DRAFT.md` — new English research-paper draft: research questions, related work, methods, results, discussion, limitations and references. It is a draft, not ACM-formatted or submission-ready; author/course placeholders remain.
- `ETUDE_RAPPORT.md` — French detailed report/handoff for colleagues; links to the paper draft.
- `public_results/full_eu_20261122/` — committed-safe result bundle generated by `scripts/export_public_results.py`; script URLs, exact VPN IP, credential-bearing profile hashes, logs, and raw database are excluded.
- `scripts/export_public_results.py` — reproducible exporter for a privacy-reduced results bundle. It does not modify source data and only refreshes an existing bundle when `--overwrite` is passed.
- `Project_S3 (1).pdf` — course brief, located outside the repo at `C:\Users\nasre\Downloads\Project_S3 (1).pdf`. Re-read if needed.
- `README.md` — setup, crawler, verification, and repository instructions.
- `analyze.py` — heuristic fingerprinting-related technique detection and per-site comparisons.
- `crawler.py`, `instrument.js`, `consent.py`, `navigation.py`, `extension_state.py` — crawler, browser instrumentation, consent verification, navigation validation and extension readiness.
- `scripts/paired_uncertainty.py` — paired bootstrap summaries.
- `purpose.py` holds the purpose-labelling rules. `scripts/label_purposes.py` → `script_purposes.csv`, `hidden_vendor_clusters.csv`. `scripts/purpose_results.py` → `purpose_results.json`, `purpose_survival.csv` and the validation sheet. `scripts/purpose_agreement.py` and `docs/annotation_guide.md` cover manual validation. `reference/tracker_radar_subset.json` holds pinned Tracker Radar data. Use `make purpose STUDY_BATCH=full_eu_20261122`.
- Main private batch outputs: `results/full_eu_20261122/analysis.log`, `per_site.csv`, `comparison.csv`, `paired_uncertainty.json`, `consent_coverage.csv`, `fp_scripts.csv`, `manifest.json`, `vpn_provenance.json`.
- The corresponding `data/full_eu_20261122/crawl.sqlite` is local/ignored and may contain cookie values. Do not publish it without a privacy-safe export.

## Research literature already connected to the study

The draft cites and discusses:

1. Englehardt & Narayanan, “Online Tracking: A 1-million-site Measurement and Analysis,” CCS 2016.
2. Acar et al., “The Web Never Forgets: Persistent Tracking Mechanisms in the Wild,” CCS 2014.
3. Iqbal, Englehardt & Shafiq, “Fingerprinting the Fingerprinters: Learning to Detect Browser Fingerprinting Behaviors,” IEEE S&P 2021 (FP-Inspector).
4. Mazel, Garnier & Fukuda, “A Comparison of Web Privacy Protection Techniques,” *Computer Communications*, 2019.
5. Nouwens et al., “Dark Patterns after the GDPR,” CHI 2020.
6. Matte et al., “Do Cookie Banners Respect my Choice?”, IEEE S&P 2020.
7. Utz et al., “(Un)informed Consent,” CCS 2019.
8. Traverso et al., “Benchmark and Comparison of Tracker-blockers,” TMA 2017.

The project relates to this prior work through browser-based measurement, behavioral fingerprinting heuristics, blocker comparisons, repeated-baseline controls, and automated consent-state verification. Do not claim novelty beyond this combination without checking more literature.

## Remaining work / strongest improvements

1. Convert `PAPER_DRAFT.md` to the required ACM AsiaCCS template; fill in authors/course details and verify all citations and final numbers.
2. Two team members annotate `results/full_eu_20261122/validation/annotation_sheet.csv` independently (`docs/annotation_guide.md`), then run `make agreement` and report κ and precision per technique and per purpose in paper Section 4.5. Do not invent precision or recall. Recall is not estimable from this sample.
3. A follow-up crawl, if feasible:
   - separate rejection from GPC;
   - capture request bodies to test whether fingerprint values leave the browser (FP-tracer- or FPTrace-style);
   - crawl login and checkout pages for stronger page-context evidence.
4. If evaluating blocker costs, measure page completion/rendering or another predefined compatibility/performance metric on real sites; current fixture verification only checks extension setup and controlled blocking.
5. Preserve the access failures, denominators, uncertainty intervals, and heuristic caveats in the final paper.

## Before making changes

1. Check `git status --short --branch`; the two Markdown documents may still be untracked or may have been committed since this note was written.
2. Read relevant `README.md` sections and `AGENTS.md` files, if present.
3. Inspect the actual current CSV/JSON outputs before repeating any reported number.
4. Do not run tests unless the user asks for testing or verification. For documentation-only edits, a basic diff/content review is enough.
