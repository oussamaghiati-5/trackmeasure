"""Isolated-profile cookie re-issue analysis."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import cookie_isolation  # noqa: E402

NOW = 1_795_000_000


class CookieIsolation(unittest.TestCase):
    def test_reissues_are_private_reduced_and_cross_site_constants_are_excluded(self):
        cookies = [
            # Same site, same cookie slot, two isolated profiles: retained.
            ("baseline", "a.com", "uid", ".a.com", "u9f8e7d6c5b4"),
            ("baseline2", "a.com", "uid", ".a.com", "u9f8e7d6c5b4"),
            # Same site but a changed slot: retained, not marked same-slot.
            ("baseline", "a.com", "old", ".a.com", "z1y2x3w4v5u6"),
            ("baseline2", "a.com", "new", ".tracker.com", "z1y2x3w4v5u6"),
            # A site/publisher constant observed on another site: excluded.
            ("baseline", "a.com", "build", ".a.com", "build2026abc9"),
            ("baseline2", "a.com", "build", ".a.com", "build2026abc9"),
            ("baseline", "b.com", "build", ".b.com", "build2026abc9"),
            # Consent values are excluded before tokenisation.
            ("baseline", "a.com", "euconsent-v2", ".a.com", "Consent9Token123"),
            ("baseline2", "a.com", "euconsent-v2", ".a.com", "Consent9Token123"),
        ]
        eligible = {"baseline": {"a.com", "b.com"}, "baseline2": {"a.com", "b.com"}}
        rows, diagnostics, pairs = cookie_isolation.find_candidates(
            cookies, NOW, eligible, (("baseline", "baseline2"),))
        self.assertEqual(pairs, [("baseline", "baseline2")])
        self.assertEqual(len(rows), 1)
        row = rows.iloc[0]
        self.assertEqual(row.site, "a.com")
        self.assertEqual(row.candidate_tokens, 2)
        self.assertEqual(row.same_slot_candidates, 1)
        self.assertEqual(row.two_profile_only_candidates, 2)
        self.assertEqual(set(row.two_profile_only_cookie_names.split(",")), {"new", "old", "uid"})
        self.assertEqual(row.multi_profile_cookie_names, "")
        self.assertNotIn("u9f8e7d6c5b4", ",".join(map(str, row)))
        self.assertEqual(diagnostics["cross_site_tokens_excluded"], 1)
        self.assertEqual(diagnostics["consent_cookie_rows_excluded"], 2)

    def test_ineligible_profiles_and_missing_pairs_do_not_count(self):
        cookies = [
            ("baseline", "a.com", "uid", ".a.com", "u9f8e7d6c5b4"),
            ("baseline2", "a.com", "uid", ".a.com", "u9f8e7d6c5b4"),
        ]
        rows, _, pairs = cookie_isolation.find_candidates(
            cookies, NOW, {"baseline": {"a.com"}, "baseline2": set()},
            (("baseline", "baseline2"),))
        self.assertTrue(rows.empty)
        self.assertEqual(pairs, [])

    def test_summary_uses_common_eligible_denominator(self):
        cookies = [
            ("baseline", "a.com", "uid", ".a.com", "u9f8e7d6c5b4"),
            ("baseline2", "a.com", "uid", ".a.com", "u9f8e7d6c5b4"),
        ]
        eligible = {"baseline": {"a.com", "b.com"}, "baseline2": {"a.com", "b.com"}}
        rows, diagnostics, pairs = cookie_isolation.find_candidates(
            cookies, NOW, eligible, (("baseline", "baseline2"),))
        summary = cookie_isolation.summarize(rows, eligible, pairs, diagnostics)
        repeat = summary["pairs"]["baseline__baseline2"]
        self.assertEqual(repeat["common_eligible_sites"], 2)
        self.assertEqual(repeat["sites_with_candidate"], 1)
        self.assertEqual(repeat["site_rate"], 0.5)

    def test_fingerprinting_context_is_an_overlap_not_an_attribution(self):
        cookies = [
            ("baseline", "a.com", "uid", ".a.com", "u9f8e7d6c5b4"),
            ("baseline2", "a.com", "uid", ".a.com", "u9f8e7d6c5b4"),
        ]
        eligible = {"baseline": {"a.com"}, "baseline2": {"a.com"}}
        rows, _, _ = cookie_isolation.find_candidates(
            cookies, NOW, eligible, (("baseline", "baseline2"),))
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp)
            (results / "fp_scripts.csv").write_text(
                "run,site,phase_group,script_domain,script_url,third_party,technique\n"
                "baseline,a.com,post,a.com,https://a.com/fp.js,0,canvas\n"
                "baseline2,a.com,post,a.com,https://a.com/fp.js,0,canvas\n", encoding="utf-8")
            # The real file uses comma-separated runs inside a quoted CSV field.
            (results / "script_purposes.csv").write_text(
                'site,script_domain,runs,harm_class\n'
                'a.com,a.com,"baseline,baseline2",Tracking\n', encoding="utf-8")
            enriched = cookie_isolation.add_fingerprinting_context(rows, results)
        row = enriched.iloc[0]
        self.assertEqual(row.fp_both_runs, 1)
        self.assertEqual(row.tracking_fp_both_runs, 1)
        self.assertEqual(row.cookie_owner_is_fp_script_domain, 1)


if __name__ == "__main__":
    unittest.main()
