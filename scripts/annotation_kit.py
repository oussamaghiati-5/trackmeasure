#!/usr/bin/env python3
"""Give each annotator their own sheet, then merge the answers for purpose_agreement.py.

    python scripts/annotation_kit.py split results/full_eu_20261125
    # annotator A fills validation/kit/annotateur_A.csv, annotator B annotateur_B.csv
    python scripts/annotation_kit.py merge results/full_eu_20261125
    # both fill adjudicated_* in validation/kit/desaccords.csv together, then merge again
    python scripts/purpose_agreement.py results/full_eu_20261125/validation/annotation_sheet.csv \
        results/full_eu_20261125/validation/annotation_key.csv

One shared sheet lets each annotator see the other's answers, which breaks the independence
that Cohen's kappa assumes. The kit files hold only the script to judge and the annotator's
own empty columns. They use ';' and a UTF-8 BOM so that Excel with French settings opens them
in columns; Google Sheets and LibreOffice read them too. They contain script URLs: share them
privately, never commit them (results/ is ignored).
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

SHOWN = ["sample_id", "site", "script_domain", "example_url", "techniques", "page_context", "stratum"]
ANSWERS = ["is_fp", "purpose", "notes"]
IS_FP = {"yes", "no", "unsure"}
PURPOSES = {"Advertising", "Analytics", "Anti-fraud/security", "Functional", "CDN", "Unknown"}
README = """# Kit d'annotation — {batch}

Deux personnes, **chacune seule**. Ne regardez ni la feuille de l'autre ni `annotation_key.csv`
(ce sont les étiquettes de nos règles : les voir fausserait la mesure).

1. Ouvrez votre fichier (`annotateur_A.csv` ou `annotateur_B.csv`) dans Excel, LibreOffice ou Google Sheets.
2. Pour chaque ligne, ouvrez `example_url` dans un navigateur normal (pas le crawler) et lisez le script
   (« pretty print » dans les outils de développement). Regardez aussi le site et la page du fournisseur.
3. Remplissez trois colonnes :
   - `is_fp` : `yes` si le script lit les API de la colonne `techniques` **et** combine les valeurs en
     quelque chose d'envoyé ou stocké (hachage, long payload encodé, POST `sensor_data`, cookie identifiant) ;
     `no` si l'usage est fonctionnel (test WebGL avant de dessiner, police avant d'afficher, lecteur vidéo) ;
     `unsure` si le code est trop obscurci.
   - `purpose` : exactement une valeur parmi `Advertising`, `Analytics`, `Anti-fraud/security`,
     `Functional`, `CDN`, `Unknown`. Mieux vaut `Unknown` qu'une supposition.
   - `notes` : la preuve en quelques mots (« Akamai sensor_data POST », « IAS ad verification »).
4. Enregistrez au même format (CSV, séparateur `;`) et renvoyez le fichier.

Ensuite : `python scripts/annotation_kit.py merge results/{batch}` produit `desaccords.csv`.
Discutez ensemble chaque désaccord et remplissez `adjudicated_is_fp` et `adjudicated_purpose`,
puis relancez `merge` et `python scripts/purpose_agreement.py ...` (voir docs/annotation_guide.md).
Durée indicative : 3 à 4 heures par personne pour 80 scripts.
"""


def read(path):
    return pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig", dtype=str).fillna("")


def write(frame, path):
    frame.to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def split(results):
    validation = Path(results) / "validation"
    sheet = read(validation / "annotation_sheet.csv")
    kit = validation / "kit"
    kit.mkdir(exist_ok=True)
    for who in ("A", "B"):
        path = kit / f"annotateur_{who}.csv"
        if path.exists():
            sys.exit(f"{path} exists; delete it first if you really want a blank sheet")
        write(sheet[SHOWN].assign(**{c: "" for c in ANSWERS}), path)
    (kit / "LISEZMOI.md").write_text(README.format(batch=Path(results).name), encoding="utf-8")
    return kit


def check(frame, who):
    errors = []
    for row in frame.itertuples():
        if row.is_fp and row.is_fp.strip().lower() not in IS_FP:
            errors.append(f"{who} {row.sample_id}: is_fp must be yes, no or unsure, not {row.is_fp!r}")
        if row.purpose and row.purpose.strip() not in PURPOSES:
            errors.append(f"{who} {row.sample_id}: purpose {row.purpose!r} is not one of {sorted(PURPOSES)}")
    return errors


def merge(results):
    validation = Path(results) / "validation"
    kit = validation / "kit"
    sheet = read(validation / "annotation_sheet.csv").set_index("sample_id")
    errors = []
    for who, prefix in (("A", "a1"), ("B", "a2")):
        answers = read(kit / f"annotateur_{who}.csv")
        errors += check(answers, who)
        answers = answers.set_index("sample_id")
        if set(answers.index) != set(sheet.index):
            errors.append(f"{who}: sample IDs differ from annotation_sheet.csv")
            continue
        sheet[f"{prefix}_is_fp"] = answers.is_fp.str.strip().str.lower()
        sheet[f"{prefix}_purpose"] = answers.purpose.str.strip()
        sheet[f"{prefix}_notes"] = answers.notes
    if errors:
        sys.exit("Fix these answers first:\n  " + "\n  ".join(errors))
    disputes = kit / "desaccords.csv"
    if disputes.exists():
        decided = read(disputes).set_index("sample_id")
        for column in ("adjudicated_is_fp", "adjudicated_purpose"):
            sheet.loc[decided.index, column] = decided[column].str.strip()
    differ = (sheet.a1_is_fp != sheet.a2_is_fp) | (sheet.a1_purpose != sheet.a2_purpose)
    if not disputes.exists():
        table = sheet[differ].reset_index()[SHOWN + ["a1_is_fp", "a2_is_fp", "a1_purpose", "a2_purpose",
                                                    "a1_notes", "a2_notes"]]
        write(table.assign(adjudicated_is_fp="", adjudicated_purpose=""), disputes)
    sheet.reset_index().to_csv(validation / "annotation_sheet.csv", index=False)
    return int(differ.sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("split", "merge"))
    ap.add_argument("results", help="results/<batch>")
    a = ap.parse_args()
    if a.action == "split":
        print(f"Kit written to {split(a.results)}")
    else:
        print(f"Merged; {merge(a.results)} rows disagree (see validation/kit/desaccords.csv)")


if __name__ == "__main__":
    main()
