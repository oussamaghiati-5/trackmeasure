"""Consent evidence and comparison eligibility, including legacy recordings."""
import base64
import json
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import analyze
import consent
import pandas as pd
import test_measurements as measurements


def tcf_state(accepted=False, event="useractioncomplete"):
    return {"tcf": {"cmpStatus": "loaded", "gdprApplies": True, "eventStatus": event,
                    "purposeConsents": {str(i): accepted for i in range(1, 11)},
                    "vendorConsents": {"1": accepted}, "specialFeatureOptins": {"1": False}}}


class ConsentEvidence(unittest.TestCase):
    def test_saved_rejection_and_acceptance_can_be_confirmed(self):
        for action, accepted in (("reject", False), ("accept", True)):
            self.assertEqual(consent.assess_choice(action, True, tcf_state(accepted))[0], "confirmed")

    def test_default_false_flags_are_not_a_completed_rejection(self):
        for event in ("cmpuishown", "tcloaded", None):
            self.assertEqual(consent.assess_choice("reject", True, tcf_state(event=event))[0], "unknown")

    def test_non_applicable_missing_and_incomplete_tcf_are_unknown(self):
        states = [{}, {"tcf": "timeout"}, {"tcf": {}}]
        for key, value in (("gdprApplies", False), ("cmpStatus", "loading"),
                           ("purposeConsents", None), ("vendorConsents", None),
                           ("specialFeatureOptins", None)):
            state = tcf_state()
            state["tcf"][key] = value
            states.append(state)
        for state in states:
            self.assertEqual(consent.assess_choice("reject", True, state)[0], "unknown")

    def test_click_without_state_is_unknown(self):
        self.assertEqual(consent.assess_choice("reject", True, {})[0], "unknown")

    def test_incomplete_action_cannot_be_confirmed_from_defaults(self):
        self.assertEqual(consent.assess_choice("reject", False, tcf_state())[0], "failed")

    def test_opposite_saved_choice_and_special_feature_optin_fail(self):
        self.assertEqual(consent.assess_choice("reject", True, tcf_state(True))[0], "failed")
        state = tcf_state()
        state["tcf"]["specialFeatureOptins"]["1"] = True
        self.assertEqual(consent.assess_choice("reject", True, state)[0], "failed")

    def test_cookiebot_needs_a_response_and_all_optional_category_flags(self):
        cb = dict(hasResponse=True, preferences=False, statistics=False, marketing=False)
        self.assertEqual(consent.assess_choice("reject", True, {"cookiebot": cb})[0], "confirmed")
        cb["hasResponse"] = False
        self.assertEqual(consent.assess_choice("reject", True, {"cookiebot": cb})[0], "unknown")
        cb["hasResponse"] = True
        del cb["statistics"]
        self.assertEqual(consent.assess_choice("reject", True, {"cookiebot": cb})[0], "unknown")

    def test_conflicting_providers_are_unknown(self):
        state = tcf_state()
        state["cookiebot"] = dict(hasResponse=True, preferences=True, statistics=True, marketing=True)
        self.assertEqual(consent.assess_choice("reject", True, state)[0], "unknown")

    def test_legacy_click_malformed_record_and_usp_alone_are_not_confirmation(self):
        for record in (None, "{broken", "[]", json.dumps({"action": "reject", "method": "click"})):
            self.assertEqual(consent.consent_record(record)["outcome"], "unknown")
        self.assertEqual(consent.assess_choice("reject", True, {"usp": "1YYY"})[0], "unknown")


def tc_string(purposes=(), features=(), vendors=(), max_vendor=None, ranges=None):
    """Encode a minimal TCF v2 core segment (fields the decoder does not read are zero)."""
    bits = ["000010", "0" * 134]                         # Version 2, then header fields
    bits.append("".join("1" if i + 1 in features else "0" for i in range(12)))
    bits.append("".join("1" if i + 1 in purposes else "0" for i in range(24)))
    bits.append("0" * 24 + "0" + "0" * 12)              # LI transparency, one-treatment, CC
    max_vendor = max_vendor if max_vendor is not None else max(vendors, default=0)
    bits.append(f"{max_vendor:016b}")
    if ranges is None:
        bits.append("0" + "".join("1" if i + 1 in vendors else "0" for i in range(max_vendor)))
    else:
        bits.append("1" + f"{len(ranges):012b}")
        for start, end in ranges:
            bits.append(("1" + f"{start:016b}" + f"{end:016b}") if end != start else ("0" + f"{start:016b}"))
    s = "".join(bits)
    s += "0" * (-len(s) % 8)
    raw = bytes(int(s[i:i + 8], 2) for i in range(0, len(s), 8))
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class ConsentVerificationFixes(unittest.TestCase):
    """Regression tests for the two verifier bugs found in the EU batch review."""

    def test_empty_purpose_map_after_user_action_is_a_rejection(self):
        state = tcf_state()
        state["tcf"].update(purposeConsents={}, vendorConsents={})
        self.assertEqual(consent.assess_choice("reject", True, state)[0], "confirmed")
        self.assertEqual(consent.assess_choice("accept", True, state)[0], "failed")

    def test_accept_all_with_unasked_global_vendors_false_is_confirmed(self):
        state = tcf_state(True)
        state["tcf"]["purposeConsents"] = {str(i): True for i in range(1, 12)}
        state["tcf"]["vendorConsents"] = {"1": True, "2": False, "3": True, "4": False}
        self.assertEqual(consent.assess_choice("accept", True, state)[0], "confirmed")
        self.assertEqual(consent.assess_choice("reject", True, state)[0], "failed")

    def test_accept_needs_most_purposes_and_some_vendor(self):
        state = tcf_state(True)
        state["tcf"]["purposeConsents"] = {str(i): i <= 5 for i in range(1, 11)}
        self.assertEqual(consent.assess_choice("accept", True, state)[0], "failed")
        state["tcf"]["purposeConsents"] = {str(i): True for i in range(1, 11)}
        state["tcf"]["vendorConsents"] = {"1": False}
        self.assertEqual(consent.assess_choice("accept", True, state)[0], "failed")

    def test_reject_with_a_remaining_vendor_consent_fails(self):
        state = tcf_state()
        state["tcf"]["vendorConsents"] = {"1": False, "2": True}
        self.assertEqual(consent.assess_choice("reject", True, state)[0], "failed")

    def test_reassess_updates_stored_record_and_keeps_crawl_verdict(self):
        state = tcf_state()
        state["tcf"].update(purposeConsents={}, vendorConsents={})
        record = dict(action="reject", action_completed=True, states=[state],
                      outcome="unknown", reason="old rule")
        new = consent.reassess(record)
        self.assertEqual((new["outcome"], new["outcome_at_crawl"]), ("confirmed", "unknown"))
        reload_state = tcf_state(True)
        record["checks"] = [dict(states=[reload_state], phase="home_reload")]
        self.assertEqual(consent.reassess(record)["outcome"], "failed")
        self.assertIn("home_reload", consent.reassess(record)["reason"])
        legacy = dict(action="reject", outcome="confirmed")
        self.assertIs(consent.reassess(legacy), legacy)

    def test_tcf_decoder_bitfield_and_range_encoding(self):
        d = consent.decode_tcf(tc_string(purposes=range(1, 12), vendors=(2, 5, 9)))
        self.assertEqual((d["purposes"], d["vendors"]), (list(range(1, 12)), 3))
        d = consent.decode_tcf(tc_string(purposes=(1, 2), features=(1,), max_vendor=900,
                                         ranges=[(10, 19), (700, 700)]))
        self.assertEqual((d["purposes"], d["special_features"], d["vendors"]), ([1, 2], [1], 11))
        self.assertIsNone(consent.decode_tcf("not-a-tc-string"))
        self.assertEqual(consent.tcf_cookie_verdict(tc_string(max_vendor=50)), "reject")
        self.assertEqual(consent.tcf_cookie_verdict(tc_string(purposes=range(1, 11), vendors=(7,))), "accept")
        self.assertEqual(consent.tcf_cookie_verdict(tc_string(purposes=(1,), vendors=(7,))), "mixed")

    def test_onetrust_and_cookiebot_cookie_verdicts(self):
        rejected = "isGpcEnabled=1&groups=C0001%3A1%2CC0002%3A0%2CC0004%3A0"
        accepted = "groups=C0001%3A1%2CC0002%3A1%2CC0004%3A1"
        self.assertIsNone(consent.onetrust_cookie_verdict(rejected, None))
        self.assertEqual(consent.onetrust_cookie_verdict(rejected, "2026-11-22T10:00:00.000Z"), "reject")
        self.assertEqual(consent.onetrust_cookie_verdict(accepted, "x"), "accept")
        cb = "{stamp:%27x%27%2Cnecessary:true%2Cpreferences:false%2Cstatistics:false%2Cmarketing:false}"
        self.assertEqual(consent.cookiebot_cookie_verdict(cb), "reject")
        self.assertEqual(consent.cookie_verdict({"CookieConsent": cb}), ("reject", ["Cookiebot cookie"]))
        both = {"CookieConsent": cb, "OptanonConsent": accepted, "OptanonAlertBoxClosed": "x"}
        self.assertEqual(consent.cookie_verdict(both)[0], "conflict")

    def test_combining_live_and_cookie_evidence(self):
        mismatch = "Saved CMP choice does not match the requested action"
        cases = [(("unknown", "x", "reject"), "confirmed"),
                 (("confirmed", "x", "reject"), "confirmed"),
                 (("failed", mismatch, "reject"), "unknown"),
                 (("confirmed", "x", "accept"), "unknown"),
                 (("unknown", "x", "mixed"), "failed"),
                 (("unknown", "x", "conflict"), "unknown"),
                 (("unknown", "x", None), "unknown")]
        for (live, reason, verdict), expected in cases:
            self.assertEqual(consent.combine_outcome("reject", live, reason, verdict)[0], expected)


class ConsentFlow(unittest.IsolatedAsyncioTestCase):
    def page(self):
        frame = SimpleNamespace(url="http://news.test/")
        return SimpleNamespace(main_frame=frame, frames=[frame], wait_for_timeout=AsyncMock())

    async def test_two_layer_flow_without_second_button_is_failed(self):
        page = self.page()
        hit = dict(frame=page.main_frame.url, label="Manage settings", _frame=page.main_frame)
        with patch.object(consent, "_eval", AsyncMock(return_value=[])), \
             patch.object(consent, "click_matching", AsyncMock(side_effect=[None, hit, None, None])), \
             patch.object(consent, "read_state", AsyncMock(return_value=tcf_state())):
            result = await consent.apply_consent(page, "reject", wait=1)
        self.assertEqual(result["outcome"], "failed")
        self.assertFalse(result["action_completed"])
        self.assertIsNone(result["detail"][1])
        json.dumps(result)  # no live browser-frame objects in saved evidence

    async def test_api_call_without_confirmation_falls_back_to_a_button(self):
        page = self.page()
        hit = dict(frame=page.main_frame.url, label="Reject all", _frame=page.main_frame)
        with patch.object(consent, "_eval", AsyncMock(return_value=["onetrust"])), \
             patch.object(consent, "click_matching", AsyncMock(return_value=hit)), \
             patch.object(consent, "read_state", AsyncMock(side_effect=[{}, tcf_state()])):
            result = await consent.apply_consent(page, "reject", wait=1)
        self.assertEqual(result["outcome"], "confirmed")
        self.assertEqual([a["method"] for a in result["attempts"]], ["api", "click"])

    async def test_saved_choice_can_be_read_after_reload_but_loss_of_state_is_unknown(self):
        page = self.page()
        result = dict(action="reject", outcome="confirmed")
        with patch.object(consent, "read_state", AsyncMock(return_value=tcf_state(event="tcloaded"))):
            await consent.recheck_consent(page, result)
        self.assertEqual(result["outcome"], "confirmed")
        with patch.object(consent, "read_state", AsyncMock(return_value={})):
            await consent.recheck_consent(page, result, phase="subpage", wait=0)
        self.assertEqual(result["outcome"], "unknown")
        self.assertIn("subpage", result["reason"])
        self.assertEqual(len(result["checks"]), 2)


class ConsentAnalysis(unittest.TestCase):
    def observations(self, record):
        helper = measurements.AnalysisValidity()
        data = helper.observations()
        data["visits"].loc[(data["visits"].run == "blocker")
                           & (data["visits"].phase == "after_consent"), "consent"] = json.dumps(record)
        det = helper.detections()
        return analyze.per_site(data, det), det

    def test_failed_unknown_and_legacy_rejections_keep_metrics_but_cannot_be_compared(self):
        for outcome in ("failed", "unknown", None):
            record = dict(action="reject", method="click", gpc=True)
            if outcome:
                record["outcome"] = outcome
            ps, det = self.observations(record)
            row = ps[ps.run == "blocker"].iloc[0]
            self.assertEqual(row.measurement_ok, 1)
            self.assertEqual(row.gpc, "enabled")
            self.assertEqual(row.comparison_eligible, 0)
            self.assertTrue(analyze.compare(ps, det, "baseline").empty)
            self.assertEqual(len(ps), 2)

    def test_confirmed_rejection_is_compared_and_labelled_with_gpc(self):
        ps, det = self.observations(dict(action="reject", outcome="confirmed", gpc=True))
        comparison = analyze.compare(ps, det, "baseline")
        self.assertEqual(comparison.survival.tolist(), [0.0])
        self.assertEqual(comparison.consent_action.tolist(), ["reject"])
        self.assertEqual(comparison.gpc.tolist(), ["enabled"])

    def test_failed_acceptance_is_also_excluded(self):
        ps, det = self.observations(dict(action="accept", outcome="failed"))
        self.assertTrue(analyze.compare(ps, det, "baseline").empty)

    def test_empty_and_all_unknown_data_export_readable_tables(self):
        for empty in (False, True):
            with self.subTest(empty=empty), tempfile.TemporaryDirectory() as td:
                data = measurements.AnalysisValidity().observations()
                data["visits"]["consent"] = json.dumps(dict(action="reject", outcome="unknown"))
                if empty:
                    data = {key: value.iloc[:0] for key, value in data.items()}
                with patch.object(analyze, "load", return_value=data), \
                     patch.object(sys, "argv", ["analyze.py", "unused.sqlite", "--outdir", td]), \
                     contextlib.redirect_stdout(io.StringIO()):
                    analyze.main()
                self.assertTrue(pd.read_csv(Path(td) / "comparison.csv").empty)
                coverage = pd.read_csv(Path(td) / "consent_coverage.csv")
                self.assertIn("consent_outcome", coverage.columns)
                self.assertEqual(int(coverage.site_runs.sum()), 0 if empty else 2)


if __name__ == "__main__":
    unittest.main()
