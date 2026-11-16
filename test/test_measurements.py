"""Regression checks: a failed page must not look like successful blocking.

Run from the repository root: python -m unittest discover -s test -p 'test_*.py'
"""
import importlib.util
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

import analyze
from navigation import measurement_error, navigation_error


def visit(phase, status=200, error=None, ended=1):
    return dict(phase=phase, status=status, error=error, ended=ended)


def complete_visits():
    return [visit("landing"), visit("after_consent", status=None), visit("home_reload")]


class NavigationValidity(unittest.TestCase):
    def test_successful_documents(self):
        self.assertIsNone(navigation_error(200))
        self.assertIsNone(navigation_error(201))

    def test_http_errors_missing_responses_and_empty_documents(self):
        for status in (None, 204, 205, 301, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                self.assertIsNotNone(navigation_error(status))

    def test_timeout_wins_even_if_a_response_was_seen(self):
        self.assertEqual(navigation_error(200, "Navigation timed out"), "Navigation timed out")

    def test_consent_phase_does_not_need_a_new_http_response(self):
        self.assertIsNone(measurement_error(complete_visits()))

    def test_missing_or_unfinished_phases_are_invalid(self):
        self.assertIn("Missing phases", measurement_error([]))
        self.assertIn("home_reload", measurement_error(complete_visits()[:-1]))
        visits = complete_visits()
        visits[1]["ended"] = None
        self.assertIn("did not finish", measurement_error(visits))

    def test_failure_in_any_navigation_invalidates_the_measurement(self):
        for phase in ("landing", "home_reload", "subpage"):
            with self.subTest(phase=phase):
                visits = [v for v in complete_visits() if v["phase"] != phase]
                visits.append(visit(phase, status=503))
                self.assertEqual(measurement_error(visits), f"{phase}: HTTP 503")


class AnalysisValidity(unittest.TestCase):
    def observations(self, failed_run=None, missing_run=None, execution_failed=None):
        """A baseline tracker and a protection run with zero tracking calls.

        A legacy database can say ok=1 even though navigation actually failed.
        """
        visits, done = [], []
        for run in ("baseline", "blocker"):
            phases = complete_visits()
            if run == failed_run:
                phases[-1]["status"] = 503
            if run == missing_run:
                phases = phases[:-1]
            for v in phases:
                visits.append(dict(v, run=run, site="example.test", consent=json.dumps({"action": "none"})))
            done.append(dict(run=run, site="example.test", ok=int(run != execution_failed),
                             error=None, seconds=1))
        return {
            "visits": pd.DataFrame(visits),
            "done": pd.DataFrame(done),
            "requests": pd.DataFrame(columns=["run", "site", "failure", "third_party", "domain"]),
            "api": pd.DataFrame(columns=["run", "site", "api", "third_party"]),
            "cookies": pd.DataFrame(columns=["run", "site", "third_party"]),
        }

    def detections(self):
        return pd.DataFrame([dict(run="baseline", site="example.test", phase_group="post",
                                  script_domain="tracker.test", script_url="http://tracker.test/fp.js",
                                  third_party=1, technique="canvas")])

    def comparison(self, **outcomes):
        det = self.detections()
        ps = analyze.per_site(self.observations(**outcomes), det)
        return ps, analyze.compare(ps, det, "baseline")

    def test_valid_zero_tracking_is_reported_as_zero_survival(self):
        ps, comparison = self.comparison()
        self.assertEqual(ps.measurement_ok.tolist(), [1, 1])
        self.assertEqual(comparison.survival.tolist(), [0.0])

    def test_legacy_success_flag_cannot_make_failed_blocker_look_effective(self):
        ps, comparison = self.comparison(failed_run="blocker")
        blocker = ps[ps.run == "blocker"].iloc[0]
        self.assertEqual(blocker.ok, 1)  # the old, incorrect completion flag
        self.assertEqual(blocker.measurement_ok, 0)
        self.assertIn("HTTP 503", blocker.measurement_error)
        self.assertTrue(comparison.empty)
        self.assertIn("survival", comparison.columns)  # readable even with no valid pairs

    def test_failed_baseline_is_also_excluded(self):
        _, comparison = self.comparison(failed_run="baseline")
        self.assertTrue(comparison.empty)

    def test_incomplete_run_is_excluded(self):
        _, comparison = self.comparison(missing_run="blocker")
        self.assertTrue(comparison.empty)

    def test_unfinished_visit_with_sql_null_timestamp_is_excluded(self):
        data = self.observations()
        data["visits"].loc[(data["visits"].run == "blocker")
                           & (data["visits"].phase == "home_reload"), "ended"] = float("nan")
        det = self.detections()
        ps = analyze.per_site(data, det)
        self.assertTrue(analyze.compare(ps, det, "baseline").empty)
        self.assertIn("did not finish", ps[ps.run == "blocker"].iloc[0].measurement_error)

    def test_execution_failure_is_excluded_even_when_navigations_finished(self):
        _, comparison = self.comparison(execution_failed="blocker")
        self.assertTrue(comparison.empty)


HAS_BROWSER_DEPS = (importlib.util.find_spec("playwright") is not None
                    and importlib.util.find_spec("tldextract") is not None)


@unittest.skipUnless(HAS_BROWSER_DEPS, "Playwright/tldextract required; available in project container")
class CrawlerNavigation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import crawler
        from common import SCHEMA
        self.crawler = crawler
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(SCHEMA)
        self.state = crawler.SiteState(self.db, "blocker", "example.test", "test", "example.test")
        self.args = SimpleNamespace(timeout=1, dwell=0)
        self.page = SimpleNamespace(url="http://example.test/", goto=AsyncMock(),
                                    mouse=SimpleNamespace(move=AsyncMock(), wheel=AsyncMock()),
                                    wait_for_timeout=AsyncMock())

    async def asyncTearDown(self):
        self.db.close()

    async def test_http_failure_is_saved_before_crawl_is_rejected(self):
        self.page.goto.return_value = SimpleNamespace(status=503)
        with self.assertRaisesRegex(RuntimeError, "landing .*HTTP 503"):
            await self.crawler.navigate(self.page, self.page.url, "landing", self.args, self.state, None)
        status, error, ended = self.db.execute("SELECT status,error,ended FROM visits").fetchone()
        self.assertEqual(status, 503)
        self.assertEqual(error, "HTTP 503")
        self.assertIsNotNone(ended)

    async def test_network_error_is_saved_before_crawl_is_rejected(self):
        self.page.goto.side_effect = RuntimeError("net::ERR_CONNECTION_REFUSED")
        with self.assertRaisesRegex(RuntimeError, "ERR_CONNECTION_REFUSED"):
            await self.crawler.navigate(self.page, self.page.url, "landing", self.args, self.state, None)
        status, error = self.db.execute("SELECT status,error FROM visits").fetchone()
        self.assertIsNone(status)
        self.assertIn("ERR_CONNECTION_REFUSED", error)

    async def test_successful_navigation_is_retained(self):
        import random
        self.page.goto.return_value = SimpleNamespace(status=200)
        url = await self.crawler.navigate(self.page, self.page.url, "landing", self.args,
                                          self.state, random.Random(0))
        self.assertEqual(url, self.page.url)
        self.assertEqual(self.db.execute("SELECT status,error FROM visits").fetchone(), (200, None))


if __name__ == "__main__":
    unittest.main()
