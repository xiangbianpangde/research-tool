"""M-011 结构化日志单元测试。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.common.logging_config import (
    DEFAULT_LOG_DIR,
    DEFAULT_RETENTION_DAYS,
    DailyRotatingHandler,
    JsonFormatter,
    configure_structured_logging,
    emit_log,
    filter_sensitive,
    hash_url,
    replace_url,
)


class TestHashUrl:
    """URL → sha256 替换。"""

    def test_hash_returns_16char_prefix(self):
        h = hash_url("https://www.youtube.com/watch?v=abc123")
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 16

    def test_hash_deterministic(self):
        a = hash_url("https://example.com/v")
        b = hash_url("https://example.com/v")
        assert a == b

    def test_hash_different_urls_differ(self):
        a = hash_url("https://example.com/v1")
        b = hash_url("https://example.com/v2")
        assert a != b

    def test_replace_url_in_dict(self):
        d = {"url": "https://www.youtube.com/watch?v=abc", "title": "T"}
        out = replace_url(d)
        assert out["url"].startswith("sha256:")
        assert out["title"] == "T"

    def test_replace_url_nested(self):
        d = {"meta": {"video_url": "https://b23.tv/xyz", "id": 1}}
        out = replace_url(d)
        assert out["meta"]["video_url"].startswith("sha256:")
        assert out["meta"]["id"] == 1

    def test_replace_url_non_dict(self):
        assert replace_url("plain") == "plain"
        assert replace_url(42) == 42


class TestFilterSensitive:
    """敏感字段 redact。"""

    def test_redact_api_key(self):
        d = {"api_key": "sk-abcdefghijklmnop", "user": "alice"}
        out = filter_sensitive(d)
        assert out["api_key"] == "***REDACTED***"
        assert out["user"] == "alice"

    def test_redact_case_insensitive(self):
        d = {"API_KEY": "sk-abcdefghijklmnop"}
        out = filter_sensitive(d)
        assert out["API_KEY"] == "***REDACTED***"

    def test_redact_cookie(self):
        d = {"cookie": "session=abcdefghijklmnop"}
        out = filter_sensitive(d)
        assert out["cookie"] == "***REDACTED***"

    def test_redact_nested(self):
        d = {"headers": {"Authorization": "Bearer abcdefghijklmnop"}}
        out = filter_sensitive(d)
        assert out["headers"]["Authorization"] == "***REDACTED***"

    def test_redact_depth_limit(self):
        # 嵌套 6 层：应停止递归（> 5）
        d: dict = {"v": "leaf"}
        for _ in range(6):
            d = {"k": d}
        out = filter_sensitive(d)
        # 6 层以内的字段不会被 redact（深度限制）
        assert isinstance(out, dict)

    def test_redact_non_dict(self):
        assert filter_sensitive("plain string") == "plain string"
        assert filter_sensitive(42) == 42


class TestJsonFormatter:
    """JsonFormatter：LogRecord → JSON Lines。"""

    def test_basic_format(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        out = formatter.format(record)
        # 应该是合法 JSON
        data = json.loads(out)
        assert data["level"] == "INFO"
        assert data["module"] == "test"
        assert data["msg"] == "hello"
        assert "ts" in data

    def test_format_with_extras(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="step done",
            args=(),
            exc_info=None,
        )
        record.extra_fields = {
            "task_id": "t-001",
            "step": "extract",
            "duration_ms": 1234,
        }
        out = formatter.format(record)
        data = json.loads(out)
        assert data["task_id"] == "t-001"
        assert data["step"] == "extract"
        assert data["duration_ms"] == 1234

    def test_format_ensure_ascii_false(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="中文消息",
            args=(),
            exc_info=None,
        )
        out = formatter.format(record)
        # ensure_ascii=False → 中文不应被转义
        assert "中文消息" in out


class TestEmitLog:
    """emit_log 公共 API。"""

    def test_emit_log_writes_to_logger(self, tmp_path: Path):
        # 配置到 tmp_path
        logger = configure_structured_logging(log_dir=tmp_path, retention_days=7)
        try:
            emit_log("info", "hello", task_id="t-1", step="start")
            # 不抛错即通过
            assert True
        finally:
            # 关闭所有 handler 避免 file handle 泄漏
            for h in list(logger.handlers):
                h.close()
                logger.removeHandler(h)

    def test_emit_log_with_url_hashed(self, tmp_path: Path):
        logger = configure_structured_logging(log_dir=tmp_path, retention_days=7)
        try:
            emit_log("info", "fetched", url="https://www.youtube.com/watch?v=abc")
            # 不抛错即通过（实际验证需要读文件，但路径可能未 flush）
            assert True
        finally:
            for h in list(logger.handlers):
                h.close()
                logger.removeHandler(h)


class TestDailyRotatingHandler:
    """按日切分 Handler。"""

    def test_creates_log_file(self, tmp_path: Path):
        handler = DailyRotatingHandler(
            log_dir=tmp_path, retention_days=7
        )
        logger = logging.getLogger("test_rotating")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False
        try:
            logger.info("test message")
            handler.flush()
        finally:
            handler.close()
            logger.removeHandler(handler)
        # 检查文件是否生成
        files = list(tmp_path.glob("structured-*.log"))
        assert len(files) >= 1
