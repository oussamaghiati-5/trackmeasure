"""Identifier sharing (scripts/id_sharing.py)."""
import sqlite3
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import id_sharing  # noqa: E402

NOW = 1_795_000_000  # crawl time used by the tests (Nov 2026)


class Tokens(unittest.TestCase):
    def test_identifier_like_parts_are_kept(self):
        tokens = id_sharing.candidate_tokens("GA1.1.1234567890.1795000000", NOW)
        self.assertIn("1234567890", tokens)          # client ID
        self.assertNotIn("1795000000", tokens)       # timestamp of the visit

    def test_short_constant_and_millisecond_timestamps_are_dropped(self):
        self.assertEqual(id_sharing.candidate_tokens("abc", NOW), set())
        self.assertEqual(id_sharing.candidate_tokens("aaaaaaaaaaaaaaa1", NOW), set())   # < 5 distinct chars
        self.assertEqual(id_sharing.candidate_tokens("1795000000123", NOW), set())      # milliseconds
        self.assertEqual(id_sharing.candidate_tokens("en-US-language", NOW), set())     # no digit

    def test_url_encoded_values_are_decoded(self):
        self.assertIn("ab12cd34ef56", id_sharing.candidate_tokens("x%3Dab12cd34ef56", NOW))

    def test_only_single_profile_values_count_and_consent_cookies_are_ignored(self):
        cookies = [
            ("baseline", "a.com", "_ga", ".a.com", "GA1.1.1357924680.1795000000"),
            ("baseline", "b.com", "build", ".b.com", "v20261125abc"),
            ("baseline", "c.com", "build", ".c.com", "v20261125abc"),   # same value on another site
            ("baseline", "a.com", "euconsent-v2", ".a.com", "CQabc123456789xyz"),
            ("baseline", "a.com", "OptanonConsent", ".a.com", "consentId=aa11bb22cc33dd"),
        ]
        ids = id_sharing.identifiers(cookies, NOW)
        self.assertEqual(dict(ids[("baseline", "a.com")]), {"1357924680": [("_ga", "a.com")]})
        self.assertNotIn(("baseline", "b.com"), ids)

    def test_strict_filter_requires_cookie_variation_across_profiles(self):
        cookies = [
            ("baseline", "a.com", "_ga", ".a.com", "GA1.1.1357924680.1795000000"),
            ("baseline2", "a.com", "_ga", ".a.com", "GA1.1.2468135790.1795000000"),
            ("accept", "a.com", "accept_only", ".a.com", "constant12345x"),
        ]
        ids = id_sharing.identifiers(cookies, NOW, require_cookie_variation=True)
        self.assertIn("1357924680", ids[("baseline", "a.com")])
        self.assertIn("2468135790", ids[("baseline2", "a.com")])
        self.assertNotIn(("accept", "a.com"), ids)


class Parties(unittest.TestCase):
    owners = {"doubleclick.net": "Google LLC", "google.com": "Google LLC"}

    def test_same_company_and_brand_domains_are_not_other_parties(self):
        self.assertTrue(id_sharing.same_company("doubleclick.net", "google.com", self.owners))
        self.assertTrue(id_sharing.same_company("tiktok.com", "tiktokcdn-eu.com", {}))
        self.assertFalse(id_sharing.same_company("a.com", "criteo.com", self.owners))

    def test_flows(self):
        self.assertEqual(id_sharing.flow("a.com", "a.com", "x.com"), "first_to_third")
        self.assertEqual(id_sharing.flow("a.com", "x.com", "y.com"), "third_to_third")
        self.assertEqual(id_sharing.flow("a.com", "x.com", "a.com"), "third_to_first")

    def test_exact_mcnemar(self):
        self.assertEqual(id_sharing.exact_mcnemar(0, 0), 1.0)
        self.assertEqual(id_sharing.exact_mcnemar(1, 3), 0.625)
        self.assertAlmostEqual(id_sharing.exact_mcnemar(0, 26), 2.9802322387695312e-08)


class Sharing(unittest.TestCase):
    def db(self):
        con = sqlite3.connect(":memory:")
        con.executescript("""
            CREATE TABLE visits(id INTEGER PRIMARY KEY, run TEXT, site TEXT, phase TEXT, started REAL);
            CREATE TABLE requests(visit_id INT, url TEXT, domain TEXT, failure TEXT, body TEXT);
            CREATE TABLE cookies(run TEXT, site TEXT, name TEXT, domain TEXT, value TEXT);""")
        con.execute("INSERT INTO visits VALUES (1, 'baseline', 'a.com', 'after_consent', ?)", (NOW,))
        con.executemany("INSERT INTO cookies VALUES (?,?,?,?,?)", [
            ("baseline", "a.com", "_ga", ".a.com", "GA1.1.1357924680.1795000000"),
            ("baseline", "a.com", "uid", ".x.com", "u9f8e7d6c5b4")])
        con.executemany("INSERT INTO requests VALUES (?,?,?,?,?)", [
            (1, "https://www.google-analytics.com/g/collect?cid=1357924680.1795000000", "google-analytics.com", None, None),
            (1, "https://y.com/sync", "y.com", None, "partner_uid=u9f8e7d6c5b4"),
            (1, "https://z.com/p?id=u9f8e7d6c5b4", "z.com", "net::ERR_BLOCKED_BY_CLIENT", None),
            (1, "https://a.com/api?cid=1357924680", "a.com", None, None)])   # the owner itself
        return con

    def test_finds_first_party_ids_and_cookie_syncing_but_not_blocked_or_own_requests(self):
        share = id_sharing.find_sharing(self.db(), {}, {"google-analytics.com"})
        got = {(r.cookie_name, r.receiver_domain, r.flow, r.receiver_known_tracker) for r in share.itertuples()}
        self.assertEqual(got, {("_ga", "google-analytics.com", "first_to_third", 1),
                               ("uid", "y.com", "third_to_third", 0)})

    def test_summary_counts_paired_known_tracker_sites(self):
        share = id_sharing.find_sharing(self.db(), {}, {"google-analytics.com"})
        per_site = pd.DataFrame([dict(run=r, site="a.com", comparison_eligible=1, consent_outcome="not_requested")
                                 for r in ("baseline", "baseline2", "ubol")])
        summary = id_sharing.summarize(share, per_site)
        self.assertEqual(summary["by_condition"]["baseline"]["sites_to_known_tracker"], 1)
        paired = summary["paired_known_tracker"]["ubol"]
        self.assertEqual({k: paired[k] for k in ("common_sites", "baseline", "repeat", "condition")},
                         {"common_sites": 1, "baseline": 1, "repeat": 0, "condition": 0})
        self.assertEqual(paired["baseline_vs_condition"], {
            "baseline_only": 1, "condition_only": 0, "exact_mcnemar_p_two_sided": 1.0})


if __name__ == "__main__":
    unittest.main()
