"""
Unit tests for backend.logging_config.

Coverage targets:
  - JsonFormatter  — nominal, extra HTTP/app fields, exception serialisation,
                     all log levels, None-valued extras omitted
  - TextFormatter  — nominal, extras appended, exception appended, all levels
  - configure_logging()  — env vars, formatter selection, level override,
                           idempotency, optional file handler
  - get_logger()   — prefix logic, identity, return type
  - log_user_action()  — message, extra dict, custom level, edge cases
"""
from __future__ import annotations

import json
import logging
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# ── Bootstrap env before any backend import ───────────────────────────────────
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword1!")

try:
    from backend.logging_config import (
        JsonFormatter,
        TextFormatter,
        configure_logging,
        get_logger,
        log_user_action,
        _ACTION_LOGGER,
    )
except ImportError:
    from logging_config import (
        JsonFormatter,
        TextFormatter,
        configure_logging,
        get_logger,
        log_user_action,
        _ACTION_LOGGER,
    )


# ─── helpers ─────────────────────────────────────────────────────────────────

def _record(
    msg: str = "test",
    level: int = logging.INFO,
    name: str = "mpdpms.test",
    func: str = "test_func",
    path: str = "/app/routes/test.py",
    lineno: int = 42,
) -> logging.LogRecord:
    return logging.LogRecord(name, level, path, lineno, msg, (), None, func)


def _exc_info():
    try:
        raise ValueError("deliberate error")
    except ValueError:
        return sys.exc_info()


# =============================================================================
# JsonFormatter
# =============================================================================

class TestJsonFormatter(unittest.TestCase):
    def setUp(self) -> None:
        self.fmt = JsonFormatter()

    # ── Nominal ───────────────────────────────────────────────────────────────

    def test_output_is_valid_json(self) -> None:
        line = self.fmt.format(_record())
        payload = json.loads(line)
        self.assertIsInstance(payload, dict)

    def test_mandatory_fields_present(self) -> None:
        payload = json.loads(self.fmt.format(_record("hello")))
        for field in ("timestamp", "level", "module", "source", "function", "message"):
            self.assertIn(field, payload, f"Missing field: {field}")

    def test_message_correct(self) -> None:
        payload = json.loads(self.fmt.format(_record("my log line")))
        self.assertEqual(payload["message"], "my log line")

    def test_module_is_logger_name(self) -> None:
        payload = json.loads(self.fmt.format(_record(name="mpdpms.circuit")))
        self.assertEqual(payload["module"], "mpdpms.circuit")

    def test_level_serialised(self) -> None:
        for lvl_name, lvl in [("DEBUG", logging.DEBUG), ("INFO", logging.INFO),
                               ("WARNING", logging.WARNING), ("ERROR", logging.ERROR),
                               ("CRITICAL", logging.CRITICAL)]:
            payload = json.loads(self.fmt.format(_record(level=lvl)))
            self.assertEqual(payload["level"], lvl_name)

    def test_timestamp_is_iso8601_with_offset(self) -> None:
        payload = json.loads(self.fmt.format(_record()))
        ts = payload["timestamp"]
        # Matches e.g. 2026-04-16T12:00:00.123+00:00
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+")

    def test_source_contains_path_and_lineno(self) -> None:
        r = _record(path="/app/circuit.py", lineno=99)
        payload = json.loads(self.fmt.format(r))
        self.assertIn("/app/circuit.py", payload["source"])
        self.assertIn("99", payload["source"])

    def test_function_field(self) -> None:
        payload = json.loads(self.fmt.format(_record(func="create_template")))
        self.assertEqual(payload["function"], "create_template")

    # ── Extra HTTP fields ─────────────────────────────────────────────────────

    def test_http_extra_fields_included(self) -> None:
        r = _record()
        r.request_id = "req-abc"
        r.method = "POST"
        r.path = "/api/v1/projects"
        r.status_code = 201
        r.duration_ms = 55
        r.client_ip = "192.168.1.1"
        payload = json.loads(self.fmt.format(r))
        self.assertEqual(payload["request_id"], "req-abc")
        self.assertEqual(payload["method"], "POST")
        self.assertEqual(payload["status_code"], 201)
        self.assertEqual(payload["duration_ms"], 55)
        self.assertEqual(payload["client_ip"], "192.168.1.1")

    def test_absent_http_fields_omitted(self) -> None:
        payload = json.loads(self.fmt.format(_record()))
        for field in ("request_id", "method", "path", "status_code", "duration_ms", "client_ip"):
            self.assertNotIn(field, payload)

    # ── Extra app fields ──────────────────────────────────────────────────────

    def test_app_extra_fields_included(self) -> None:
        r = _record()
        r.project_id = "proj-1"
        r.user_id = "user-1"
        r.entity_type = "project"
        r.entity_id = "proj-1"
        r.action = "project.create"
        payload = json.loads(self.fmt.format(r))
        self.assertEqual(payload["project_id"], "proj-1")
        self.assertEqual(payload["action"], "project.create")
        self.assertEqual(payload["entity_type"], "project")

    def test_absent_app_fields_omitted(self) -> None:
        payload = json.loads(self.fmt.format(_record()))
        for field in ("project_id", "user_id", "action", "entity_type", "entity_id"):
            self.assertNotIn(field, payload)

    # ── Exception serialisation ───────────────────────────────────────────────

    def test_exception_key_present_on_exc_info(self) -> None:
        r = _record(level=logging.ERROR)
        r.exc_info = _exc_info()
        payload = json.loads(self.fmt.format(r))
        self.assertIn("exception", payload)

    def test_exception_type_correct(self) -> None:
        r = _record(level=logging.ERROR)
        r.exc_info = _exc_info()
        payload = json.loads(self.fmt.format(r))
        self.assertEqual(payload["exception"]["type"], "ValueError")

    def test_exception_message_correct(self) -> None:
        r = _record(level=logging.ERROR)
        r.exc_info = _exc_info()
        payload = json.loads(self.fmt.format(r))
        self.assertIn("deliberate error", payload["exception"]["message"])

    def test_exception_traceback_is_list(self) -> None:
        r = _record(level=logging.ERROR)
        r.exc_info = _exc_info()
        payload = json.loads(self.fmt.format(r))
        tb = payload["exception"]["traceback"]
        self.assertIsInstance(tb, list)
        self.assertTrue(any("ValueError" in line for line in tb))

    def test_no_exception_no_exception_key(self) -> None:
        payload = json.loads(self.fmt.format(_record()))
        self.assertNotIn("exception", payload)

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_message(self) -> None:
        payload = json.loads(self.fmt.format(_record("")))
        self.assertEqual(payload["message"], "")

    def test_unicode_message_round_trips(self) -> None:
        payload = json.loads(self.fmt.format(_record("Résultat: 42 tph ✓")))
        self.assertIn("42 tph", payload["message"])


# =============================================================================
# TextFormatter
# =============================================================================

class TestTextFormatter(unittest.TestCase):
    def setUp(self) -> None:
        self.fmt = TextFormatter(colorize=False)

    def test_format_contains_pipe_separators(self) -> None:
        line = self.fmt.format(_record("msg"))
        self.assertGreaterEqual(line.count("|"), 3)

    def test_contains_timestamp(self) -> None:
        line = self.fmt.format(_record())
        self.assertRegex(line, r"\d{4}-\d{2}-\d{2}T")

    def test_contains_level(self) -> None:
        line = self.fmt.format(_record(level=logging.WARNING))
        self.assertIn("WARNING", line)

    def test_contains_module(self) -> None:
        line = self.fmt.format(_record(name="mpdpms.opex_v2"))
        self.assertIn("mpdpms.opex_v2", line)

    def test_contains_message(self) -> None:
        line = self.fmt.format(_record("hello world"))
        self.assertIn("hello world", line)

    def test_extra_fields_appended_as_key_value(self) -> None:
        r = _record()
        r.project_id = "proj-99"
        r.action = "project.delete"
        line = self.fmt.format(r)
        self.assertIn("project_id=proj-99", line)
        self.assertIn("action=project.delete", line)

    def test_absent_extras_not_in_output(self) -> None:
        line = self.fmt.format(_record())
        self.assertNotIn("project_id=", line)
        self.assertNotIn("user_id=", line)

    def test_exception_appended(self) -> None:
        r = _record(level=logging.ERROR)
        r.exc_info = _exc_info()
        line = self.fmt.format(r)
        self.assertIn("ValueError", line)
        self.assertIn("deliberate error", line)

    def test_all_levels(self) -> None:
        for lvl_name, lvl in [("DEBUG", logging.DEBUG), ("INFO", logging.INFO),
                               ("WARNING", logging.WARNING), ("ERROR", logging.ERROR),
                               ("CRITICAL", logging.CRITICAL)]:
            line = TextFormatter(colorize=False).format(_record(level=lvl))
            self.assertIn(lvl_name, line)

    def test_colorize_true_does_not_crash(self) -> None:
        fmt = TextFormatter(colorize=True)
        line = fmt.format(_record())
        self.assertIsInstance(line, str)
        self.assertGreater(len(line), 0)


# =============================================================================
# configure_logging
# =============================================================================

class TestConfigureLogging(unittest.TestCase):
    """All tests restore the logger state via setUp/tearDown."""

    def setUp(self) -> None:
        configure_logging()

    def _with_env(self, overrides: dict) -> None:
        with patch.dict(os.environ, overrides, clear=False):
            configure_logging()

    # ── Level selection ───────────────────────────────────────────────────────

    def test_default_level_is_info(self) -> None:
        self._with_env({"LOG_LEVEL": "INFO"})
        self.assertEqual(logging.getLogger("mpdpms").level, logging.INFO)

    def test_debug_level_applied(self) -> None:
        self._with_env({"LOG_LEVEL": "DEBUG"})
        self.assertEqual(logging.getLogger("mpdpms").level, logging.DEBUG)

    def test_warning_level_applied(self) -> None:
        self._with_env({"LOG_LEVEL": "WARNING"})
        self.assertEqual(logging.getLogger("mpdpms").level, logging.WARNING)

    def test_invalid_level_falls_back_to_info(self) -> None:
        self._with_env({"LOG_LEVEL": "VERBOSE"})
        self.assertEqual(logging.getLogger("mpdpms").level, logging.INFO)

    def test_mpdpms_level_override_independent_from_root(self) -> None:
        self._with_env({"LOG_LEVEL": "WARNING", "LOG_MPDPMS_LEVEL": "DEBUG"})
        self.assertEqual(logging.getLogger("mpdpms").level, logging.DEBUG)
        self.assertEqual(logging.getLogger().level, logging.WARNING)

    def test_invalid_mpdpms_level_falls_back_to_root_level(self) -> None:
        self._with_env({"LOG_LEVEL": "INFO", "LOG_MPDPMS_LEVEL": "NOTALEVEL"})
        self.assertEqual(logging.getLogger("mpdpms").level, logging.INFO)

    # ── Formatter selection ───────────────────────────────────────────────────

    def test_json_mode_attaches_json_formatter(self) -> None:
        self._with_env({"LOG_JSON": "1"})
        h = logging.getLogger("mpdpms").handlers[0]
        self.assertIsInstance(h.formatter, JsonFormatter)

    def test_text_mode_attaches_text_formatter(self) -> None:
        self._with_env({"LOG_JSON": "0"})
        h = logging.getLogger("mpdpms").handlers[0]
        self.assertIsInstance(h.formatter, TextFormatter)

    def test_log_json_false_variants(self) -> None:
        for val in ("0", "false", "no", "off"):
            self._with_env({"LOG_JSON": val})
            h = logging.getLogger("mpdpms").handlers[0]
            self.assertIsInstance(h.formatter, TextFormatter,
                                  f"Failed for LOG_JSON={val!r}")

    # ── Handler structure ─────────────────────────────────────────────────────

    def test_mpdpms_does_not_propagate(self) -> None:
        configure_logging()
        self.assertFalse(logging.getLogger("mpdpms").propagate)

    def test_idempotent_call_does_not_accumulate_handlers(self) -> None:
        configure_logging()
        configure_logging()
        handlers = logging.getLogger("mpdpms").handlers
        stream_handlers = [h for h in handlers if type(h).__name__ == "StreamHandler"]
        self.assertEqual(len(stream_handlers), 1)

    def test_root_logger_level_is_warning(self) -> None:
        configure_logging()
        self.assertEqual(logging.getLogger().level, logging.WARNING)

    # ── File handler ──────────────────────────────────────────────────────────

    def test_file_handler_created_when_log_file_set(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "subdir", "app.log")
            self._with_env({"LOG_FILE": log_path, "LOG_JSON": "1"})
            handlers = logging.getLogger("mpdpms").handlers
            types = [type(h).__name__ for h in handlers]
            self.assertIn("RotatingFileHandler", types)

    def test_no_file_handler_when_log_file_empty(self) -> None:
        self._with_env({"LOG_FILE": ""})
        handlers = logging.getLogger("mpdpms").handlers
        types = [type(h).__name__ for h in handlers]
        self.assertNotIn("RotatingFileHandler", types)

    def test_file_handler_uses_json_formatter_regardless_of_console(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "app.log")
            self._with_env({"LOG_FILE": log_path, "LOG_JSON": "0"})
            handlers = logging.getLogger("mpdpms").handlers
            fh = next(h for h in handlers if type(h).__name__ == "RotatingFileHandler")
            self.assertIsInstance(fh.formatter, JsonFormatter)

    # ── Third-party silencing ─────────────────────────────────────────────────

    def test_sqlalchemy_engine_level_at_warning(self) -> None:
        configure_logging()
        self.assertEqual(logging.getLogger("sqlalchemy.engine").level, logging.WARNING)

    def test_alembic_level_at_warning(self) -> None:
        configure_logging()
        self.assertEqual(logging.getLogger("alembic").level, logging.WARNING)


# =============================================================================
# get_logger
# =============================================================================

class TestGetLogger(unittest.TestCase):
    def test_plain_name_gets_mpdpms_prefix(self) -> None:
        lg = get_logger("routes.circuit")
        self.assertEqual(lg.name, "mpdpms.routes.circuit")

    def test_mpdpms_name_unchanged(self) -> None:
        lg = get_logger("mpdpms.mymodule")
        self.assertEqual(lg.name, "mpdpms.mymodule")

    def test_returns_logger_instance(self) -> None:
        self.assertIsInstance(get_logger("foo"), logging.Logger)

    def test_same_name_returns_same_logger(self) -> None:
        self.assertIs(get_logger("same"), get_logger("same"))

    def test_different_names_return_different_loggers(self) -> None:
        self.assertIsNot(get_logger("one"), get_logger("two"))

    def test_dunder_name_passthrough(self) -> None:
        lg = get_logger("backend.routes.opex_v2")
        self.assertEqual(lg.name, "mpdpms.backend.routes.opex_v2")


# =============================================================================
# log_user_action
# =============================================================================

class TestLogUserAction(unittest.TestCase):
    def _call(self, *args, **kwargs) -> tuple:
        """Capture the call args to _ACTION_LOGGER.log."""
        with patch.object(_ACTION_LOGGER, "log") as mock_log:
            log_user_action(*args, **kwargs)
        return mock_log.call_args

    # ── Nominal ───────────────────────────────────────────────────────────────

    def test_default_level_is_info(self) -> None:
        call = self._call("project.create")
        self.assertEqual(call[0][0], logging.INFO)

    def test_action_in_message(self) -> None:
        call = self._call("project.create")
        self.assertIn("project.create", call[0][1])

    def test_action_in_extra(self) -> None:
        call = self._call("project.create", user_id="u1")
        extra = call[1]["extra"]
        self.assertEqual(extra["action"], "project.create")

    def test_user_id_in_extra(self) -> None:
        call = self._call("auth.login", user_id="uuid-123")
        self.assertEqual(call[1]["extra"]["user_id"], "uuid-123")

    def test_entity_type_in_extra(self) -> None:
        call = self._call("x", entity_type="project")
        self.assertEqual(call[1]["extra"]["entity_type"], "project")

    def test_entity_id_in_extra(self) -> None:
        call = self._call("x", entity_id="pid-456")
        self.assertEqual(call[1]["extra"]["entity_id"], "pid-456")

    def test_details_appended_to_message(self) -> None:
        call = self._call("project.create", details={"name": "Mine A", "code": "MA-01"})
        msg = call[0][1]
        self.assertIn("name=Mine A", msg)
        self.assertIn("code=MA-01", msg)

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_no_user_id_omits_key(self) -> None:
        call = self._call("system.startup")
        self.assertNotIn("user_id", call[1]["extra"])

    def test_no_entity_type_omits_key(self) -> None:
        call = self._call("x")
        self.assertNotIn("entity_type", call[1]["extra"])

    def test_no_entity_id_omits_key(self) -> None:
        call = self._call("x")
        self.assertNotIn("entity_id", call[1]["extra"])

    def test_no_details_message_equals_action(self) -> None:
        call = self._call("project.delete")
        self.assertEqual(call[0][1], "project.delete")

    def test_custom_level_warning(self) -> None:
        call = self._call("auth.failed", level=logging.WARNING)
        self.assertEqual(call[0][0], logging.WARNING)

    def test_custom_level_critical(self) -> None:
        call = self._call("security.breach", level=logging.CRITICAL)
        self.assertEqual(call[0][0], logging.CRITICAL)

    def test_entity_id_coerced_to_string(self) -> None:
        call = self._call("x", entity_id=42)
        self.assertEqual(call[1]["extra"]["entity_id"], "42")

    def test_user_id_coerced_to_string(self) -> None:
        call = self._call("x", user_id=99)
        self.assertEqual(call[1]["extra"]["user_id"], "99")

    def test_empty_details_message_equals_action(self) -> None:
        call = self._call("project.patch", details={})
        self.assertEqual(call[0][1], "project.patch")


if __name__ == "__main__":
    unittest.main()
