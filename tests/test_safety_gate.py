import unittest
from unittest.mock import patch

from core.agent import process_text
from core.router import dispatch_intent


class TestSafetyGate(unittest.TestCase):
    def test_router_blocks_shutdown_without_confirmation(self) -> None:
        with self.assertRaises(PermissionError):
            dispatch_intent({"intent": "shutdown", "parameters": {}})

    def test_router_allows_shutdown_with_confirmation(self) -> None:
        with patch("core.router.subprocess.run") as run:
            dispatch_intent({"intent": "shutdown", "parameters": {}, "confirmed": True})
            self.assertTrue(run.called)

    def test_agent_shutdown_requires_confirm_prefix(self) -> None:
        with patch("core.router.subprocess.run") as run:
            out = process_text("shutdown")
            self.assertIn("High-risk action needs confirmation", out)
            run.assert_not_called()

    def test_agent_confirm_shutdown_executes(self) -> None:
        with patch("core.router.subprocess.run") as run:
            out = process_text("confirm shutdown")
            self.assertTrue(run.called)
            self.assertNotIn("High-risk action needs confirmation", out)

    def test_close_chrome_routes_to_close_window(self) -> None:
        mock_close = lambda *_args, **_kwargs: "Closed."  # noqa: E731
        with patch.dict("core.router.INTENT_REGISTRY", {"close_window": mock_close}):
            out = process_text("close chrome")
            self.assertIn("Closed.", out)


if __name__ == "__main__":
    unittest.main()
