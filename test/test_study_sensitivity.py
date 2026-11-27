"""Incident parsing for scripts/study_sensitivity.py."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import study_sensitivity  # noqa: E402


class AcceptRestart(unittest.TestCase):
    def test_collects_unique_sites_after_first_restart(self):
        text = """[accept] 97 sites to crawl (0 already done)
[accept] 1/97 old.example ok (1s)
=== restart ===
[accept] 2 sites to crawl (95 already done)
[accept] 1/2 a.example FAIL x
[accept] 2/2 b.example ok (1s)
=== retry ===
[accept] 1 sites to crawl (96 already done)
[accept] 1/1 a.example ok (1s)
"""
        self.assertEqual(study_sensitivity.accept_retry_sites(text), ["a.example", "b.example"])

    def test_no_restart_is_empty(self):
        self.assertEqual(study_sensitivity.accept_retry_sites(
            "[accept] 1 sites to crawl (0 already done)\n[accept] 1/1 a.example ok (1s)"), [])


if __name__ == "__main__":
    unittest.main()
