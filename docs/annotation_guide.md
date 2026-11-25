# Manual validation of fingerprinting detections and purpose labels

This is the last validation step. It measures how often the detector is right (precision per technique) and how often the purpose rules are right (precision per purpose). It also measures how well two people agree (Cohen's kappa).

## Material

- `results/<batch>/validation/annotation_sheet.csv` lists 80 scripts, stratified by strongest technique × party (first-party, third-party, hidden first-party vendor). It is private because it contains script URLs.
- `results/<batch>/validation/annotation_key.csv` holds the rule labels. **Do not open it until both annotators have finished.**
- `make purpose` regenerates both files. The fixed seed gives the same sample each time.

## Separate sheets for each annotator

Use one file per person, so neither can see the other's answers:
1. Create the two sheets:
   ```
   python scripts/annotation_kit.py split results/<batch>
   ```
   This writes `validation/kit/annotateur_A.csv` and `annotateur_B.csv`, plus `LISEZMOI.md`, the instructions in French.
2. Send each person their own file, privately. The files contain script URLs.
3. When both are filled, merge them:
   ```
   python scripts/annotation_kit.py merge results/<batch>
   ```
   This copies the answers into `annotation_sheet.csv` and writes `kit/desaccords.csv`.
4. Settle each disagreement together in `desaccords.csv`, then run `merge` again.

The files use `;` and a UTF-8 BOM, so French Excel opens them in columns.

## Procedure

1. Two team members work independently and fill only their own columns (`a1_*` or `a2_*`).
2. For each row, open `example_url` in a normal browser, outside the crawler, and look at the script. Use the browser's pretty-print. Also look at the site, the vendor's website and public documentation of the domain, such as DuckDuckGo Tracker Radar, the vendor's product page or a filter-list comment.
3. Answer two questions:
   - **`is_fp`: is this script fingerprinting the browser?** Answer `yes` if it reads the API pattern listed in `techniques` *and* combines the values into something sent away or stored. Typical signs are hashing, a long encoded payload, a `sensor_data`-style POST, or an identifier cookie. Answer `no` if the API use is functional, such as a WebGL capability check before drawing, a font check before rendering text, or a video player probing codecs. Answer `unsure` if the code is too obfuscated to decide.
   - **`purpose`: why does it run?** Use exactly one of `Advertising`, `Analytics`, `Anti-fraud/security`, `Functional`, `CDN`, `Unknown`.
     - `Anti-fraud/security`: bot management, CAPTCHA, payment-fraud checks, VPN/proxy detection.
     - `Advertising`: ad delivery, bidding, ad verification, ad recovery or anti-adblock, affiliate tracking.
     - `Analytics`: audience measurement, session replay, marketing attribution.
     - `Functional`: the site needs it to work and it is not security.
     - `CDN`: only a hosting domain, and the purpose of the code cannot be told.
     - `Unknown`: use it freely, because a forced guess is worse than `Unknown`.
4. Use `notes` for the evidence, for example "Akamai sensor_data POST" or "IAS ad-verification library".
5. Afterwards, discuss every row where you disagree and fill `adjudicated_is_fp` and `adjudicated_purpose` together.
6. Run `make agreement`. It writes `validation/agreement.json` with:
   - Cohen's kappa for both questions. Above 0.6 is substantial agreement, above 0.8 is almost perfect.
   - Detector precision per technique: the share of sampled detections judged `yes`.
   - Rule precision per purpose and a confusion matrix of rule labels against the adjudicated labels.

## What to report

Report kappa, precision per technique and precision per purpose, each with its *n*. Recall cannot be estimated, because the sample contains only scripts the detector flagged. Say so in the paper. If precision for a purpose is low, fix the rule in `purpose.py`, re-run `make purpose`, and report both the before and after numbers.
