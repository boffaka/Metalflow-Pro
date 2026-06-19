from __future__ import annotations

import json
import logging
import unittest

from backend.observability import JsonFormatter, security_headers, startup_mode


class ObservabilityTests(unittest.TestCase):
    def test_startup_mode_resolution(self) -> None:
        self.assertEqual(startup_mode(True, False), "alembic")
        self.assertEqual(startup_mode(False, True), "bootstrap")
        self.assertEqual(startup_mode(False, False), "seed-only")

    def test_security_headers_include_request_id(self) -> None:
        headers = security_headers("req-123", enable_hsts=True)
        self.assertEqual(headers["X-Request-ID"], "req-123")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("Strict-Transport-Security", headers)

    def test_json_formatter_emits_expected_fields(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord("mpdpms", logging.INFO, __file__, 10, "request complete", (), None)
        record.request_id = "req-1"
        record.method = "GET"
        record.path = "/api/v1/health"
        record.status_code = 200
        payload = json.loads(formatter.format(record))
        self.assertEqual(payload["module"], "mpdpms")
        self.assertEqual(payload["request_id"], "req-1")
        self.assertEqual(payload["status_code"], 200)

    def test_json_formatter_includes_source_and_function(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord("mpdpms.auth", logging.WARNING, "/app/auth.py", 55, "warn", (), None, "login")
        payload = json.loads(formatter.format(record))
        self.assertIn("source", payload)
        self.assertIn("/app/auth.py", payload["source"])
        self.assertEqual(payload["function"], "login")

    def test_json_formatter_exception_has_type_and_traceback(self) -> None:
        import sys
        formatter = JsonFormatter()
        try:
            raise RuntimeError("something broke")
        except RuntimeError:
            exc_info = sys.exc_info()
        record = logging.LogRecord("mpdpms", logging.ERROR, __file__, 1, "err", (), exc_info)
        payload = json.loads(formatter.format(record))
        self.assertIn("exception", payload)
        self.assertEqual(payload["exception"]["type"], "RuntimeError")
        self.assertIsInstance(payload["exception"]["traceback"], list)


if __name__ == "__main__":
    unittest.main()
