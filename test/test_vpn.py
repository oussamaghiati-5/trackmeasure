"""Fail-closed checks for the host-side VPN preflight orchestration."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import verify_vpn as vpn


class VPNPreflightTests(unittest.TestCase):
    def exercise(self, ready, stopped=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        process = MagicMock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        messages = [ready] + ([stopped] if stopped is not None else [])
        patches = [
            patch.object(vpn, "ROOT", root),
            patch.object(vpn, "run"),
            patch.object(vpn, "spawn", return_value=process),
            patch.object(vpn, "message", side_effect=messages),
            patch.object(vpn, "trace", side_effect=[
                {"ip": "198.51.100.1", "country": "MA"}, ready["exit"]]),
            patch("sys.argv", ["verify_vpn.py", "--country", "NL"]),
            patch("sys.stdout", new_callable=io.StringIO),
        ]
        entered = [p.start() for p in patches]
        for p in reversed(patches):
            self.addCleanup(p.stop)
        return root, entered[1]

    def ready(self, country="NL"):
        return {"exit": {"ip": "203.0.113.2", "country": country},
                "direct_before": {"1.1.1.1": True}}

    def test_remaining_direct_access_fails_and_restores_vpn(self):
        root, run = self.exercise(self.ready(), {
            "direct_after": {"1.1.1.1": True}, "https_blocked": True})
        with self.assertRaisesRegex(RuntimeError, "retained internet access"):
            vpn.main()
        self.assertEqual(run.call_args_list[-1].args[0][-1], "gluetun")
        self.assertIn("up", run.call_args_list[-1].args[0])
        report = json.loads((root / "results/vpn_verification.json").read_text())
        self.assertEqual(report["status"], "failed")

    def test_wrong_country_fails_before_stopping_vpn(self):
        root, run = self.exercise(self.ready("US"))
        with self.assertRaisesRegex(RuntimeError, "Expected NL, observed US"):
            vpn.main()
        self.assertFalse(any("stop" in c.args[0] for c in run.call_args_list))
        self.assertEqual(json.loads((root / "results/vpn_verification.json").read_text())[
            "status"], "failed")

    def test_blocked_and_recovered_connection_passes(self):
        root, run = self.exercise(self.ready(), {
            "direct_after": {"1.1.1.1": False}, "https_blocked": True})
        vpn.main()
        report = json.loads((root / "results/vpn_verification.json").read_text())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["exit"], report["recovery_exit"])
        self.assertTrue(any("stop" in c.args[0] for c in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
