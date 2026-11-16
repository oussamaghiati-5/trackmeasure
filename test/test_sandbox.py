"""Security regressions: sandbox required, overrides and incomplete reports fail."""
import importlib.util
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

HAS_DEPS = (importlib.util.find_spec("playwright") is not None
            and importlib.util.find_spec("tldextract") is not None)
if HAS_DEPS:
    from common import launch
    from scripts.preflight import verify_sandbox


@unittest.skipUnless(HAS_DEPS, "Run inside the project container")
class LauncherSandbox(unittest.IsolatedAsyncioTestCase):
    async def test_shared_launcher_requires_sandbox_with_and_without_extensions(self):
        for extensions in ((), ("test/ext_block",)):
            callbacks = {}
            context = SimpleNamespace(on=Mock(side_effect=lambda event, cb: callbacks.update({event: cb})))
            browser = SimpleNamespace(launch_persistent_context=AsyncMock(return_value=context))
            with tempfile.TemporaryDirectory() as profile:
                await launch(SimpleNamespace(chromium=browser), profile, extensions=extensions)
            if "close" in callbacks:
                callbacks["close"]()
            self.assertIs(browser.launch_persistent_context.call_args.kwargs["chromium_sandbox"], True)

    async def test_user_arguments_cannot_disable_sandbox(self):
        for flag in ("--no-sandbox", "--no-sandbox=true", "--disable-seccomp-filter-sandbox",
                     "--disable-namespace-sandbox", "--disable-gpu-sandbox", "--single-process"):
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "Sandbox-disabling"):
                await launch(None, "/tmp/profile", extra_args=[flag])


@unittest.skipUnless(HAS_DEPS, "Run inside the project container")
class SandboxReport(unittest.TestCase):
    def status(self):
        return {"Layer 1 Sandbox": "Namespace", "PID namespaces": "Yes",
                "Network namespaces": "Yes", "Seccomp-BPF sandbox": "Yes"}

    def test_enabled_report_passes(self):
        verify_sandbox(self.status(), "/opt/chromium --headless")

    def test_disabled_missing_or_changed_report_fails(self):
        for feature in self.status():
            for value in ("No", "Unknown", None):
                status = self.status()
                status[feature] = value
                with self.subTest(feature=feature, value=value), self.assertRaises(RuntimeError):
                    verify_sandbox(status, "/opt/chromium --headless")

    def test_effective_command_line_must_be_readable_and_safe(self):
        for command in ("", "/opt/chromium --no-sandbox", "/opt/chromium --disable-gpu-sandbox"):
            with self.subTest(command=command), self.assertRaises(RuntimeError):
                verify_sandbox(self.status(), command)
