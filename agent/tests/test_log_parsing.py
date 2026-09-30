"""
Tests for the two pure parsing functions extracted into log_parsing.py.
No Docker, no Redis, no network — pure Python only.
"""
import sys
import os
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from log_parsing import parse_log_line, is_error


class TestParseLogLine:
    def test_valid_json_returns_dict(self):
        line = '{"event": "user_not_found", "level": "error", "timestamp": "2024-01-01T12:00:00"}'
        result = parse_log_line(line)
        assert result is not None
        assert result["event"] == "user_not_found"

    def test_valid_json_includes_raw_field(self):
        line = '{"event": "order_created", "level": "info"}'
        result = parse_log_line(line)
        assert "_raw" in result
        assert result["_raw"] == line

    def test_plain_text_returns_none(self):
        assert parse_log_line("INFO:     Application startup complete.") is None

    def test_empty_string_returns_none(self):
        assert parse_log_line("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_log_line("   \n  ") is None

    def test_partial_json_returns_none(self):
        assert parse_log_line('{"event": "broken"') is None

    def test_non_object_json_returns_none(self):
        # All real log lines are JSON objects. Arrays and scalars are not
        # valid log lines and should be treated as unparseable.
        assert parse_log_line("[1, 2, 3]") is None
        assert parse_log_line('"just a string"') is None

    def test_leading_trailing_whitespace_stripped(self):
        line = '  {"event": "test", "level": "info"}  '
        result = parse_log_line(line)
        assert result is not None
        assert result["event"] == "test"

    def test_nested_json_preserved(self):
        line = '{"event": "db_error", "context": {"user_id": 1, "item": "keyboard"}}'
        result = parse_log_line(line)
        assert result["context"]["user_id"] == 1

    def test_uvicorn_startup_line_returns_none(self):
        assert parse_log_line("INFO:     Uvicorn running on http://0.0.0.0:8000") is None

    def test_unicode_content_handled(self):
        line = '{"event": "test", "message": "こんにちは"}'
        result = parse_log_line(line)
        assert result["message"] == "こんにちは"


class TestIsError:
    def test_error_level_is_error(self):
        assert is_error({"event": "user_not_found", "level": "error"}) is True

    def test_critical_level_is_error(self):
        assert is_error({"event": "db_down", "level": "critical"}) is True

    def test_info_level_is_not_error(self):
        assert is_error({"event": "user_fetched", "level": "info"}) is False

    def test_warning_level_is_not_error(self):
        assert is_error({"event": "slow_query", "level": "warning"}) is False

    def test_debug_level_is_not_error(self):
        assert is_error({"event": "trace", "level": "debug"}) is False

    def test_missing_level_is_not_error(self):
        assert is_error({"event": "something"}) is False

    def test_empty_level_is_not_error(self):
        assert is_error({"event": "something", "level": ""}) is False

    def test_uppercase_level_handled(self):
        assert is_error({"event": "fail", "level": "ERROR"}) is True

    def test_mixed_case_level_handled(self):
        assert is_error({"event": "fail", "level": "Error"}) is True
