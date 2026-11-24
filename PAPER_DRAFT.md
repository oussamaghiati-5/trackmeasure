# Who Fingerprints, and Does Saying No Stop It? Purpose-Labelled Fingerprinting Under Verified Consent and Content Blockers

**Course project paper draft, working version (updated 24 November 2026)**
**Authors:** [add team members]
**Affiliation:** [add course / institution]

## Abstract

API-pattern detectors can say *that* a script fingerprints the browser, but not *why*. Our professor asked how to tell security fingerprinting from tracking fingerprinting. We crawled 149 popular websites from a German exit with an instrumented Chromium under six conditions: baseline, repeat baseline, uBlock Origin Lite, Privacy Badger, reject plus GPC, and accept. We label each detected fingerprinting script by purpose with an evidence ladder: vendor signatures, DuckDuckGo Tracker Radar categories, page context in the style of FP-Redemption, first-party path clusters that expose hidden vendors, and vendor cookies. We also verify consent state from two independent sources: the live CMP API and the consent cookies stored at the end of the visit.

On 93 usable sites, 64 ran fingerprinting-pattern scripts. 63% of the detected (site, script, technique) pairs were security or anti-fraud and 11% were advertising or analytics, and security vendors produced 84% of the strong-technique pairs. 27% of pairs came from bot managers served through the site's own URLs. After a bug fix, consent verification rose from 3 confirmed choices to 36 verified rejections and 44 verified acceptances. A verified rejection removed third-party contacts (median 22 against 63 after acceptance, on 30 sites) and third-party cookies (1 against 35). It did not reduce fingerprinting: pair survival relative to the repeat baseline changed by +1.7 points (95% CI −6.4 to +11.9). 10 of 36 sites still ran ad-tech or analytics fingerprinting patterns after a verified "no", although almost all of these were weak signals. uBlock Origin Lite removed 88 points of ad/analytics fingerprinting but only 20 points of security fingerprinting. Content blockers and consent therefore act on different fingerprinting populations, and a single "fingerprinting" count hides this.

## 1. Introduction

Browser fingerprinting reads stable properties of the browser and device, such as canvas rendering, fonts, audio processing, WebGL and navigator attributes. Combined, they can recognise a browser without cookies. Detection tools since Englehardt and Narayanan [1] flag scripts that call these APIs in characteristic ways, and FP-Inspector [3] learns the patterns. A detection, however, does not say why the script runs.

The same canvas readback is used by an ad-tech vendor to re-identify a visitor and by a bot-management vendor to decide whether the visitor is a human. FP-Redemption [9] and Senol et al. [10] show that much fingerprinting serves web security, and that the same script can serve both purposes. European law treats the two differently. Fingerprinting falls under Art. 5(3) of the ePrivacy Directive [16], so tracking needs consent, while what is strictly necessary for a service the user requested, which can include its security, may be exempt. A measurement that counts all fingerprinting together cannot say whether a site that keeps fingerprinting after "reject all" is breaking the rules or protecting its login page.

We ask:

- **RQ1. Who fingerprints, and why?** Which share of detected fingerprinting is security or anti-fraud, advertising or analytics, or unknown, and how much is served by vendors hidden behind first-party URLs?
- **RQ2. Does rejecting consent stop it?** With the stored consent state verified, does a rejection change fingerprinting, compared with its effect on cookies and third-party contacts, and for which purposes?
- **RQ3. What do content blockers stop?** Which fingerprinting survives uBlock Origin Lite and Privacy Badger, by purpose, signal strength and party?

Contributions:

1. **A purpose-labelling method for fingerprinting scripts.** It uses vendor signatures, Tracker Radar, page context, path clusters and vendor cookies, and outputs a primary and secondary purpose, a party, a confidence level and an evidence trail. It is validated by agreement between independent sources and by behavioural checks, and comes with a stratified 80-script manual-validation protocol.
2. **Two-source consent verification.** It combines the live CMP API with offline decoding of stored IAB TCF, OneTrust and Cookiebot cookies. It fixes two verifier errors that had turned the consent question into a negative result.
3. **A 2026 EU replication of Papadogiannakis et al. [8] with verified consent state and purpose labels.** We find that consent and blockers act on different fingerprinting populations.

## 2. Related Work

**Detecting fingerprinting.** Englehardt and Narayanan crawled one million sites and introduced API-pattern rules for canvas, font, WebRTC and audio fingerprinting [1]. Acar et al. measured canvas fingerprinting and respawning [2]. FP-Inspector [3] learns fingerprinting behaviour from static and dynamic features and finds substantially more fingerprinting scripts than hand-written rules. We keep the transparent rules of [1], adapted as in [3], and add manual validation (Section 3.6) rather than a classifier. FP-tracer [11] follows attribute values from source to network sink. FPTrace [12] shows causally that fingerprints are used for ad targeting by changing the fingerprint and watching ad bids. Both answer "is it used for tracking" more directly than we can, and Section 6 explains how our pipeline would extend towards them.

**Why scripts fingerprint.** FP-Redemption [9] infers security use from context: scripts present on every page suggest bot detection, and scripts present only on sign-up or payment pages suggest fraud prevention. Senol et al. [10] study fingerprinting on authentication pages and stress its dual nature. DuckDuckGo Tracker Radar [13] assigns usage categories (Advertising, Analytics, Fraud Prevention, Ad Fraud, CDN and others) and a 0–3 fingerprinting score per domain. The score measures the likelihood of fingerprinting-API use, not intent. We combine these sources and add first-party path clustering and vendor cookies.

**Consent.** Matte et al. [6] decoded stored TCF consent strings and found banners that did not respect choices. Nouwens et al. [5] and Utz et al. [7] studied consent interfaces. Papadogiannakis et al. [8] found that about 73.5% of fingerprinting websites fingerprint regardless of the user's consent choice. We repeat their question with consent state verified from two sources and with purpose labels, which lets us separate potentially exempt security fingerprinting from ad-tech fingerprinting.

**Blockers and measurement validity.** Traverso et al. [4] benchmarked tracker blockers. Amjad et al. [14] show that blocking scripts that mix tracking and functionality breaks sites. Krumnow et al. [15] show that measurement tools can be detected, and that hardening them changes the tracking that is measured. This matters here because the scripts we observe most often are bot detectors.

## 3. Methodology

### 3.1 Sample and crawl conditions

The frozen input list contains 149 domains: 50 news, 50 e-commerce, 37 social and 12 affiliate sites. They come from the 22 November Tranco list `46YLX`. The batch used an OpenVPN exit in Germany. It ran a headful Chromium under Xvfb (Playwright 1.63.0) with two workers, one subpage, five seconds of dwell, the `en-US` locale and the `Europe/Amsterdam` time zone. The six conditions were baseline, repeat baseline, uBlock Origin Lite 2026.1110.1816, Privacy Badger 2026.9.15.2, reject plus Global Privacy Control (GPC), and accept. Each condition used a fresh profile. Settings and source hashes are in `manifest.json`, and extension package hashes are in `extensions.lock.json`.

### 3.2 Fingerprinting detection

An injected script records browser API calls with the calling script and frame. Per script, frame and visit, we apply the rules of [1] as adapted in [3]:

- canvas: text with at least 10 distinct characters plus a non-lossy readback of at least 16×16 pixels
- canvas font: at least 50 distinct fonts measured
- WebRTC: a data channel plus an offer
- audio: an oscillator, a compressor and a readback
- WebGL: an unmasked vendor or renderer query
- attribute enumeration: at least 12 distinct navigator or screen attributes read

The first four are **strong** techniques. WebGL and attribute enumeration are **weak**, because ordinary rendering and feature detection also trigger them. A **pair** is (site, script domain, technique). For purpose-level analysis, a pair is (site, script domain, purpose, technique).

### 3.3 Purpose labelling

The unit of analysis is a **script**, defined as (site, script domain, path template). Templates collapse UUID, hex, numeric and random mixed-case path segments. A bot-manager script whose URL changes on every visit, such as Akamai's sensor path, therefore stays one script. For each script, `scripts/label_purposes.py` records:

- techniques
- runs in which it was detected
- the page types where it ran: home, or the subpage with its type (article, product, login, checkout or payment, other)
- whether it read `navigator.webdriver` or `Permissions.query`, which are automation probes
- vendor cookies on the site: Akamai `_abck`/`bm_sz`, HUMAN `_px*`, DataDome, Kasada `KP_UIDz`, AWS WAF, Imperva, Forter, Fastly `_fs_ch_*`
- the domain's Tracker Radar entry, fetched at a pinned commit and committed under `reference/`

Labels follow an evidence ladder (`purpose.py`):

1. **Vendor signatures.** These are 35 third-party vendor patterns, from HUMAN, DataDome, AWS WAF and reCAPTCHA through Integral Ad Science, Prebid and AppsFlyer to HubSpot. There are also 8 first-party path signatures for bot managers served from the site's own domain, such as Kasada's `/149e9513-…/2d206a39-…/ips.js`, Akamai's `/akam/13/…`, Cloudflare's `/cdn-cgi/challenge-platform/`, Fastly's `/_fs-ch-…/` and DataDome's `tags.js` on a site subdomain.
2. **Tracker Radar categories**, mapped to Advertising, Analytics, Anti-fraud/security, Functional or CDN. They apply only to third-party scripts not owned by the site's owner, because the categories describe a domain *as a third party*.
3. **Path clusters.** These are path templates served by two or more sites under different domains. A first-party script in a cluster, or one that matches a first-party path signature, is labelled a **hidden first-party vendor**. An Akamai random-path sensor script is attributed only when the site also carries Akamai cookies or an `/akam/` beacon.
4. **Page context and behaviour (FP-Redemption-style, low confidence).** These rules apply only when steps 1–3 give no purpose. A script present only on login or checkout pages is labelled anti-fraud. A script present on all crawled pages that also reads `navigator.webdriver` and uses a strong technique or at least three techniques is labelled anti-bot.
5. Everything else is **Unknown**. We do not force a category.

Confidence is High when two independent sources agree, for example a signature plus Tracker Radar or a vendor cookie. It is Medium for a single signature or Tracker Radar category, and Low for context rules, CDN-only categories and Unknown. Ad-verification vendors (IAS, Ad-Score, invalid-traffic detection) are labelled Advertising with Anti-fraud/security as the secondary purpose. This is Senol et al.'s dual use: their fraud checks protect advertisers, inside the ad supply chain.

### 3.4 Consent verification from two sources

The crawler attempts the choice through CMP JavaScript APIs or by clicking banner buttons in seven languages, and stores every frame's CMP state. A click alone never counts. We verify the saved state in two ways.

**Live CMP API.** `__tcfapi` and Cookiebot are queried after the action and after a reload. The verifier stored with the crawl had two errors:

- After "reject all", TCF CMPs often return an empty purpose map `{}`. The verifier treated this as unreadable, although missing purpose IDs mean no consent.
- The vendor map covers the whole Global Vendor List. After "accept all", vendors the site never asked about stay false, but the verifier required every entry to be true. On mirror.co.uk, 982 of 1,673 entries were true.

The fixed rule is in `consent.tcf_matches`. A rejection must leave no purpose, vendor or special-feature consent. An acceptance must grant at least 9 of TCF purposes 1–10 and at least one vendor. Stored states are re-assessed with this rule without a new crawl (`consent.reassess`).

**Stored cookies.** At the end of each visit we decode:

- the IAB TCF v2 `euconsent-v2` core string: purposes, special features, and vendor consents in both bit-field and range encoding
- OneTrust `OptanonConsent` groups, counted only when `OptanonAlertBoxClosed` shows a real choice
- Cookiebot `CookieConsent`

Our decoder agrees with the live `__tcfapi` purposes and vendor counts on 20 of 20 visits where both exist.

A choice is **verified** when either source matches the request and neither shows the opposite. Conflicts are excluded as unknown.

### 3.5 Comparisons and uncertainty

We compare each condition with the baseline on sites with usable measurements in the condition, the baseline and the repeat baseline. For the consent conditions, we further require a verified choice. A baseline pair **survives** when it is seen again in the condition's post-consent phases. We report the condition's survival minus the repeat baseline's survival, with 95% percentile intervals from 2,000 site-level bootstrap resamples (`scripts/paired_uncertainty.py`), overall and per purpose group: Security, Ads/analytics, and Other/unknown.

### 3.6 Validation of the labels

Without ground truth, we use four checks:

1. agreement between two independent label sources, vendor signatures and Tracker Radar
2. convergent validity: security scripts should probe for automation more often than ad scripts
3. the composition of techniques by purpose
4. a stratified manual sample of 80 scripts, by strongest technique and party, for two independent annotators (`docs/annotation_guide.md`), scored with Cohen's kappa and precision per technique and per purpose (`scripts/purpose_agreement.py`)

## 4. Results

### 4.1 Coverage

The baseline was usable on 93 of 149 sites (62%): news 42/50, e-commerce 22/50, social 22/37 and affiliate 7/12. Of the 56 failures, 51 were HTTP 401, 403 or 429 responses. Section 4.2 shows that the sites we reached run bot management heavily. The failures are therefore probably bot blocking, and the excluded sites are probably the ones that fingerprint most.

### 4.2 RQ1: who fingerprints, and why

64 of 93 usable sites ran at least one fingerprinting-pattern script in the baseline, with 237 (site, script domain, purpose, technique) pairs. The labels cover 75.5% of pairs and 87.8% of strong-technique pairs. Of the labelled pairs, 95 are High confidence, 64 Medium and 20 Low.

| Purpose | Pairs | Strong-technique pairs | Sites |
|---|---:|---:|---:|
| Anti-fraud/security | 149 (63%) | 76 | 42 |
| Advertising + analytics | 26 (11%) | 3 | 17 |
| Unknown, functional, CDN | 62 (26%) | 11 | 28 |

- **Strong techniques mostly mean security.** 76 of the 90 strong-technique pairs (84%) come from security vendors. Ad-tech and analytics scripts use the weak techniques: attribute enumeration (IAS, Prebid, GumGum, the X Ads pixel, AppsFlyer) and the WebGL renderer query.
- **E-commerce fingerprinting is bot and fraud management.** 21 of 22 usable e-commerce sites fingerprint. 20 of them run security fingerprinting, 10 run nothing else, and only one shows an ad/analytics pattern. On news sites, 12 of 42 run security fingerprinting and 11 run ad/analytics fingerprinting.
- **Hidden first-party vendors.** 63 of 237 pairs (27%) come from vendors served through the site's own URLs. Clusters, each corroborated by vendor cookies:
  - Akamai Bot Manager's `/akam/` beacon on 10 sites, plus its random-path sensor script on the same sites
  - Cloudflare Bot Management on 10
  - Kasada on 5: chewy, costco, nike, otto and twitch, with an identical path
  - DataDome `tags.js` on site subdomains (airbnb, elpais, soundcloud)
  - Fastly's bot challenge on 4 (lemonde, newsweek, wikihow, bloomberg)

  Script-domain attribution counts all of these as first-party, and blockers cannot remove them by domain.
- **Before consent.** 17 sites ran ad/analytics fingerprinting patterns before any consent interaction, but only 2 with a strong technique: HubSpot on techcrunch.com, and an Adobe tag on corriere.it.

### 4.3 RQ2: does rejecting consent stop fingerprinting?

Two-source verification raised verified choices from 3 to 80:

| | Crawl-time verifier | Fixed live API | Stored cookie | Combined |
|---|---:|---:|---:|---:|
| Reject + GPC (91 usable) | 2 | 29 | 29 | **36** |
| Accept (92 usable) | 1 | 34 | 34 | **44** |
| Verified both ways | 0 | – | – | **30** |

The two sources agreed on 20 rejections and 24 acceptances, and each added sites the other could not read. Two reject attempts (cnn.com, nbcnews.com) stored an "all groups on" OneTrust state and count as failed. Two others had a live rejection but a mixed cookie and are excluded.

On the 30 dual-verified sites, the choice matters a great deal for cookies and third parties. After acceptance the median site contacted 63 third-party domains, against 22 after rejection, and accept exceeded reject on all 30 sites. The median third-party cookie count was 35 against 1. Fingerprinting barely moved:

| Verified reject vs repeat baseline | Sites | Pair survival | Difference (pp) | 95% CI |
|---|---:|---:|---:|---:|
| All purposes | 19 | 89.8% | +1.7 | [−6.4, +11.9] |
| Security | 11 | 86.1% | −2.8 | [−10.7, 0.0] |
| Ads/analytics | 9 | 93.8% | −6.3 | [−23.1, 0.0] |

After a verified rejection, 29 of 36 sites still ran fingerprinting-pattern scripts. On 12 sites these were security scripts. On 10 sites they were ad-tech or analytics scripts:

- Integral Ad Science (washingtonpost.com, theverge.com)
- GumGum, AnyClip and Prebid (dictionary.com)
- the X Ads pixel (vice.com)
- AppsFlyer (weather.com)
- HubSpot (techcrunch.com)
- Adobe tags (elpais.com)
- Mantis (mirror.co.uk)
- an ad-recovery network (huffpost.com, buzzfeed.com, mirror.co.uk)

These ad/analytics cases were almost all weak signals: 16 of 18 pairs are attribute enumeration or the WebGL renderer query. The one strong case is HubSpot analytics (canvas and audio) on techcrunch.com. Of 21 strong-technique pairs after rejection, 19 are security vendors. Accepting added ad/analytics fingerprinting: on the dual-verified sites there were 23 ad/analytics pairs after acceptance against 11 after rejection, on 11 sites against 7.

We conclude that rejection removes the cookie and third-party layer of tracking but not the fingerprinting layer. Most fingerprinting that survives a "no" is security fingerprinting, which may be exempt as strictly necessary. A minority is ad-tech attribute collection, which is not clearly exempt. Because API reads alone cannot show that a fingerprint left the browser (Section 6), we report these as potential, not demonstrated, consent violations.

### 4.4 RQ3: what do content blockers stop?

| Survival vs repeat baseline (pp, 95% CI) | All | Security | Ads/analytics | Other/unknown |
|---|---:|---:|---:|---:|
| uBlock Origin Lite (56 sites) | −27.5 [−40.2, −16.2] | −19.8 [−34.7, −7.2] | **−88.0 [−100, −72.7]** | −16.9 [−32.7, −3.6] |
| Privacy Badger (57 sites) | −19.9 [−31.5, −9.7] | −13.7 [−25.0, −2.4] | −40.0 [−64.5, −13.0] | −23.7 [−40.6, −7.5] |

uBlock Origin Lite removed 22 of 25 ad/analytics pairs but kept 77% of security pairs, which are 58% of all baseline pairs. The headline "−27.7 points" is therefore an average of two different effects. Blockers do the job of a tracker blocker well against ad-tech, and leave the bulk of strong fingerprinting, which is bot management that is often first-party, in place. Blocking security scripts also has a cost we do not measure: uBlock removed HUMAN, Forter, Riskified, Stripe and DataDome pairs, which protect checkouts.

We also saw circumvention. On nordstrom.com, under both blockers, Forter's script loaded from a CloudFront host, with the same hashed filename as on `forter.com`. On bild.de, mirror.co.uk and tomsguide.com, an ad-recovery network that the baseline served from `html-load.*` reappeared as extension-less UUID paths on the site's own domain, mostly in the Privacy Badger run. That run also produced 10 new ad/analytics pairs.

### 4.5 Validity of the purpose labels

- **Two independent sources agree moderately.** On the 12 third-party domains with both a vendor signature and a Tracker Radar purpose, raw agreement is 67% and Cohen's κ = 0.49. The four disagreements are secondary-purpose differences:
  - Stripe: fraud signals against online payment
  - Cloudflare: Turnstile against analytics
  - Adobe and HubSpot: analytics against advertising

  Tracker Radar alone could label only 18 of 59 third-party script domains: 54 were in Tracker Radar, but most had no usage category. Signatures and context are needed.
- **Behaviour agrees with the labels.** Using only labels that rest on vendor, Tracker Radar or cookie evidence, and not on behaviour, 93% of security scripts read `navigator.webdriver` (132/142, 95% CI 88–96%), against 68% of ad/analytics scripts (44/65, CI 56–78%). Fisher's exact p < 10⁻⁵. 79% of security scripts use a strong technique, against 12% of ad/analytics scripts.
- **Tracker Radar's fingerprinting score is not a purpose signal.** Mean scores are 2.35 for advertising, 2.71 for analytics and 1.76 for security, which confirms that the score measures API use, not intent.
- **Manual validation is pending.** The 80-script stratified sample is generated. Detector precision per technique and label precision per purpose will be reported from two annotators' independent labels. The rule labels in the sample are 38 security, 30 unknown, 8 advertising, 3 analytics and 1 functional.

## 5. Discussion: how to tell security fingerprinting from tracking fingerprinting

Our answer to the professor's question is that **no single signal separates the two, but a ladder of evidence separates most of it, and the rest should be reported as Unknown.**

- **Vendor identity is the strongest signal.** It is recoverable for first-party URLs through path templates and vendor cookies.
- **Tracker Radar gives an independent but sparse second opinion.**
- **Context and behaviour are weak alone** but consistent with the vendor labels. Bot managers run on every page and probe for automation. Ad-tech enumerates attributes.

Purpose labels change the conclusions of a fingerprinting study. Without them we would have reported that rejection "does not stop fingerprinting" and that uBlock "reduces it by 28 points". With them: rejection leaves mostly security fingerprinting plus weak ad-tech attribute collection on a minority of sites, and uBlock removes ad-tech fingerprinting almost completely while leaving bot management.

## 6. Limitations and validity

1. **An API read is not proof of tracking.** We do not observe whether values leave the browser or feed an identifier. FP-tracer [11] (taint tracking) and FPTrace [12] (ad-bid changes) close this gap. Capturing request bodies and searching them for the browser's known attribute values is a cheap next step.
2. **Purpose labels are estimates.** 26% of baseline pairs remain Unknown, mostly inline WebGL checks, platform bundles (YouTube, Meta, X, Temu) and rotating, unattributed domains. Manual validation is pending. The signature list was built by inspecting this batch, so it may fit this batch better than new data.
3. **Page context is shallow.** We crawl the home page and one subpage, with no login, cart or checkout pages. FP-Redemption's strongest context signals are therefore only partly available.
4. **Selection.** 56 of 149 sites were unusable, mostly through bot blocking. The verified-consent sites are mainly large TCF or OneTrust publishers. Reject is combined with GPC.
5. **One crawl, one exit, one browser profile.** The profile was en-US with an Amsterdam time zone behind a German IP. This inconsistency may itself trigger bot detection.
6. **Consent decoding.** We decode TCF purposes, special features and vendor consents, but not legitimate-interest fields. The decoder matches the live API on 20/20 visits, but has not yet been checked against IAB's reference library.
7. **Blocker cost is not measured** (breakage, page load, failed checkout) [14].

## 7. Ethics and reproducibility

The crawler visits public pages without logging in and touches only consent controls. Raw databases, which contain cookie values, and crawl logs are not published. `public_results/full_eu_20261122/` contains per-site tables, comparisons, uncertainty estimates and purpose labels, without script URLs, path templates or the VPN exit IP. Everything can be regenerated offline:

```
python analyze.py data/full_eu_20261122/crawl.sqlite --outdir results/full_eu_20261122   # consent re-assessed
python scripts/paired_uncertainty.py results/full_eu_20261122/comparison.csv
make purpose STUDY_BATCH=full_eu_20261122                                                 # labels + purpose results
```

The Tracker Radar subset (CC BY-NC-SA 4.0) is pinned to commit `a358dac` and committed under `reference/`.

## 8. Conclusion

In a 149-site EU crawl, most fingerprinting, and almost all strong fingerprinting, is bot and fraud management, often served from the site's own URLs. Verified consent rejection removes third parties and cookies but leaves fingerprinting in place: mostly security scripts, plus weak ad-tech attribute collection on 10 of 36 sites. Content blockers remove ad-tech fingerprinting almost completely and leave bot management. Purpose labelling turns "fingerprinting went up or down" into statements that a privacy engineer or a regulator can act on. The next step is to show which fingerprints leave the browser and feed an identifier.

## References

1. S. Englehardt and A. Narayanan. 2016. Online Tracking: A 1-million-site Measurement and Analysis. *CCS '16*. https://doi.org/10.1145/2976749.2978313
2. G. Acar et al. 2014. The Web Never Forgets: Persistent Tracking Mechanisms in the Wild. *CCS '14*.
3. U. Iqbal, S. Englehardt and Z. Shafiq. 2021. Fingerprinting the Fingerprinters: Learning to Detect Browser Fingerprinting Behaviors. *IEEE S&P 2021*. https://arxiv.org/abs/2008.04480
4. S. Traverso et al. 2017. Benchmark and Comparison of Tracker-blockers: Should You Trust Them? *TMA 2017*. https://doi.org/10.23919/TMA.2017.8002898
5. M. Nouwens et al. 2020. Dark Patterns after the GDPR: Scraping Consent Pop-ups and Demonstrating their Influence. *CHI 2020*. https://doi.org/10.1145/3313831.3376321
6. C. Matte, N. Bielova and C. Santos. 2020. Do Cookie Banners Respect my Choice? *IEEE S&P 2020*. https://doi.org/10.1109/SP40000.2020.00076
7. C. Utz et al. 2019. (Un)informed Consent: Studying GDPR Consent Notices in the Field. *CCS '19*. https://arxiv.org/abs/1909.02638
8. E. Papadogiannakis et al. 2021. User Tracking in the Post-cookie Era: How Websites Bypass GDPR Consent to Track Users. *WWW '21*. https://arxiv.org/abs/2102.08779
9. A. Durey et al. 2021. FP-Redemption: Studying Browser Fingerprinting Adoption for the Sake of Web Security. *DIMVA 2021*. https://hal-univ-tlse3.archives-ouvertes.fr/INRIA/hal-03212726
10. A. Senol et al. 2024. The Double Edged Sword: Identifying Authentication Pages and their Fingerprinting Behavior. *WWW '24*. https://doi.org/10.1145/3589334.3645493
11. S. Boussaha et al. 2024. FP-tracer: Fine-grained Browser Fingerprinting Detection via Taint-tracking and Entropy-based Thresholds. *PoPETs 2024*. https://petsymposium.org/popets/2024/popets-2024-0092.php
12. Z. Liu et al. 2025. The First Early Evidence of the Use of Browser Fingerprinting for Online Tracking (FPTrace). *WWW '25*. https://arxiv.org/abs/2409.15656
13. DuckDuckGo. Tracker Radar. https://github.com/duckduckgo/tracker-radar (commit a358dac, 23 November 2026).
14. A. H. Amjad et al. 2023. Blocking JavaScript without Breaking the Web: An Empirical Investigation. *PoPETs 2023*. https://petsymposium.org/popets/2023/popets-2023-0087.php
15. B. Krumnow et al. 2022. How gullible are web measurement tools? A case study analysing and strengthening OpenWPM's reliability. *CoNEXT '22*. https://bkrumnow.github.io/openwpm-reliability
16. European Data Protection Board. 2023. Guidelines 2/2023 on Technical Scope of Art. 5(3) of ePrivacy Directive.
17. I. Fouad et al. 2022. My Cookie is a phoenix: detection, measurement, and lawfulness of cookie respawning with browser fingerprinting. *PoPETs 2022*. https://petsymposium.org/popets/2022/popets-2022-0063.php
18. M. S. M. S. Annamalai et al. 2025. Beyond the Crawl: Unmasking Browser Fingerprinting in Real User Interactions. *WWW '25*. https://arxiv.org/abs/2502.01608

---

**Before submission:**

- Add the authors.
- Convert the paper to the ACM AsiaCCS template.
- Run the two-annotator validation (`docs/annotation_guide.md`) and fill in Section 4.5.
- Optionally run `scripts/check_tcf_official.mjs` to confirm the TCF decoder against IAB's library.
- Keep the limitations in the submitted version.
