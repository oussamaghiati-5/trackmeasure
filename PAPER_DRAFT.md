# Harmful or Useful? Separating Tracking Fingerprinting from Security Fingerprinting Under Verified Consent and Content Blockers

**Course project paper draft, working version (updated 24 November 2026)**
**Authors:** [add team members]
**Affiliation:** [add course / institution]

## Abstract

API-pattern detectors can say *that* a script fingerprints the browser, but not *why*. Fingerprinting that defends a site against bots and fraud is useful, and in EU law it can be strictly necessary. Fingerprinting that recognises the user for advertising or profiling is harmful, and needs consent. We separate the two and focus the study on the harmful kind.

We crawled 149 popular websites from a German exit with an instrumented Chromium under six conditions: baseline, repeat baseline, uBlock Origin Lite, Privacy Badger, reject plus GPC, and accept. Each detected script gets a harm class built from two parts:

- **Purpose,** from an evidence ladder: vendor signatures, DuckDuckGo Tracker Radar categories, first-party path clusters that expose hidden vendors, vendor cookies, and page context in the style of FP-Redemption.
- **Tracking capability:** how identifying the techniques are, cross-site reach, whether the browser's own values appear in requests to that vendor, and storage writes.

We verify consent state from two independent sources: the live CMP API and the consent cookies stored at the end of the visit.

On 93 usable sites, 64 ran fingerprinting-pattern scripts. Most of it is useful, not harmful. 64% of the detected (site, script, technique) pairs serve security or functionality, and these produce 76 of 90 strong-technique pairs. Harmful (tracking) fingerprinting is 11% of pairs on 17 sites, up to 19% on 23 sites when dual-use and high-capability unknown scripts are included. It mostly collects attributes: 3 of its 26 pairs use a strong technique. The X Ads pixel sent the browser's time zone and platform to its own servers on 9 sites.

All 17 harmful-fingerprinting sites run it before any consent interaction. After a bug fix, consent verification rose from 3 confirmed choices to 36 verified rejections and 44 verified acceptances. A verified rejection removed third parties (median 22 against 63 domains after acceptance, on 30 sites) and third-party cookies (1 against 35). It barely reduced harmful fingerprinting: 15 of 16 pairs survived, −6.3 points against the repeat baseline (95% CI −23.1 to 0). 8 of 36 sites kept it after a verified "no". uBlock Origin Lite removed harmful fingerprinting almost entirely (−88 points, CI −100 to −73) but security fingerprinting only partly (−20 points). Counting all fingerprinting together would have hidden all of this.

## 1. Introduction

Browser fingerprinting reads stable properties of the browser and device, such as canvas rendering, fonts, audio processing, WebGL and navigator attributes. Combined, they can recognise a browser without cookies. Detection tools since Englehardt and Narayanan [1] flag scripts that call these APIs in characteristic ways, and FP-Inspector [3] learns the patterns. A detection, however, does not say why the script runs.

The same canvas readback is used by an ad-tech vendor to re-identify a visitor and by a bot-management vendor to decide whether the visitor is a human. FP-Redemption [9] and Senol et al. [10] show that much fingerprinting serves web security, and that the same script can serve both purposes. European law treats the two differently. Fingerprinting falls under Art. 5(3) of the ePrivacy Directive [16], so tracking needs consent, while what is strictly necessary for a service the user requested, which can include its security, may be exempt. A measurement that counts all fingerprinting together cannot say whether a site that keeps fingerprinting after "reject all" is breaking the rules or protecting its login page.

We ask:

We ask:

- **RQ1. Can harmful fingerprinting be told apart from useful fingerprinting, and how much of each is there?** Which share of detected fingerprinting serves tracking (advertising, analytics), which serves the site itself (security, functionality), and which cannot be decided? How much is served by vendors hidden behind first-party URLs?
- **RQ2. Does rejecting consent stop harmful fingerprinting?** With the stored consent state verified, does a rejection remove tracking fingerprinting, compared with its effect on cookies and third-party contacts?
- **RQ3. Do content blockers stop harmful fingerprinting, and what do they remove besides?** Which tracking and which security fingerprinting survives uBlock Origin Lite and Privacy Badger?

Contributions:

1. **A harm classification for fingerprinting scripts.** Purpose comes from vendor signatures, Tracker Radar, page context, path clusters and vendor cookies, with a confidence level and an evidence trail. Tracking capability comes from identifying power, cross-site reach, observed transmission of the browser's own values, and storage writes. Each script gets a class: Tracking, Dual-use, Service, or Undetermined. The method is validated by agreement between independent sources and by behavioural checks, and comes with a stratified 80-script manual-validation protocol.
2. **Two-source consent verification.** It combines the live CMP API with offline decoding of stored IAB TCF, OneTrust and Cookiebot cookies. It fixes two verifier errors that had turned the consent question into a negative result.
3. **A 2026 EU replication of Papadogiannakis et al. [8] with verified consent state and purpose labels.** We find that consent and blockers act on different fingerprinting populations.

## 2. Related Work

**Detecting fingerprinting.** Englehardt and Narayanan crawled one million sites and introduced API-pattern rules for canvas, font, WebRTC and audio fingerprinting [1]. Acar et al. measured canvas fingerprinting and respawning [2]. FP-Inspector [3] learns fingerprinting behaviour from static and dynamic features and finds substantially more fingerprinting scripts than hand-written rules. We keep the transparent rules of [1], adapted as in [3], and add manual validation (Section 3.7) rather than a classifier. FP-tracer [11] follows attribute values from source to network sink. FPTrace [12] shows causally that fingerprints are used for ad targeting by changing the fingerprint and watching ad bids. Both answer "is it used for tracking" more directly than we can, and Section 6 explains how our pipeline would extend towards them.

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

### 3.4 Harmful versus useful fingerprinting

We call fingerprinting **harmful** when its purpose is to recognise the user for someone other than the service the user asked for: advertising, ad measurement, analytics or profiling. Fingerprinting is **useful** when it serves the requested service itself: bot and fraud defence, or functionality. This follows the line drawn by Art. 5(3) of the ePrivacy Directive and the EDPB's guidelines on it [16]. Access to device information needs consent unless it is strictly necessary for the service, and security can be part of that service. It also follows Senol et al.'s dual-use framing [10].

Purpose alone does not say how much harm a script can do, so we also measure its **tracking capability**:

- **Identifying power.** High means a strong technique, or three or more techniques combined, since weak signals become identifying in combination. Medium means two weak techniques, and Low means one.
- **Cross-site reach.** A third party, not owned by the site's owner, is embedded on at least 2 sites in our crawl or in Tracker Radar's regional crawl, so it can link visits across sites.
- **Attribute transmission.** A request from the same site to the script's domain, or to another domain of the same owner, carries a browser value that only API reads expose. We use the configured time zone `Europe/Amsterdam` and the `X11; Linux x86_64` platform string, matched raw, URL-encoded once or twice, `+`-encoded, and inside base64 at every byte offset. Request bodies were not captured, so this is a lower bound.
- **Storage writes.** The script writes cookies or `localStorage`, which can hold an identifier.

Capability is High with three or more of these points (identifying power counts 0–2, each other ingredient 1), Medium with two, and Low otherwise. The harm class (`purpose.harm_class`) follows from purpose and capability:

| Harm class | Rule | Interpretation |
|---|---|---|
| **Tracking** (harmful) | primary purpose Advertising or Analytics | the study's focus |
| **Dual-use** | an advertising purpose with a secondary security purpose (ad verification such as IAS), or the reverse | harmful or useful depending on view |
| **Service** (useful) | primary purpose Anti-fraud/security or Functional | arguably strictly necessary |
| **Undetermined, high capability** | purpose Unknown or CDN, capability High | could track; purpose unproven |
| **Undetermined, low capability** | purpose Unknown or CDN, capability Low or Medium | |

We report every harmful-fingerprinting result twice: for **Tracking** (main estimate), and for an **upper bound** that adds Dual-use and Undetermined scripts with high capability. Purpose decides the class, and capability only ranks scripts within a class and settles scripts of unknown purpose. A security vendor with high capability is still Service. This keeps the classification tied to purpose, which the law uses, rather than to technique, which is the same in both cases.

### 3.5 Consent verification from two sources

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

### 3.6 Comparisons and uncertainty

We compare each condition with the baseline on sites with usable measurements in the condition, the baseline and the repeat baseline. For the consent conditions, we further require a verified choice. A baseline pair **survives** when it is seen again in the condition's post-consent phases. We report the condition's survival minus the repeat baseline's survival, with 95% percentile intervals from 2,000 site-level bootstrap resamples (`scripts/paired_uncertainty.py`). We do this overall, per purpose group (Security, Ads/analytics, Other/unknown) and per harm class (Tracking, the Tracking upper bound, Service, Undetermined).

### 3.7 Validation of the labels

Without ground truth, we use four checks:

1. agreement between two independent label sources, vendor signatures and Tracker Radar
2. convergent validity: security scripts should probe for automation more often than ad scripts
3. the composition of techniques by purpose
4. a stratified manual sample of 80 scripts, by strongest technique and party, for two independent annotators (`docs/annotation_guide.md`), scored with Cohen's kappa and precision per technique and per purpose (`scripts/purpose_agreement.py`)

## 4. Results

### 4.1 Coverage

The baseline was usable on 93 of 149 sites (62%): news 42/50, e-commerce 22/50, social 22/37 and affiliate 7/12. Of the 56 failures, 51 were HTTP 401, 403 or 429 responses. Section 4.2 shows that the sites we reached run bot management heavily. The failures are therefore probably bot blocking, and the excluded sites are probably the ones that fingerprint most.

### 4.2 RQ1: harmful versus useful fingerprinting

64 of 93 usable sites ran at least one fingerprinting-pattern script in the baseline, with 237 (site, script domain, purpose, technique) pairs. The purpose labels cover 74.7% of pairs and 87.8% of strong-technique pairs. Of the labelled pairs, 95 are High confidence, 64 Medium and 18 Low.

| Harm class (baseline) | Pairs | Strong-technique pairs | Sites |
|---|---:|---:|---:|
| **Tracking** (harmful) | **26 (11%)** | **3** | **17** |
| Tracking, upper bound (+ dual-use, + undetermined with high capability) | 45 (19%) | 12 | 23 |
| Service (useful: security, functional) | 151 (64%) | 76 | 43 |
| Undetermined, high capability | 19 (8%) | 9 | 6 |
| Undetermined, low capability | 41 (17%) | 2 | 23 |

Most fingerprinting in this sample is useful, not harmful. Harmful fingerprinting is a minority, and it is mostly attribute collection rather than strong fingerprinting:

- **Vendors.** Across all runs, the 57 tracking scripts (on 32 sites) belong to:
  - ad-recovery networks (`html-load.*` and their first-party fallbacks)
  - the X Ads pixel
  - AppsFlyer, Wunderkind, Prebid, Microsoft Clarity, Adobe tags and HubSpot
  - Quantum Metric, InsurAds, Stay22, GumGum, AnyClip and Mantis
- **Capability.** 49 of the 57 can link visits across sites, 44 write cookies or storage, and 22 have high capability.
- **Values sent home.** The X Ads pixel sent the browser's time zone and platform string to X's servers on 9 sites. GumGum and AnyClip did so on dictionary.com, and the ad-recovery fallback on bild.de.
- **Undetermined, high capability.** These are first-party platform bundles that use strong techniques: Amazon, Best Buy, LinkedIn, Nordstrom, Pinterest, Temu and X. There are also two unattributed third parties, `toadmash.net` on thesun.co.uk and `lyfnh.io` on cdiscount.com. Their purpose cannot be shown from our data. They are the reason for the upper bound.

The purpose breakdown behind these classes:

| Purpose | Pairs | Strong-technique pairs | Sites |
|---|---:|---:|---:|
| Anti-fraud/security | 149 (63%) | 76 | 42 |
| Advertising + analytics | 26 (11%) | 3 | 17 |
| Unknown, functional | 62 (26%) | 11 | 28 |

- **Strong techniques mostly mean security.** 76 of the 90 strong-technique pairs (84%) come from security vendors. Ad-tech and analytics scripts use the weak techniques: attribute enumeration (IAS, Prebid, GumGum, the X Ads pixel, AppsFlyer) and the WebGL renderer query.
- **E-commerce fingerprinting is bot and fraud management.** 21 of 22 usable e-commerce sites fingerprint. 20 of them run security fingerprinting, 10 run nothing else, and only one shows an ad/analytics pattern. On news sites, 12 of 42 run security fingerprinting and 11 run ad/analytics fingerprinting.
- **Hidden first-party vendors.** 63 of 237 pairs (27%) come from vendors served through the site's own URLs. Clusters, each corroborated by vendor cookies:
  - Akamai Bot Manager's `/akam/` beacon on 10 sites, plus its random-path sensor script on the same sites
  - Cloudflare Bot Management on 10
  - Kasada on 5: chewy, costco, nike, otto and twitch, with an identical path
  - DataDome `tags.js` on site subdomains (airbnb, elpais, soundcloud)
  - Fastly's bot challenge on 4 (lemonde, newsweek, wikihow, bloomberg)

  Script-domain attribution counts all of these as first-party, and blockers cannot remove them by domain.
- **Harmful fingerprinting before consent.** All 17 sites with tracking fingerprinting ran it before any consent interaction, 6 of them with high capability: the X Ads pixel on honey.com, nypost.com and vice.com, HubSpot on techcrunch.com, an Adobe tag on corriere.it, and the ad-recovery network on buzzfeed.com. Only 2 used a strong technique: HubSpot (canvas and audio) and the Adobe tag (audio). Under ePrivacy Art. 5(3), tracking fingerprinting before consent is the clearest potential violation in this data.

### 4.3 RQ2: does rejecting consent stop harmful fingerprinting?

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
| **Tracking** (harmful) | 9 | 93.8% (15/16) | **−6.3** | [−23.1, 0.0] |
| Tracking, upper bound | 9 | 93.8% | −6.3 | [−23.1, 0.0] |
| Service (useful) | 12 | 86.5% | −2.7 | [−10.3, 0.0] |
| All fingerprinting | 19 | 89.8% | +1.7 | [−6.4, +11.9] |

**Harmful fingerprinting survives a verified "no".** 15 of 16 tracking pairs present at baseline were still there after rejection. After a verified rejection, 8 of 36 sites ran tracking fingerprinting, and 10 under the upper bound:

- the X Ads pixel (vice.com), which also sent the time zone and platform to X
- GumGum and AnyClip (dictionary.com), which also sent attributes, plus Prebid there
- HubSpot (techcrunch.com)
- AppsFlyer (weather.com)
- Adobe tags (elpais.com)
- Mantis (mirror.co.uk)
- the ad-recovery network (huffpost.com, buzzfeed.com, mirror.co.uk)
- under the upper bound, Integral Ad Science ad verification (washingtonpost.com, theverge.com)

Three of these sites had high capability (buzzfeed.com, techcrunch.com, vice.com). Only one used a strong technique: HubSpot, with canvas and audio. The rest is attribute enumeration and the WebGL renderer query. By contrast, 19 of the 21 strong-technique pairs seen after rejection were Service scripts.

Accepting adds harmful fingerprinting on top. On the dual-verified sites there were 20 tracking pairs on 10 sites after acceptance, against 9 pairs on 5 sites after rejection.

We conclude that rejection removes the cookie and third-party layer of tracking but leaves the harmful fingerprinting that runs before consent in place. The fingerprinting that survives a "no" is mostly useful security fingerprinting, which may be exempt as strictly necessary. A harmful minority remains on 8 of 36 sites, and it is not exempt. Its attribute transmission is directly observed for the X Ads pixel, GumGum and AnyClip. For the rest we observe API reads only (Section 6), so we report potential, not demonstrated, violations.

### 4.4 RQ3: do content blockers stop harmful fingerprinting?

| Survival vs repeat baseline (pp, 95% CI) | **Tracking** | Tracking, upper bound | Service | Undetermined | All |
|---|---:|---:|---:|---:|---:|
| uBlock Origin Lite (56 sites) | **−88.0 [−100, −72.7]** | −50.0 [−74.4, −27.7] | −19.7 [−32.7, −7.0] | −17.2 [−33.3, −3.8] | −27.5 [−40.2, −16.2] |
| Privacy Badger (57 sites) | **−40.0 [−64.5, −13.0]** | −34.1 [−56.5, −14.3] | −13.6 [−25.0, −2.6] | −24.1 [−42.1, −7.1] | −19.9 [−31.5, −9.7] |

Yes, for the known trackers. uBlock Origin Lite removed 22 of 25 tracking pairs. Privacy Badger removed 10 of 25, and its learning-based blocking starts from an empty state in each fresh profile. The upper bound is smaller (−50 points), because the high-capability first-party platform bundles are untouched. uBlock also removed 23% of useful security pairs, while keeping 77% of them. Security pairs are 58% of all baseline pairs. The headline "−27.7 points" is therefore an average of two different effects. Blockers do the job of a tracker blocker well against ad-tech, and leave the bulk of strong fingerprinting, which is bot management that is often first-party, in place. Blocking security scripts also has a cost we do not measure: uBlock removed HUMAN, Forter, Riskified, Stripe and DataDome pairs, which protect checkouts.

We also saw circumvention. On nordstrom.com, under both blockers, Forter's script loaded from a CloudFront host, with the same hashed filename as on `forter.com`. On bild.de, mirror.co.uk and tomsguide.com, an ad-recovery network that the baseline served from `html-load.*` reappeared as extension-less UUID paths on the site's own domain, mostly in the Privacy Badger run. That run also produced 10 new ad/analytics pairs.

### 4.5 Validity of the purpose labels

- **Two independent sources agree moderately.** On the 12 third-party domains with both a vendor signature and a Tracker Radar purpose, raw agreement is 67% and Cohen's κ = 0.49. The four disagreements are secondary-purpose differences:
  - Stripe: fraud signals against online payment
  - Cloudflare: Turnstile against analytics
  - Adobe and HubSpot: analytics against advertising

  Tracker Radar alone could label only 18 of 59 third-party script domains: 54 were in Tracker Radar, but most had no usage category. Signatures and context are needed.
- **Behaviour agrees with the labels.** Using only labels that rest on vendor, Tracker Radar or cookie evidence, and not on behaviour, 94% of security scripts read `navigator.webdriver` (132/141, 95% CI 88–97%), against 68% of ad/analytics scripts (44/65, CI 56–78%). Fisher's exact p < 10⁻⁵. 79% of security scripts use a strong technique, against 12% of ad/analytics scripts.
- **Tracker Radar's fingerprinting score is not a purpose signal.** Mean scores are 2.35 for advertising, 2.71 for analytics and 1.76 for security, which confirms that the score measures API use, not intent.
- **Manual validation is pending.** The 80-script stratified sample is generated. Detector precision per technique and label precision per purpose will be reported from two annotators' independent labels. The rule labels in the sample are 39 security, 29 unknown, 7 advertising, 4 analytics and 1 functional.

## 5. Discussion: separating harmful from useful fingerprinting

Our answer to the professor's question is that **harm is defined by purpose, and purpose can be established for three quarters of the detections. The rest is bounded, not guessed.**

- **Vendor identity is the strongest signal.** It is recoverable even for first-party URLs, through path templates and vendor cookies.
- **Tracker Radar gives an independent but sparse second opinion.**
- **Context and behaviour are weak alone,** but they agree with the vendor labels. Bot managers run on every page and probe for automation, while ad-tech enumerates attributes and sends them home.
- **Capability adds what purpose cannot.** It ranks harmful scripts by how much tracking they can do, and it sizes the uncertain remainder into the upper bound.

Focusing on harmful fingerprinting changes the study's conclusions. Counting all fingerprinting, we would have reported that rejection "does not stop fingerprinting" and that uBlock "reduces it by 28 points". Restricted to harmful fingerprinting:

- Harmful fingerprinting is a minority (11–19% of pairs) and runs before consent on every site that has it.
- A verified rejection leaves 15 of 16 of its pairs in place.
- uBlock Origin Lite removes 88% of it, together with a fifth of the useful security fingerprinting.

The useful majority is mostly first-party bot management. Neither consent nor filter lists should be expected to remove it, and removing it would cost security.

## 6. Limitations and validity

1. **An API read is not proof of tracking.** Attribute transmission is observed only in request URLs, because request bodies were not captured. It is a lower bound: 12 of 57 tracking scripts are confirmed to send the browser's values. We do not observe whether the values feed an identifier. FP-tracer [11] (taint tracking) and FPTrace [12] (ad-bid changes) close this gap. Capturing request bodies is a cheap next step.
2. **Harm classes are estimates.** 25% of baseline pairs have no supported purpose, mostly inline WebGL checks, platform bundles (YouTube, Meta, X, Temu) and rotating, unattributed domains. The upper bound covers the high-capability part. Manual validation is pending. The signature list was built by inspecting this batch, so it may fit this batch better than new data. Security vendors that pool device data across their customers are classed as Service by purpose, even though their capability is high.
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

Harmful and useful fingerprinting can be separated by purpose, with tracking capability as a second axis and an explicit bound for what stays undetermined.

In a 149-site EU crawl, most fingerprinting, and almost all strong fingerprinting, is useful bot and fraud management, often served from the site's own URLs. Harmful (tracking) fingerprinting accounts for 11–19% of detections on 17–23 sites, and it is mostly ad-tech attribute collection. It runs before consent on every site that has it. It survives a verified rejection on 8 of 36 sites (15 of 16 pairs), and some of it sends the browser's attributes to the vendor. uBlock Origin Lite removes it almost entirely.

The next step is to capture request bodies and show which fingerprints feed an identifier.

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
