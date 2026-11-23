# Measuring Browser Fingerprinting Signals Under Privacy Defenses

**Course project paper draft — working version**
**Authors:** [add team members]
**Affiliation:** [add course / institution]

## Abstract

Browser fingerprinting can expose browser and device characteristics to websites without relying on conventional cookies. We present a controlled measurement study of fingerprinting-related browser API patterns across a curated list of 149 websites in four categories. Using an instrumented Chromium browser, we compare an initial visit, a repeated baseline visit, uBlock Origin Lite, Privacy Badger, and attempted consent decisions. We treat API patterns as heuristic indicators rather than proof of user identification. Of 149 attempted baseline visits, 93 yielded usable measurements. Among paired sites with baseline detections, uBlock Origin Lite reduced the survival of observed domain–technique pairs by 27.7 percentage points relative to repeat-baseline variation (95% site-bootstrap interval: −40.0 to −16.3; 56 sites); Privacy Badger reduced it by 19.8 points (95% interval: −31.7 to −8.6; 57 sites). uBlock also reduced the paired median number of contacted third-party domains by 6.5, while the Privacy Badger median change was zero. Consent-state verification succeeded for only two rejection attempts and one acceptance attempt, so the study cannot estimate consent effects. The results show that the two tested blocker configurations changed measurable browser activity in this sample, while also illustrating the limits of API-based classification, access-biased samples, and automated consent verification.

## 1. Introduction

Web tracking is not limited to cookies. A page can query browser APIs that expose rendering, graphics, audio, network, or device properties. Combinations of these properties may help distinguish browsers, but observing an API call alone does not establish that a script created an identifier, transmitted it, or tracked a person.

This project studies advanced tracking from the defensive measurement and evaluation perspective described in the course project brief. Our goal is to compare browser-observable activity under two privacy extensions and to assess whether an automated crawler can verify consent states on the same sites. We focus on changes in fingerprinting-related signals and third-party contacts; we do not claim to measure the uniqueness of a fingerprint or prove that a particular site identifies users.

We ask:

- **RQ1:** Which fingerprinting-related API patterns are observed during usable baseline visits in this sample?
- **RQ2:** How do uBlock Origin Lite and Privacy Badger change observed domain–technique pairs and third-party contacts relative to repeat-baseline variation?
- **RQ3:** How often can the crawler verify that an attempted accept or reject action produced a persistent consent state?

Our contribution is an end-to-end, reproducible measurement pipeline that combines browser instrumentation, blocker readiness checks, navigation-quality checks, consent-state verification, and paired site-level analysis. The main empirical result is the blocker comparison. The consent experiment is reported as a coverage and protocol finding because too few choices were confirmed for an effectiveness estimate.

## 2. Related Work

Large-scale tracking measurement established that cookie-based and stateless tracking, including fingerprinting, can be studied through instrumented browsers. Englehardt and Narayanan crawled one million sites and measured multiple tracking behaviors and privacy tools, providing a methodological basis for browser-based web measurement ([CCS 2016](https://doi.org/10.1145/2976749.2978313)). Acar et al. examined persistent mechanisms including canvas fingerprinting and cookie respawning ([CCS 2014](https://securehomes.esat.kuleuven.be/~gacar/persistent/the_web_never_forgets.pdf)).

Our fingerprinting heuristics are informed by Iqbal et al.’s FP-Inspector, which detects fingerprinting behaviors using syntactic and semantic information and evaluates detection at scale ([IEEE S&P 2021](https://arxiv.org/abs/2008.04480)). We use explicit API-pattern rules for a smaller set of behaviors. Unlike FP-Inspector, our study does not train or validate a classifier against a labeled ground truth; its detections must therefore be interpreted as candidate behaviors, not confirmed fingerprinting scripts.

Prior work has also compared privacy-protection techniques and tracker blockers. Mazel et al. proposed a methodology for comparing web privacy protection techniques at scale ([Computer Communications](https://www.sciencedirect.com/science/article/pii/S0140366418300604)); TMA researchers benchmarked tracker-blocking extensions ([TMA 2017](https://doi.org/10.23919/TMA.2017.8002898)). We extend this line of work with a repeated baseline to estimate short-term measurement variation and with site-paired comparisons of two currently pinned extension configurations. The results are specific to our browser, extension versions, filter settings, and crawl date.

Consent research has studied both interface design and whether stored consent reflects users’ choices. Nouwens et al. examined GDPR-era consent interfaces and their influence on choice ([CHI 2020](https://doi.org/10.1145/3313831.3376321)). Matte et al. inspected consent stored behind IAB Europe TCF banners and identified cases where stored state did not match the interface choice ([IEEE S&P 2020](https://doi.org/10.1109/SP40000.2020.00076)). Utz et al. studied consent notices in a field setting ([CCS 2019](https://arxiv.org/abs/1909.02638)). Our consent component is narrower: it attempts automated choices and checks readable persisted state; the low confirmation rate prevents a broad behavioral comparison.

## 3. Methodology

### 3.1 Sample and crawl conditions

The frozen input list contains 149 domains: 50 news, 50 commerce, 37 social, and 12 affiliate sites. The main batch used an exit located in Germany. Each condition used a headful Chromium browser under Xvfb (Playwright image 1.63.0), two workers, one subpage where available, five seconds of dwell time, an `en-US` locale, and the `Europe/Amsterdam` time zone. The tested extensions were uBlock Origin Lite 2026.1110.1816 and Privacy Badger 2026.9.15.2. The complete settings and source-file hashes are recorded in `results/full_eu_20261122/manifest.json`; extension versions and package hashes are in `extensions.lock.json`.

The six conditions were: (1) baseline; (2) repeated baseline; (3) uBlock Origin Lite; (4) Privacy Badger; (5) attempted rejection with Global Privacy Control (GPC) enabled; and (6) attempted acceptance. Comparison visits reused the baseline subpage URL when available. A navigation was included only when the visit completed and passed the crawler’s measurement-quality checks. HTTP 401, 403, and 429 responses were retained as failures rather than treated as zero tracking.

### 3.2 Instrumentation and operational measures

The crawler records browser API calls, script URLs and domains, network requests, third-party domains, blocked requests, storage activity, cookies, and consent observations. The analysis groups API calls by visit, frame, and script and applies explicit rules for canvas readback, font enumeration, WebRTC setup, audio processing, WebGL renderer queries, and broad navigator/screen attribute enumeration. These rules are operational heuristics. In particular, WebGL queries and broad attribute enumeration may serve non-tracking purposes, and legitimate anti-fraud or rendering code may satisfy some rules.

For the blocker comparison, a **domain–technique pair** is a pair consisting of the script domain and one detected technique. A baseline pair **survives** when it is observed again in the corresponding condition on the same site. We compare blockers with the repeated baseline, which estimates short-term changes in dynamic content and measurement. We also compare the number of contacted third-party domains at the site level. Blocked-request totals are reported as browser observations, not as counts of trackers or identifiers prevented from reaching a tracker.

Consent actions are counted as confirmed only when the crawler can read and validate a persisted consent state after the action. A click or dismissed banner alone is not confirmation. Rejection and GPC were combined in one condition, so their independent effects cannot be identified.

### 3.3 Paired uncertainty

For blocker analyses, we include sites with usable measurements in both the condition and repeat-baseline runs, and with at least one detected baseline domain–technique pair. We estimate the condition-minus-repeat difference in pair survival and obtain percentile intervals by resampling sites with replacement (2,000 draws; fixed seed). Third-party-domain changes use paired site-level medians with bootstrap intervals. These intervals describe resampling uncertainty within the measured sample; they do not correct selection bias, classifier error, or temporal variation.

## 4. Results

### 4.1 Coverage and baseline signals

The baseline produced usable measurements for 93 of 149 sites (62%). Access varied by category: news 42/50 (84%), commerce 22/50 (44%), social 22/37 (59%), and affiliate 7/12 (58%). Fifty-six baseline attempts were unusable; 51 were associated with HTTP 401, 403, or 429. The successful sample is therefore access-biased, particularly for commerce sites.

Among 93 usable baseline visits, 64 sites had at least one domain–technique pair classified by the heuristics, and 59 had at least one such observation before consent interaction. The most frequent per-site technique categories were WebGL (51 sites), attribute enumeration (49), canvas (34), audio (18), canvas-font measurement (11), and WebRTC (10). Categories overlap. These counts describe API patterns captured by the instrumentation and do not establish that a unique identifier was generated or transmitted.

### 4.2 Repeat baseline and blockers

The repeat baseline retained 183 of 192 baseline pairs (95.3%) across 91 sites and introduced 11 new pairs. This provides evidence that pair presence is not perfectly stable even across short-term repeated visits.

| Condition vs. repeat baseline | Paired sites with detected baseline pairs | Difference in pair survival (percentage points) | 95% site-bootstrap interval | Median change in third-party domains (sites) |
|---|---:|---:|---:|---:|
| uBlock Origin Lite | 56 | −27.7 | [−40.0, −16.3] | −6.5 (90; interval [−8.5, −4]) |
| Privacy Badger | 57 | −19.8 | [−31.7, −8.6] | 0 (91; interval [−2, 0]) |

The blocker runs recorded 3,624 blocked requests for uBlock Origin Lite and 2,376 for Privacy Badger. These totals are not normalized by site traffic and do not identify the purpose of every blocked request. The observed reductions are consistent with both extensions changing measurable activity in this setup; the estimates do not show that all tracking was prevented.

### 4.3 Consent-state verification

Among usable navigations, the reject-plus-GPC condition produced two confirmed choices, 36 failed choices, and 53 unknown outcomes (91 navigations). The accept condition produced one confirmed choice, 65 failed choices, and 26 unknown outcomes (92 navigations). After requiring a confirmed state and a paired comparison, only one site remained for rejection and one for acceptance. No general consent-effect estimate is warranted.

## 5. Discussion

The blocker results are the strongest finding in this dataset. Both extensions reduced the measured survival of domain–technique pairs beyond the short-term change seen in repeated baseline visits; the reduction was larger for uBlock Origin Lite. The third-party-domain comparison agrees for uBlock, while the Privacy Badger median was unchanged. This difference shows why a single metric is insufficient: an extension can alter detected API patterns without producing a corresponding shift in the number of third-party domains.

These findings complement large-scale tracking and protection studies by pairing an instrumented fingerprinting-signal measure with repeated controls and site-level uncertainty estimates. They should not be interpreted as a direct ranking of extension privacy performance. The extension configurations may block different request classes, pages may change when requests are blocked, and our API patterns are not a labeled ground truth for fingerprinting.

The consent component is a useful protocol diagnostic rather than an effectiveness result. The gap between navigable sites and confirmed choices indicates that automated CMP handling and state verification need improvement before this part can support a behavioral claim. A follow-up should separate rejection from GPC, handle additional CMP workflows, and predefine a minimum number of confirmed paired sites before estimating effects.

## 6. Limitations and validity

1. **Heuristic classification:** We did not calculate precision or recall against manually labeled scripts. API patterns may reflect benign rendering, compatibility, or fraud prevention. Conversely, uninstrumented workers, obfuscated code, bundled scripts, and first-party or CNAME-cloaked trackers can evade or confuse attribution.
2. **Access and selection bias:** Only 93 of 149 baseline attempts were usable, with category-specific success rates. Results cannot be generalized to all sites on the input list or the wider web.
3. **Small consent sample:** Three choices were confirmed in total. The combined rejection-plus-GPC condition cannot separate their effects.
4. **One crawl period and network context:** Findings depend on the crawl date, Germany exit, browser locale, browser and extension versions, and page state. The repeat baseline estimates short-term variation only.
5. **Incomplete defense-cost assessment:** We verify extension initialization and controlled blocking behavior, but do not provide a full real-site compatibility, rendering, or performance evaluation.
6. **Observed contacts are not equivalent to tracking:** A third-party request does not by itself prove personal-data collection, identifier exchange, or profiling.

## 7. Ethics and reproducibility

The crawler visits public pages without account login and does not submit forms except for consent controls. The project uses low concurrency and records navigation and measurement failures. Raw crawl databases can contain cookie values and should not be published as-is. The repository contains scripts and aggregate outputs; any public artifact release should remove cookie values and other potentially sensitive browsing data while retaining enough metadata to reproduce the analyses.

The committed, privacy-reduced result bundle is `public_results/full_eu_20261122/`; it contains the per-site table, paired comparison, uncertainty estimates, consent coverage, sanitized fingerprinting observations, input list, and a redacted manifest. The local source outputs are under ignored `results/full_eu_20261122/`; the corresponding crawl database is `data/full_eu_20261122/crawl.sqlite` and is intentionally excluded from version control. Raw script URLs, the exact VPN exit IP, VPN profile hashes, crawl logs, and the raw database are not part of the public bundle. The analysis can be regenerated from the private exported database with the repository's analysis scripts.

## 8. Conclusion

In this German-exit crawl, uBlock Origin Lite and Privacy Badger reduced the survival of heuristically detected domain–technique pairs relative to repeated-baseline variation, with a larger estimated reduction for uBlock. uBlock also reduced the paired median number of third-party domains; Privacy Badger did not change that median. Because the fingerprinting indicators are not validated labels, these results concern observed browser behavior rather than confirmed user tracking. Automated consent verification succeeded too rarely to estimate the effect of user choices. The study’s main value is a reproducible comparison with explicit controls and exclusions, alongside a clear account of what its measurements cannot establish.

## References

1. Steven Englehardt and Arvind Narayanan. 2016. Online Tracking: A 1-million-site Measurement and Analysis. In *Proceedings of the 2016 ACM SIGSAC Conference on Computer and Communications Security (CCS '16)*. [https://doi.org/10.1145/2976749.2978313](https://doi.org/10.1145/2976749.2978313)
2. Gunes Acar et al. 2014. The Web Never Forgets: Persistent Tracking Mechanisms in the Wild. In *Proceedings of the 2014 ACM SIGSAC Conference on Computer and Communications Security (CCS '14)*. [Author PDF](https://securehomes.esat.kuleuven.be/~gacar/persistent/the_web_never_forgets.pdf)
3. Umar Iqbal, Steven Englehardt, and Zubair Shafiq. 2021. Fingerprinting the Fingerprinters: Learning to Detect Browser Fingerprinting Behaviors. In *2021 IEEE Symposium on Security and Privacy*. [https://arxiv.org/abs/2008.04480](https://arxiv.org/abs/2008.04480)
4. Johan Mazel, Richard Garnier, and Kensuke Fukuda. 2019. A Comparison of Web Privacy Protection Techniques. *Computer Communications* 144 (2019), 162–174. [https://doi.org/10.1016/j.comcom.2019.04.005](https://doi.org/10.1016/j.comcom.2019.04.005)
5. Midas Nouwens et al. 2020. Dark Patterns after the GDPR: Scraping Consent Pop-ups and Demonstrating their Influence. In *Proceedings of the 2020 CHI Conference on Human Factors in Computing Systems*. [https://doi.org/10.1145/3313831.3376321](https://doi.org/10.1145/3313831.3376321)
6. Claudio Matte et al. 2020. Do Cookie Banners Respect my Choice? Measuring Legal Compliance of Banners from IAB Europe's Transparency and Consent Framework. In *2020 IEEE Symposium on Security and Privacy*. [https://doi.org/10.1109/SP40000.2020.00076](https://doi.org/10.1109/SP40000.2020.00076)
7. Christine Utz et al. 2019. (Un)informed Consent: Studying GDPR Consent Notices in the Field. In *Proceedings of the 2019 ACM SIGSAC Conference on Computer and Communications Security (CCS '19)*. [https://arxiv.org/abs/1909.02638](https://arxiv.org/abs/1909.02638)
8. Stefano Traverso, Martino Trevisan, Leonardo Giannantoni, Marco Mellia, and Hassan Metwalley. 2017. Benchmark and Comparison of Tracker-blockers: Should You Trust Them? In *2017 Network Traffic Measurement and Analysis Conference (TMA)*. [https://doi.org/10.23919/TMA.2017.8002898](https://doi.org/10.23919/TMA.2017.8002898)

---

**Before submission:** add authors and course details; convert this draft to the required ACM template; verify final values against the versioned result files; and keep the limitations above in the submitted paper. The consent findings should not be promoted to a general claim unless a follow-up obtains substantially more confirmed paired choices.
