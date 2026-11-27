# Public results bundle — full_eu_20261125

This directory contains selected aggregate and per-site outputs for the completed study batch.

## Files

- `analyze.md` (when present): the written analysis of this batch.

- `per_site.csv`: measurement status and per-site metrics for each condition; URLs in error messages are redacted.
- `comparison.csv`: site-paired domain–technique pair and third-party-domain comparisons.
- `paired_uncertainty.json`: generic purpose-agnostic paired bootstrap estimates, using
  `(site, script domain, technique)`; its `_meta` block distinguishes it from paper results.
- `consent_coverage.csv`: attempted, confirmed, failed, and unknown consent outcomes. Outcomes combine the stored CMP API state (re-assessed with the current rules) with the consent cookies stored at the end of the visit; `confirmed_at_crawl` shows the crawl-time count.
- `fp_scripts.csv`: detected script domains and techniques; script URLs are deliberately omitted because their paths or query strings can contain session tokens.
- `sites.csv`: frozen list of domains used as input.
- `script_purposes.csv`: one row per fingerprinting script (site, script domain, path template) with its purpose label, party (first-party, third-party or hidden first-party vendor), page context, confidence and evidence; script URLs and path templates omitted.
- `hidden_vendor_clusters.csv`: path templates served by two or more sites, attributed to vendors (e.g. bot managers behind first-party URLs).
- `purpose_prevalence.csv`, `purpose_survival.csv`, `purpose_consent_sites.csv`, `purpose_results.json`: results split by purpose (security/anti-fraud, ads/analytics, other/unknown), using `(site, script domain, purpose, technique)`. These are the paper's headline estimates.
- `harm_survival.csv` and the `harm` block of `purpose_results.json`: the same analysis by harm class. Tracking means an advertising or analytics purpose; Service means security or functional. The inclusive sensitivity scenario adds Dual-use and high-capability Undetermined scripts, but is not a formal upper bound. `script_purposes.csv` gives each script's `harm_class`, `capability`, `cross_site`, `sends_attributes` and `writes_storage`.
- `sensitivity_audit.json`: post-hoc checks excluding the accept restart sites, the suspended Privacy Badger duration outlier, and conservatively flagged degraded GPC visits. These checks do not silently change primary eligibility.
- `id_sharing.csv` and `id_sharing.json`: identifier-like cookie values found in requests to another party, such as an analytics client ID sent to its vendor or a cookie synced between ad companies.
  - A value counts as an identifier only if it occurs in one browser profile (one run and site).
  - `id_sharing_strict.csv` additionally requires that cookie name/owner to vary across at least two profiles; `id_sharing.json` reports both summaries and paired exact McNemar tests.
  - `receiver_known_tracker` marks receivers on DuckDuckGo's tracker list.
  - The files contain cookie names and domains, never cookie values or URLs.
- `cookie_isolation.csv` and `cookie_isolation.json`: identifier-like cookie tokens re-issued to the same site across two runs whose Chromium profiles were created independently and deleted afterwards.
  - Cross-site constants, timestamps and consent cookies are excluded; values are never exported.
  - This is evidence of cross-profile value reuse, not proof of fingerprint derivation. The shared exit IP, server state or a site-scoped constant can explain it; `respawn.py` supplies the controlled follow-up protocol.
- `validation_agreement.json` (when present): scores for the 80-script validation sample.
  - It holds the agreement between the two annotators, the detector's precision per technique, and the rule labels' precision per purpose.
  - `method` says who annotated and adjudicated. For 2026-11-25, two independent AI annotators labelled the sample and a third AI reviewer adjudicated their disagreements; none was human.
  - The sheets themselves contain script URLs and stay private.
- `manifest.json`: crawl settings and source hashes; exact VPN exit IP removed.
- `vpn_provenance.json`: public provider/region, resolver-change and collection-incident summary; credential-bearing profile hashes and exact timestamps omitted.

Raw crawl databases and crawl logs are not included. The database can contain cookie values and the logs may contain request details. Error-message URLs are also redacted. Use the repository scripts and the private local batch to reproduce or inspect raw observations. These results describe the accessible sites and this crawler configuration; heuristic API detections are not ground-truth proof of tracking, and purpose labels are evidence-based estimates (see `confidence` and `evidence`), not vendor-confirmed facts.
