"""Respawn comparisons require a successfully loaded document."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import respawn


class RespawnVisit(unittest.IsolatedAsyncioTestCase):
    def context(self, status=200):
        page = SimpleNamespace(goto=AsyncMock(return_value=SimpleNamespace(status=status)),
                               mouse=SimpleNamespace(wheel=AsyncMock()),
                               wait_for_timeout=AsyncMock())
        ctx = SimpleNamespace(pages=[page], storage_state=AsyncMock(return_value={"cookies": []}))
        return ctx, page

    async def test_http_failure_rejects_snapshot(self):
        ctx, _ = self.context(503)
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            await respawn.visit(ctx, "https://example.test", 0, 1)
        ctx.storage_state.assert_not_awaited()

    async def test_navigation_exception_is_not_swallowed(self):
        ctx, page = self.context()
        page.goto.side_effect = TimeoutError("navigation timed out")
        with self.assertRaises(TimeoutError):
            await respawn.visit(ctx, "https://example.test", 0, 1)
        ctx.storage_state.assert_not_awaited()

    async def test_successful_document_can_be_snapshotted(self):
        ctx, _ = self.context()
        self.assertEqual(await respawn.visit(ctx, "https://example.test", 0, 1), {"cookies": []})


if __name__ == "__main__":
    unittest.main()
