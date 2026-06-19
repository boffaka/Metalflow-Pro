from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import smoke_test


class SmokeScriptTests(unittest.TestCase):
    def test_assert_status_raises_on_mismatch(self) -> None:
        with self.assertRaises(AssertionError):
            smoke_test.assert_status(500, 200, "health")

    @patch("backend.smoke_test.time.sleep", return_value=None)
    @patch("backend.smoke_test.request")
    def test_wait_for_health_retries_until_success(self, request_mock, _sleep_mock) -> None:
        request_mock.side_effect = [
            (503, {"status": "error"}),
            (200, {"status": "ok"}),
        ]
        smoke_test.wait_for_health()
        self.assertEqual(request_mock.call_count, 2)

    @patch("backend.smoke_test.wait_for_health", return_value=None)
    @patch("backend.smoke_test.request")
    def test_main_runs_happy_path(self, request_mock, _health_mock) -> None:
        request_mock.side_effect = [
            (200, {"access_token": "token"}),
            (201, {"id": "project-1"}),
            (200, {"email": smoke_test.ADMIN_EMAIL}),
            (200, {"ok": True}),
            (200, [{}] * 6),
            (200, {"items": [], "total_usd": 0}),
            (200, {"id": "block-model-1"}),
            (201, {"count": 1}),
            (200, {"total_blocks": 1}),
            (200, {"ok": True}),
        ]

        exit_code = smoke_test.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(request_mock.call_count, 10)


if __name__ == "__main__":
    unittest.main()
