"""Annotation kit: independent sheets and their merge (scripts/annotation_kit.py)."""
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import annotation_kit as kit  # noqa: E402

COLUMNS = ["sample_id", "site", "script_domain", "example_url", "techniques", "page_context", "runs", "stratum",
           "a1_is_fp", "a1_purpose", "a1_notes", "a2_is_fp", "a2_purpose", "a2_notes",
           "adjudicated_is_fp", "adjudicated_purpose"]


class Kit(unittest.TestCase):
    def batch(self, root):
        validation = root / "validation"
        validation.mkdir(parents=True)
        rows = [dict(sample_id=f"S00{i}", site="a.com", script_domain="b.com", example_url="https://b.com/s.js",
                     techniques="canvas", page_context="Home only", runs="baseline", stratum="canvas | Third-party")
                for i in (1, 2)]
        pd.DataFrame(rows, columns=COLUMNS).to_csv(validation / "annotation_sheet.csv", index=False)
        return root

    def fill(self, path, answers):
        sheet = kit.read(path)
        for column, values in answers.items():
            sheet[column] = values
        kit.write(sheet, path)

    def test_each_annotator_sees_only_the_script_and_their_own_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = kit.split(self.batch(Path(tmp)))
            a = kit.read(out / "annotateur_A.csv")
            self.assertEqual(list(a.columns), kit.SHOWN + kit.ANSWERS)
            self.assertTrue((out / "LISEZMOI.md").exists())
            self.assertTrue((out / "annotateur_A.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
            with self.assertRaises(SystemExit):
                kit.split(Path(tmp))          # never overwrite a sheet in progress

    def test_merge_writes_both_annotators_lists_disagreements_then_adjudication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.batch(Path(tmp))
            out = kit.split(root)
            self.fill(out / "annotateur_A.csv", {"is_fp": ["yes", "no"], "purpose": ["Advertising", "Functional"]})
            self.fill(out / "annotateur_B.csv", {"is_fp": ["Yes", "yes"], "purpose": ["Advertising", "Unknown"]})
            self.assertEqual(kit.merge(root), 1)
            sheet = pd.read_csv(root / "validation" / "annotation_sheet.csv", dtype=str)
            self.assertEqual(list(sheet.a2_is_fp), ["yes", "yes"])
            disputes = kit.read(out / "desaccords.csv")
            self.assertEqual(list(disputes.sample_id), ["S002"])
            self.fill(out / "desaccords.csv", {"adjudicated_is_fp": ["no"], "adjudicated_purpose": ["Functional"]})
            kit.merge(root)
            sheet = pd.read_csv(root / "validation" / "annotation_sheet.csv", dtype=str).set_index("sample_id")
            self.assertEqual(sheet.loc["S002", "adjudicated_purpose"], "Functional")

    def test_invalid_answers_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.batch(Path(tmp))
            out = kit.split(root)
            self.fill(out / "annotateur_A.csv", {"is_fp": ["oui", "no"], "purpose": ["Ads", "Functional"]})
            self.fill(out / "annotateur_B.csv", {"is_fp": ["yes", "no"], "purpose": ["Advertising", "Functional"]})
            with self.assertRaises(SystemExit):
                kit.merge(root)


if __name__ == "__main__":
    unittest.main()
