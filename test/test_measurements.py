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

    def request(self, method="POST", body=b"", url="https://t.example/c?tz=Europe%2FBerlin"):
        class Request:      # hashable, like Playwright's: the crawler keys pending requests by object
            pass
        req = Request()
        req.__dict__.update(url=url, method=method, resource_type="xhr", post_data_buffer=body,
                            frame=SimpleNamespace(url="http://example.test/"))
        return req

    def test_request_body_and_long_url_are_kept_up_to_their_caps(self):
        self.state.new_visit("http://example.test/", "landing")
        body = b'{"tz":"Europe/Berlin"}' + b"x" * self.crawler.BODY_MAX
        self.state.on_request(self.request(body=body, url="https://t.example/c?" + "a" * 20000))
        self.state.on_request(self.request(method="GET", body=b"ignored"))
        rows = self.db.execute("SELECT method, length(url), body, body_size FROM requests ORDER BY id").fetchall()
        self.assertEqual(rows[0][1], self.crawler.URL_MAX)
        self.assertTrue(rows[0][2].startswith('{"tz":"Europe/Berlin"}'))
        self.assertEqual(len(rows[0][2]), self.crawler.BODY_MAX)
        self.assertEqual(rows[0][3], len(body))
        self.assertEqual(rows[1], ("GET", len("https://t.example/c?tz=Europe%2FBerlin"), None, None))

    async def test_http_error_on_fresh_subpage_uses_a_spare_and_is_logged(self):
        import random
        self.page.goto.side_effect = [SimpleNamespace(status=404), SimpleNamespace(status=200)]
        after = AsyncMock()
        args = SimpleNamespace(timeout=1, dwell=0, subpages=1)
        measured = await self.crawler.visit_subpages(
            self.page, ["http://example.test/a", "http://example.test/b", "http://example.test/c"],
            False, args, self.state, random.Random(0), after)
        self.assertEqual(measured, 1)
        self.assertEqual(self.db.execute("SELECT url, status FROM visits").fetchall(),
                         [("http://example.test/b", 200)])
        self.assertEqual(self.db.execute("SELECT site, url, error FROM subpage_skips").fetchall(),
                         [("example.test", "http://example.test/a", "subpage http://example.test/a: HTTP 404")])
        after.assert_awaited_once()

    async def test_reused_subpage_timeouts_and_last_candidate_are_not_replaced(self):
        import random
        args = SimpleNamespace(timeout=1, dwell=0, subpages=1)
        cases = [(True, [SimpleNamespace(status=404)]),                      # reused URL
                 (False, RuntimeError("Timeout 1000ms exceeded")),            # no HTTP answer
                 (False, [SimpleNamespace(status=403), SimpleNamespace(status=403)])]  # no spare left
        for reused, outcome in cases:
            self.db.execute("DELETE FROM visits")
            self.page.goto.side_effect = outcome
            with self.assertRaises(RuntimeError):
                await self.crawler.visit_subpages(self.page, ["http://example.test/a", "http://example.test/b"][
                    :1 if reused or isinstance(outcome, Exception) else 2], reused, args, self.state,
                    random.Random(0), AsyncMock())
            self.assertIsNotNone(self.db.execute("SELECT error FROM visits ORDER BY id DESC").fetchone()[0])


class DatabaseSchema(unittest.TestCase):
    @unittest.skipUnless(HAS_BROWSER_DEPS, "tldextract required; available in project container")
    def test_old_database_gains_request_body_columns(self):
        import tempfile
        from pathlib import Path
        from common import open_db
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite"
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE requests(id INTEGER PRIMARY KEY, visit_id INT, url TEXT, host TEXT, "
                        "domain TEXT, third_party INT, rtype TEXT, method TEXT, frame_url TEXT, failure TEXT, ts REAL)")
            con.execute("INSERT INTO requests(url) VALUES ('https://old.example/')")
            con.commit()
            con.close()
            db = open_db(path)
            columns = [row[1] for row in db.execute("PRAGMA table_info(requests)")]
            self.assertEqual(columns[-2:], ["body", "body_size"])
            self.assertEqual(db.execute("SELECT url, body FROM requests").fetchall(), [("https://old.example/", None)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM subpage_skips").fetchone(), (0,))
            db.close()


if __name__ == "__main__":
    unittest.main()
