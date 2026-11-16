"""Fast tests of cross-frame consent evidence; no browser or external services."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import consent


def tcf(accepted=False, event="useractioncomplete"):
    return {"tcf": {"cmpStatus": "loaded", "gdprApplies": True, "eventStatus": event,
                    "purposeConsents": {"1": accepted, "3": accepted},
                    "vendorConsents": {"1": accepted}, "specialFeatureOptins": {"1": False}}}


def cb(accepted=False):
    return {"cookiebot": {"hasResponse": True, "preferences": accepted,
                          "statistics": accepted, "marketing": accepted}}


def page():
    main = SimpleNamespace(url="https://publisher.example/")
    child = SimpleNamespace(url="https://cmp.example/banner")
    return SimpleNamespace(main_frame=main, frames=[main, child],
                           wait_for_timeout=AsyncMock(), url=main.url, screenshot=AsyncMock())


class AcrossFrames(unittest.TestCase):
    def test_supported_state_can_be_in_parent_or_child(self):
        for states in ([tcf(), {}], [{}, cb()], [tcf(), cb()]):
            self.assertEqual(consent.assess_choice("reject", True, {"states": states})[0], "confirmed")

    def test_conflict_is_not_hidden_by_a_confirming_frame(self):
        conflict = tcf()
        conflict.update(cb(True))
        for states in ([tcf(), cb(True)], [tcf(), tcf(True)], [conflict, cb()]):
            self.assertEqual(consent.assess_choice("reject", True, {"states": states})[0], "unknown")

    def test_defaults_missing_state_and_unfinished_action_still_cannot_confirm(self):
        for states in ([{}, {}], [tcf(event="tcloaded"), {}], [cb() | {"cookiebot": None}]):
            self.assertEqual(consent.assess_choice("reject", True, {"states": states})[0], "unknown")
        self.assertEqual(consent.assess_choice("reject", False, {"states": [tcf()]})[0], "failed")

    def test_manage_or_reject_is_a_generic_control_not_an_arbitrary_link(self):
        for label in ("Manage or reject", "Manage preferences", "Gérer les cookies", "Einstellungen"):
            self.assertIsNotNone(consent.MANAGE.fullmatch(label))
        for label in ("Manage account", "Subscribe to reject cookies", "Reject and subscribe"):
            self.assertIsNone(consent.MANAGE.fullmatch(label))


class FrameFlow(unittest.IsolatedAsyncioTestCase):
    async def test_clicking_a_removed_iframe_reads_the_remaining_parent_state(self):
        p = page()
        child = p.frames[1]

        async def click(*_):
            p.frames = [p.main_frame]  # The banner destroys its own frame.
            return dict(frame=child.url, label="Tout refuser", _frame=child)

        with patch.object(consent, "_eval", AsyncMock(return_value=[])), \
             patch.object(consent, "click_matching", AsyncMock(side_effect=click)), \
             patch.object(consent, "read_state", AsyncMock(return_value=tcf())) as read:
            result = await consent.apply_consent(p, "reject", wait=1)
        self.assertEqual(result["outcome"], "confirmed")
        self.assertIs(read.await_args.args[0], p.main_frame)
        self.assertEqual(result["detail"]["frame"], child.url)
        json.dumps(result)

    async def test_cmp_api_inside_child_frame_is_attempted(self):
        p = page()

        async def api(frame, js):
            return ["cookiebot"] if frame is p.frames[1] else []

        async def state(frame):
            return cb() if frame is p.frames[1] else {}

        with patch.object(consent, "_eval", AsyncMock(side_effect=api)), \
             patch.object(consent, "read_state", AsyncMock(side_effect=state)), \
             patch.object(consent, "click_matching", AsyncMock()) as click:
            result = await consent.apply_consent(p, "reject", wait=1)
        self.assertEqual(result["outcome"], "confirmed")
        self.assertEqual(result["attempts"][0]["frame"], p.frames[1].url)
        click.assert_not_awaited()

    async def test_late_loaded_cmp_api_is_probed_again(self):
        p = page()
        p.frames = [p.main_frame]
        with patch.object(consent, "_eval", AsyncMock(side_effect=[[], ["cookiebot"]])) as api, \
             patch.object(consent, "read_state", AsyncMock(return_value=cb())), \
             patch.object(consent, "click_matching", AsyncMock(return_value=None)):
            result = await consent.apply_consent(p, "reject", wait=1)
        self.assertEqual(result["outcome"], "confirmed")
        self.assertEqual(api.await_count, 2)

    async def test_reload_checks_child_state_and_downgrades_conflict(self):
        p = page()
        result = dict(action="reject", outcome="confirmed")
        with patch.object(consent, "read_state", AsyncMock(side_effect=[{}, cb()])):
            await consent.recheck_consent(p, result)
        self.assertEqual(result["outcome"], "confirmed")
        with patch.object(consent, "read_state", AsyncMock(side_effect=[tcf(event="tcloaded"), cb(True)])):
            await consent.recheck_consent(p, result, phase="subpage")
        self.assertEqual(result["outcome"], "unknown")
        self.assertIn("disagree", result["reason"])

    async def test_reload_waits_for_delayed_state_without_reapplying_consent(self):
        p = page()
        result = dict(action="reject", outcome="confirmed")
        with patch.object(consent, "read_page_state", AsyncMock(side_effect=[{}, cb()])) as read, \
             patch.object(consent, "apply_consent", AsyncMock()) as apply:
            await consent.recheck_consent(p, result, wait=0.01)
        self.assertEqual(result["outcome"], "confirmed")
        self.assertEqual(read.await_count, 2)
        self.assertEqual(result["checks"][0]["polls"], 2)
        apply.assert_not_awaited()

    async def test_missing_state_at_deadline_drops_confirmation(self):
        result = dict(action="reject", outcome="confirmed")
        with patch.object(consent, "read_page_state", AsyncMock(return_value={})):
            await consent.recheck_consent(page(), result, wait=0)
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(result["checks"][0]["outcome"], "unknown")

    async def test_opposite_saved_choice_is_not_waited_away(self):
        result = dict(action="reject", outcome="confirmed")
        with patch.object(consent, "read_page_state", AsyncMock(side_effect=[cb(True), cb()])) as read:
            await consent.recheck_consent(page(), result)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(read.await_count, 1)

    async def test_detached_frame_does_not_erase_readable_parent(self):
        p = page()
        p.frames[1].evaluate = AsyncMock(side_effect=RuntimeError("Frame detached"))
        p.main_frame.evaluate = AsyncMock(side_effect=[tcf()["tcf"], None, None])
        state = await consent.read_page_state(p)
        self.assertEqual(consent.assess_choice("reject", True, state)[0], "confirmed")
        self.assertEqual(len(state["states"]), 2)

    async def test_failed_screenshot_keeps_button_labels_and_failed_outcome(self):
        p = page()
        p.screenshot.side_effect = RuntimeError("Screenshot failed")
        result = dict(action="reject", outcome="failed", reason="No button")
        with tempfile.TemporaryDirectory() as td, \
             patch.object(consent, "_eval", AsyncMock(return_value=[{"label": "Manage or reject"}])):
            await consent.save_diagnostics(p, result, td, "example:reject")
            evidence = json.loads(Path(result["diagnostics"][0]).read_text())
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(evidence["screenshot_error"], "RuntimeError")
        self.assertEqual(len(evidence["frames"]), 2)
        self.assertEqual(evidence["frames"][1]["controls"][0]["label"], "Manage or reject")


if __name__ == "__main__":
    unittest.main()
