# Final consent-eligible experiment list

Frozen on 2026-11-24. The final list is `sites_final_experiment.csv`.

## Provenance

- Candidate source: Hunter's public Cookiebot technology list, retrieved 2026-11-24: <https://hunter.io/technologies/cookiebot>
- Expanded candidate source: TidyTools' public Tranco-derived Cookiebot census, retrieved 2026-11-24: <https://tools.yukai.uk/tech-stack/tech/cookiebot>
- Existing TCF candidates came from the project's earlier consent checks.
- Three additional CMP test sites came from *Opted Out, Yet Tracked* (PoPETs 2024): <https://www.petsymposium.org/popets/2024/popets-2024-0016.pdf>
- Fouad et al.'s *My Cookie is a Phoenix* (PoPETs 2022) informed the broad popularity-list sampling approach. Its paper describes an Alexa Top 30,000 crawl, but its linked Dropbox artifact was unavailable during this freeze, so no site was accepted solely from that historical artifact: <https://petsymposium.org/2022/files/papers/issue3/popets-2022-0063.pdf>
- TCF scope follows the IAB Europe publisher/CMP model: <https://iabeurope.eu/tcf-for-publishers-2/>
- Candidate files: `sites_consent_candidates.csv`, `sites_consent_candidates_supplement.csv`, and `sites_consent_candidates_round2.csv`.

The first two candidate files contain 52 unique sites. The second round added 79 candidates not already in those files. Selection did not inspect or use fingerprinting outcomes.

## Frozen verification rule

A site was included only when all of the following held from the same Netherlands VPN exit:

1. The page loaded successfully.
2. Cookiebot or a loaded GDPR-applicable TCF CMP was observable.
3. Accept was completed, the saved CMP state matched accept after a home-page reload, and GPC was disabled.
4. Reject was completed, the saved CMP state matched reject after a home-page reload, and GPC was enabled.
5. The reject post-reload screenshot showed a usable site page: no block/challenge, pay-or-OK wall, language-only landing page, or second consent prompt.

Screen records:

- `results/site-screen/20261124T212638689443Z/screen.json`
- `results/site-screen/20261124T215521867979Z/screen.json`
- `results/site-screen/20261124T225132134276Z/screen.json`

In round one, the machine gate passed 25 of 52 candidates. Four were removed by the predefined visual/methodological review, leaving 21 sites.

In round two, the machine gate passed 42 of 79 candidates. Ten were removed by the same visual/methodological and destination-domain rules, leaving 32 additions. The frozen final sample therefore contains 53 sites: 51 Cookiebot sites and two TCF sites.

## Visual or methodological exclusions after a machine pass

- `cookiebot.com`: retained as a consent-system control, but excluded from the study sample because it is the CMP vendor rather than an ordinary publisher.
- `bfmtv.com`: a second consent prompt remained after the saved TCF rejection.
- `rtl.fr`: a second consent prompt remained after the saved TCF rejection.
- `dnsbelgium.be`: post-reload screenshot stopped at a language-selection page rather than the target content.

Round-two exclusions after a machine pass:

- `action.com`: post-reload screenshot stopped at a country/language selector.
- Redirected to a different registrable domain: `adscale.de` to `stroeer.de`, `apptentive.com` to `alchemer.com`, `pushd.com` to `auraframes.nl`, `runwayml.com` to `runway.com`, `ktrmr.com` to `kantar.com`, `calldorado.com` to `appvestor.com`, `kumulos.com` to `optimove.com`, `tidio.co` to `tidio.com`, and `salesmanago.pl` to `manago.ai`.

The canonical `runway.com` destination was retained. `alchemer.com` and `kantar.com` were already present in the first-round sample, so their retired aliases were not counted as additional sites.

## Machine-gate exclusions

- Navigation failure: `acm.org`, `globalsign.com`, `weforum.org`.
- Consent state unreadable: `berlingske.dk`, `calameo.com`, `marketingprofs.com`, `tfl.gov.uk`, `timeshighereducation.com`, `tinyjpg.com`, `fliphtml5.com`, `pehub.com`, `pixelio.de`, `pixelio.net`, `rheinhessen.de`.
- Unsupported CMP: `itv.com`, `openstack.org`, `klantenvertellen.nl`, `tidelift.com`.
- Reject action not completed: `circleci.com`, `folkhalsomyndigheten.se`, `mariadb.com`, `thingiverse.com`, `iv.lt`, `serveriai.lt`, `wszystkoociasteczkach.pl`.
- Reject classified as pay-or-OK: `sendpulse.com`, `strava.com`.

The round-two machine gate rejected 37 candidates. This includes the three sites suggested by the 2024 related-work paper: `sammobile.com` failed navigation with HTTP 403, `marca.com` exposed an unreadable consent state, and `foreca.fi` did not complete the accept action. Their presence in earlier work did not override the frozen eligibility rule.

## Scope limitation

This is a purposive, consent-verifiable sample, not a representative sample of the whole web. It contains 51 Cookiebot sites and two TCF sites. Eligibility must still be recorded during the final run because CMPs and websites can change after this freeze date.
