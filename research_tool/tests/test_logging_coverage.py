from __future__ import annotations

import io
import json
import logging
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

import research_tool.common.logging_config as logging_config
from research_tool.common.logging_config import (
    DailyRotatingHandler,
    JsonFormatter,
    configure_structured_logging,
    emit_log,
    filter_sensitive,
    replace_url,
    setup_logging,
)


class _BrokenReconfigureStream(io.StringIO):
    def reconfigure(self, **kwargs):
        raise OSError("not supported")


@pytest.fixture(autouse=True)
def _restore_logging_state():
    root = logging.getLogger()
    root_handlers = list(root.handlers)
    root_level = root.level
    noisy_levels = {
        name: logging.getLogger(name).level
        for name in ("httpx", "httpcore", "urllib3", "asyncio")
    }
    structured = logging.getLogger("research_tool.structured")
    structured_handlers = list(structured.handlers)
    structured_level = structured.level
    structured_propagate = structured.propagate
    yield
    for handler in list(root.handlers):
        if handler not in root_handlers:
            handler.close()
    root.handlers[:] = root_handlers
    root.setLevel(root_level)
    for name, level in noisy_levels.items():
        logging.getLogger(name).setLevel(level)
    for handler in list(structured.handlers):
        if handler not in structured_handlers:
            handler.close()
    structured.handlers[:] = structured_handlers
    structured.setLevel(structured_level)
    structured.propagate = structured_propagate


@pytest.mark.parametrize(
    ("verbose", "quiet", "expected_level", "handler_count"),
    [
        (True, False, logging.DEBUG, 1),
        (False, True, logging.ERROR, 2),
    ],
)
def test_setup_logging_modes_and_reconfigure_failures(
    monkeypatch, verbose, quiet, expected_level, handler_count
):
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    stdout = _BrokenReconfigureStream()
    stderr = _BrokenReconfigureStream()
    monkeypatch.setattr(logging_config.sys, "stdout", stdout)
    monkeypatch.setattr(logging_config.sys, "stderr", stderr)
    try:
        setup_logging(verbose=verbose, quiet=quiet)
        assert root.level == expected_level
        assert len(root.handlers) == handler_count
    finally:
        root.handlers.clear()
        root.handlers.extend(previous_handlers)
        root.setLevel(previous_level)


def test_setup_logging_default_level(monkeypatch):
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    monkeypatch.setattr(logging_config.sys, "stdout", io.StringIO())
    monkeypatch.setattr(logging_config.sys, "stderr", io.StringIO())
    try:
        setup_logging()
        assert root.level == logging.INFO
    finally:
        root.handlers.clear()
        root.handlers.extend(previous_handlers)
        root.setLevel(previous_level)


def test_recursive_list_filter_and_url_replacement_keep_inputs_immutable():
    sensitive_input = [{"api_key": "marker"}, "plain-value"]
    url_input = [{"url": 123}, {"video_url": "https://example.com/watch"}]

    filtered = filter_sensitive(sensitive_input)
    replaced = replace_url(url_input)

    assert filtered == [{"api_key": "***REDACTED***"}, "plain-value"]
    assert replaced[0]["url"] == 123
    assert replaced[1]["video_url"].startswith("sha256:")
    assert sensitive_input[0]["api_key"] == "marker"
    assert url_input[1]["video_url"] == "https://example.com/watch"


def test_json_formatter_preserves_non_reserved_extra_fields():
    record = logging.LogRecord("core", logging.INFO, "", 0, "hello", (), None)
    record.extra_fields = {"custom": {"answer": 42}, "msg": "ignored override"}

    payload = json.loads(JsonFormatter().format(record))

    assert payload["msg"] == "hello"
    assert payload["custom"] == {"answer": 42}


def test_daily_handler_ignores_chmod_failure_and_rolls_over(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_config.os, "chmod", lambda *args: (_ for _ in ()).throw(OSError()))
    handler = DailyRotatingHandler(tmp_path, retention_days=0)
    try:
        current_name = handler.baseFilename
        handler._current_date -= timedelta(days=1)
        record = logging.LogRecord("core", logging.INFO, "", 0, "hello", (), None)
        assert handler.shouldRollover(record)
        handler.doRollover()
        handler.emit(record)
        handler.flush()
        assert handler.stream is not None
        assert "hello" in Path(handler.baseFilename).read_text(encoding="utf-8")
        assert current_name
    finally:
        handler.close()


def test_daily_handler_removes_expired_files(tmp_path):
    expired = tmp_path / "structured-2000-01-01.log"
    expired.write_text("old", encoding="utf-8")
    old = time.time() - 10 * 86400
    os.utime(expired, (old, old))

    handler = DailyRotatingHandler(tmp_path, retention_days=1)
    try:
        assert not expired.exists()
    finally:
        handler.close()


def test_daily_handler_cleanup_ignores_filesystem_error(tmp_path, monkeypatch):
    handler = DailyRotatingHandler(tmp_path, retention_days=1)

    def fail_glob(self, pattern):
        raise OSError("filesystem unavailable")

    try:
        monkeypatch.setattr(Path, "glob", fail_glob)
        handler._cleanup_expired()
    finally:
        handler.close()


def test_configure_structured_logging_replaces_existing_handler(tmp_path):
    logger = configure_structured_logging(tmp_path)
    first = next(h for h in logger.handlers if isinstance(h, DailyRotatingHandler))

    logger = configure_structured_logging(tmp_path)
    second = next(h for h in logger.handlers if isinstance(h, DailyRotatingHandler))

    assert first is not second
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_configure_structured_logging_falls_back_to_stderr(tmp_path, monkeypatch):
    logger = logging.getLogger("research_tool.structured")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    class BrokenDailyHandler(logging.Handler):
        def __init__(self, *args, **kwargs):
            raise OSError("read only")

    monkeypatch.setattr(logging_config, "DailyRotatingHandler", BrokenDailyHandler)
    configured = configure_structured_logging(tmp_path)
    try:
        assert len(configured.handlers) == 1
        assert isinstance(configured.handlers[0], logging.StreamHandler)
        assert configured.propagate is False
    finally:
        for handler in list(configured.handlers):
            handler.close()
            configured.removeHandler(handler)


def test_emit_log_auto_configures_and_defaults_unknown_level(monkeypatch):
    logger = logging.getLogger("research_tool.structured")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    calls: list[bool] = []

    def fake_configure():
        calls.append(True)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    monkeypatch.setattr(logging_config, "configure_structured_logging", fake_configure)
    try:
        emit_log("not-a-level", "hello")
        assert calls == [True]
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_emit_log_includes_duration_and_code(tmp_path):
    logger = configure_structured_logging(tmp_path)
    try:
        emit_log("warning", "slow", duration_ms=42, code="E_TEST")
        for handler in logger.handlers:
            handler.flush()
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    payload = json.loads(next(tmp_path.glob("*.log")).read_text(encoding="utf-8"))
    assert payload["duration_ms"] == 42
    assert payload["code"] == "E_TEST"
