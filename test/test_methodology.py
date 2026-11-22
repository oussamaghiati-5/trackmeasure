"""Unit tests for the methodology additions: E&N canvas exclusion, exfiltration and
ID-sharing matching, pay-or-OK classification, behavioural listeners and statistics.

Run from the repository root: python -m unittest discover -s test -p 'test_*.py'
"""
import base64
import hashlib
import importlib.util
import sqlite3
import sys
import time
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import analyze  # noqa: E402
import exfil  # noqa: E402
from consent import classify_pay_or_ok  # noqa: E402

HAS_NUMPY = importlib.util.find_spec("numpy") is not None
CANVAS_CALLS = [("Canvas2D.fillText", {"text": "Cwm fjordbank glyphs"}),
                ("Canvas.toDataURL", {"w": 240, "h": 60, "type": "image/png"})]


class CanvasAnimationExclusion(unittest.TestCase):
    def test_static_readback_is_canvas_fingerprinting(self):
        self.assertIn("canvas", analyze.techniques(CANVAS_CALLS))

    def test_save_restore_or_canvas_listener_excludes_the_script(self):
        for extra in ("Canvas2D.save", "Canvas2D.restore", "Canvas.addEventListener"):
            with self.subTest(extra=extra):
                self.assertNotIn("canvas", analyze.techniques(CANVAS_CALLS + [(extra, {})]))

    def test_new_apis_are_not_counted_as_fingerprinting_calls(self):
        self.assertEqual(analyze.category("Event.mousemove"), "behavior")
        self.assertEqual(analyze.category("Sensor.Accelerometer"), "behavior")
        self.assertEqual(analyze.category("Canvas2D.save"), "other")


CANVAS = "data:image/png;base64," + base64.b64encode(bytes(range(256)) * 4).decode()
RENDERER = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device), SwiftShader driver)"
SCHEMA = """
CREATE TABLE visits(id INTEGER PRIMARY KEY, run TEXT, site TEXT, phase TEXT);
CREATE TABLE requests(visit_id INT, url TEXT, domain TEXT, third_party INT, ts REAL, failure TEXT, post_data TEXT);
CREATE TABLE fp_values(visit_id INT, script_url TEXT, script_domain TEXT, kind TEXT, encoding TEXT, token TEXT, ci INT, ts REAL);
CREATE TABLE cookies(run TEXT, site TEXT, name TEXT, domain TEXT, value TEXT);
"""


class ExfiltrationMatching(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA)
        self.con.execute("INSERT INTO visits VALUES (1,'baseline','s','home_reload')")
        for kind, value in (("canvas", CANVAS), ("webgl_renderer", RENDERER)):
            for enc, tok, ci in exfil.value_tokens(kind, value):
                self.con.execute("INSERT INTO fp_values VALUES (1,'u','tracker.test',?,?,?,?,100)",
                                 (kind, enc, tok, int(ci)))

    def request(self, url, post=None, domain="tracker.test", ts=101, failure=None):
        self.con.execute("INSERT INTO requests VALUES (1,?,?,1,?,?,?)", (url, domain, ts, failure, post))

    def found(self):
        return {(r["kind"], r["encoding"]) for r in exfil.find_exfil(self.con)}

    def test_raw_canvas_in_body_and_url_encoded_renderer(self):
        self.request("http://t/b", post='{"c":"%s"}' % CANVAS)
        self.request("http://t/r?r=" + "ANGLE%20(Google%2C%20Vulkan%201.3.0%20(SwiftShader%20Device)%2C%20SwiftShader%20driver)")
        self.assertIn(("canvas", "base64_slice"), self.found())
        self.assertIn(("webgl_renderer", "raw"), self.found())

    def test_hashed_values_match_case_insensitively(self):
        self.request("http://t/h?c=" + hashlib.sha256(CANVAS.encode()).hexdigest().upper())
        self.request("http://t/m", post="r=" + hashlib.md5(RENDERER.encode()).hexdigest())
        self.assertEqual(self.found(), {("canvas", "sha256"), ("webgl_renderer", "md5")})

    def test_blocked_or_earlier_requests_do_not_count(self):
        self.request("http://t/b", post=CANVAS, failure="net::ERR_BLOCKED_BY_CLIENT")
        self.request("http://t/e", post=CANVAS, ts=50)
        self.assertEqual(self.found(), set())

    def test_short_values_are_not_raw_tokens(self):
        self.assertFalse(any(enc == "raw" for enc, _, _ in exfil.value_tokens("webgl_vendor", "WebKit")))


class IdentifierSharing(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA)
        self.con.execute("INSERT INTO visits VALUES (1,'baseline','s','subpage')")
        owner = lambda d: d.lstrip(".").split(".", 1)[-1] if d.count(".") > 1 else d.lstrip(".")
        self.find = lambda: exfil.find_sharing(self.con, owner=owner)

    def cookie(self, name, value, domain="news.test"):
        self.con.execute("INSERT INTO cookies VALUES ('baseline','s',?,?,?)", (name, domain, value))

    def test_identifier_sent_to_other_party_is_reported(self):
        self.cookie("_trk", "u8p3kv2m9z7q")
        self.con.execute("INSERT INTO requests VALUES (1,'http://cdn.tracker.test/c?id=u8p3kv2m9z7q','tracker.test',1,1,NULL,NULL)")
        self.con.execute("INSERT INTO requests VALUES (1,'http://news.test/x?id=u8p3kv2m9z7q','news.test',0,1,NULL,NULL)")
        self.assertEqual([(r["cookie_name"], r["receiver_domain"]) for r in self.find()], [("_trk", "tracker.test")])

    def test_consent_cookies_timestamps_and_short_values_are_ignored(self):
        self.cookie("euconsent-v2", "CPXxRfAPXxRfAAfKABENB-CgAAAAAAAAAAYgAAAAAAAA")
        self.cookie("ts", str(int(time.time())))
        self.cookie("pref", "dark")
        self.con.execute("INSERT INTO requests VALUES (1,?, 'ads.test',1,1,NULL,NULL)",
                         ("http://ads.test/?c=CPXxRfAPXxRfAAfKABENB-CgAAAAAAAAAAYgAAAAAAAA&t=" + str(int(time.time())),))
        self.assertEqual(self.find(), [])


class PayOrOk(unittest.TestCase):
    def test_guardian_style_wall(self):
        self.assertTrue(classify_pay_or_ok(["Accept all", "Reject all and subscribe"])[0])
        self.assertTrue(classify_pay_or_ok(["Zustimmen und weiter", "Jetzt PUR abonnieren"])[0])

    def test_free_reject_or_no_paid_option_is_not_pay_or_ok(self):
        self.assertFalse(classify_pay_or_ok(["Accept all", "Reject all", "Subscribe"])[0])
        self.assertFalse(classify_pay_or_ok(["Accept all", "Manage options"])[0])
        self.assertFalse(classify_pay_or_ok(["Subscribe for €5/month"])[0])


class BehaviouralListeners(unittest.TestCase):
    def test_families_per_script(self):
        api = pd.DataFrame([dict(run="b", site="s", phase="landing", script_domain="t.test", third_party=1, api=a)
                            for a in ("Event.mousemove", "Event.keydown", "Sensor.Gyroscope", "Canvas.toDataURL")])
        beh = analyze.behavior(api)
        self.assertEqual(beh.iloc[0]["families"], "keyboard,motion,pointer")
        self.assertEqual(beh.iloc[0]["phase_group"], "pre")


@unittest.skipUnless(HAS_NUMPY, "numpy required")
class Statistics(unittest.TestCase):
    def test_sign_test_and_noise_floor_pairing(self):
        import stats
        self.assertEqual(stats.sign_test([0, 0]), 1.0)
        self.assertAlmostEqual(stats.sign_test([-1] * 10), 2 / 2 ** 10)
        cmp = pd.DataFrame([dict(run=r, site=f"s{i}", survival=v, exfil_survival=None)
                            for i in range(8) for r, v in (("baseline2", 0.9), ("ubol", 0.1))])
        row = [r for r in stats.paired(cmp, "survival", "baseline2") if r["run"] == "ubol"][0]
        self.assertEqual(row["paired_sites"], 8)
        self.assertAlmostEqual(row["median_diff_vs_noise"], -0.8)
        self.assertLess(row["sign_test_p"], 0.01)

    def test_validation_precision(self):
        import validation
        lab = pd.DataFrame(dict(technique=["canvas"] * 4, label=["fp", "fp", "not_fp", "unsure"]))
        row = validation.score(lab).iloc[0]
        self.assertAlmostEqual(row["precision"], 2 / 3)
        self.assertEqual(row["unsure"], 1)


if __name__ == "__main__":
    unittest.main()
