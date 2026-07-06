"""项目级日志配置 + 结构化 JSON Lines 日志器。

依据 02-代码编写规范 §四：统一 logging 模块，禁止 print()。
CLI 工具向 stdout 输出用户消息（INFO 无时间戳），stderr 输出问题（WARNING+ 带戳）。

V1.1 扩展（DD-001 M-011）：
- JsonFormatter：LogRecord → JSON Lines（固定字段顺序）
- DailyRotatingHandler：按日切分 + 30 天保留
- SensitiveFilter：敏感字段（API_KEY/COOKIE/PROMPT/Authorization）递归 redact
- UrlHasher：url → sha256(url) 替换（不暴露原始 URL）
- emit_log / configure_structured_logging：高层入口
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from logging.handlers import BaseRotatingHandler
from pathlib import Path
from typing import Any, ClassVar

_DETAIL_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
_DATE_FMT = "%H:%M:%S"

# --------------------------------------------------------------------------- #
# V1.0：原 setup_logging / get_logger 保持不变（向后兼容）
# --------------------------------------------------------------------------- #


def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    """配置根 logger。

    INFO → stdout（干净格式，无时间戳，用户可读）；DEBUG/WARNING/ERROR → stderr（含时间戳+模块）。

    Args:
        verbose: True 时启用 DEBUG 级别
        quiet: True 时仅输出 ERROR 级别
    """
    root = logging.getLogger()
    root.handlers.clear()

    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    root.setLevel(level)

    # INFO → stdout（干净格式）
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(lambda r: r.levelno == logging.INFO)
    stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stdout_handler)

    # DEBUG/WARNING/ERROR → stderr（含时间戳+模块）
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.DEBUG)
    stderr_handler.addFilter(lambda r: r.levelno != logging.INFO)
    stderr_handler.setFormatter(logging.Formatter(_DETAIL_FORMAT, _DATE_FMT))
    root.addHandler(stderr_handler)

    # 抑制第三方库的 DEBUG 噪音
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> logging.Logger:
    """获取模块级 logger。

    约定每个模块顶部：`logger = get_logger(__name__)`
    """
    return logging.getLogger(name)


# --------------------------------------------------------------------------- #
# V1.1 新增：结构化日志（JsonFormatter / DailyRotatingHandler / ...）
# --------------------------------------------------------------------------- #


# 敏感字段名（不区分大小写，递归 redact 嵌套 dict，深度上限 5）
_SENSITIVE_KEYS: ClassVar[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "cookie",
        "set-cookie",
        "set_cookie",
        "prompt",
        "system_prompt",
        "authorization",
        "auth",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
    }
)

# 嵌套递归最大深度（防止恶意输入导致栈溢出）
_MAX_REDACT_DEPTH = 5

# 默认日志目录
DEFAULT_LOG_DIR = Path("./research-output/logs")
DEFAULT_RETENTION_DAYS = 30


# ---- 敏感字段过滤（黑名单） ---------------------------------------------- #


def _redact_value(value: Any) -> Any:
    """非 dict/list 的 leaf：若字符串且匹配敏感模式 → '***REDACTED***'。"""
    if isinstance(value, str):
        # 简单启发式：长字符串（≥16 字符）视为可能含 token
        if len(value) >= 16 and any(c in value for c in "._-"):
            return "***REDACTED***"
    return value


def filter_sensitive(obj: Any, depth: int = 0) -> Any:
    """递归 redact 敏感字段（IC-028 敏感过滤）。

    Args:
        obj: 任意对象（dict/list/scalar）
        depth: 当前递归深度（首次调用为 0）

    Returns:
        redact 后的新对象（不修改原对象）
    """
    if depth >= _MAX_REDACT_DEPTH:
        return obj
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                out[k] = "***REDACTED***"
            else:
                out[k] = filter_sensitive(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [filter_sensitive(v, depth + 1) for v in obj]
    return _redact_value(obj)


# ---- URL 哈希器 ---------------------------------------------------------- #


def hash_url(url: str) -> str:
    """URL → sha256 前 16 位（IC-028 URL 替换）。

    Args:
        url: 原始 URL

    Returns:
        16 位十六进制字符串（"sha256:abcdef1234567890"）
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def replace_url(obj: Any) -> Any:
    """递归把 dict 中所有 'url' / 'video_url' 键的值替换为 sha256。

    Args:
        obj: 任意对象

    Returns:
        替换后的新对象
    """
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in ("url", "video_url"):
                if isinstance(v, str):
                    out[k] = hash_url(v)
                else:
                    out[k] = v
            else:
                out[k] = replace_url(v)
        return out
    if isinstance(obj, list):
        return [replace_url(v) for v in obj]
    return obj


# ---- JSON 格式化器（IC-028 emit_log） ------------------------------------ #


class JsonFormatter(logging.Formatter):
    """LogRecord → JSON Lines（固定字段顺序）。

    字段顺序：ts → level → module → task_id → url_sha256 → step
               → duration_ms → code → msg → *kwargs
    """

    # 字段顺序（与设计文档保持一致，便于 jq/grep 链式查询）
    _FIELD_ORDER: ClassVar[tuple[str, ...]] = (
        "ts",
        "level",
        "module",
        "task_id",
        "url_sha256",
        "step",
        "duration_ms",
        "code",
        "msg",
    )

    def format(self, record: logging.LogRecord) -> str:
        # 1) 基础字段
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }

        # 2) 附加字段（从 record.__dict__ / extra 中提取）
        extras = getattr(record, "extra_fields", None)
        if isinstance(extras, dict):
            for k, v in extras.items():
                if k not in payload:
                    payload[k] = v

        # 3) 敏感字段 redact + URL 哈希
        payload = filter_sensitive(payload)
        payload = replace_url(payload)

        # 4) 固定字段顺序输出
        ordered: dict[str, Any] = {}
        for key in self._FIELD_ORDER:
            if key in payload:
                ordered[key] = payload[key]
        # 剩余字段追加
        for k, v in payload.items():
            if k not in ordered:
                ordered[k] = v

        return json.dumps(ordered, ensure_ascii=False, default=str)


# ---- 按日切分 Handler ---------------------------------------------------- #


class DailyRotatingHandler(BaseRotatingHandler):
    """按日切分日志文件（基于 UTC 日期，模板方法模式）。

    启动时尝试 chmod(log_dir, 0o700) 满足 IC-028 安全前置。
    跨日时自动 rollover 到 log_dir/structured-YYYY-MM-DD.log。
    """

    def __init__(
        self,
        log_dir: str | Path = DEFAULT_LOG_DIR,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        encoding: str = "utf-8",
    ) -> None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        # IC-028 前置：日志目录 0o700
        try:
            os.chmod(log_dir, 0o700)
        except OSError:
            # Windows 上 os.chmod 只能设置只读位，0o700 在 Windows 无效；忽略
            pass

        self._log_dir = log_dir
        self._retention_days = retention_days
        self._current_date = datetime.now(tz=timezone.utc).date()
        base = self._filepath_for_date(self._current_date)
        super().__init__(base, mode="a", encoding=encoding, delay=False)
        # 启动时清理过期文件
        self._cleanup_expired()

    def _filepath_for_date(self, d: Any) -> str:
        return str(self._log_dir / f"structured-{d.isoformat()}.log")

    def shouldRollover(self, record: logging.LogRecord) -> bool:  # noqa: N802
        today = datetime.fromtimestamp(record.created, tz=timezone.utc).date()
        return today != self._current_date

    def doRollover(self) -> None:  # noqa: N802
        if self.stream:
            self.stream.close()
            self.stream = None
        self._current_date = datetime.now(tz=timezone.utc).date()
        self.baseFilename = self._filepath_for_date(self._current_date)
        self._open_stream()
        self._cleanup_expired()

    def _cleanup_expired(self) -> None:
        """清理超过 retention_days 的日志文件。"""
        if self._retention_days <= 0:
            return
        cutoff = time.time() - self._retention_days * 86400
        try:
            for path in self._log_dir.glob("structured-*.log"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
        except OSError:
            # 清理失败不阻塞主链
            pass


# ---- 公共 API（IC-028 emit_log / configure_structured_logging） ----------- #


_STRUCTURED_LOGGER_NAME = "research_tool.structured"


def configure_structured_logging(
    log_dir: str | Path | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    level: int = logging.INFO,
) -> logging.Logger:
    """注册结构化日志 Handler/Formatter（IC-028 configure_logging）。

    Args:
        log_dir: 日志目录（None=DEFAULT_LOG_DIR）
        retention_days: 保留天数（<=0=永不过期）
        level: 日志级别

    Returns:
        配置好的 logger（可独立 emit_log；不影响 root logger）
    """
    logger = logging.getLogger(_STRUCTURED_LOGGER_NAME)
    logger.setLevel(level)
    # 移除已有的同 handler（避免重复）
    for h in list(logger.handlers):
        if isinstance(h, DailyRotatingHandler):
            logger.removeHandler(h)

    try:
        handler = DailyRotatingHandler(
            log_dir=log_dir or DEFAULT_LOG_DIR,
            retention_days=retention_days,
        )
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    except OSError:
        # 路径不可写：降级为 stderr
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(JsonFormatter())
        logger.addHandler(stderr_handler)
        logger.propagate = False

    return logger


def emit_log(
    level: str = "info",
    msg: str = "",
    *,
    task_id: str | None = None,
    step: str | None = None,
    duration_ms: int | None = None,
    code: str | None = None,
    url: str | None = None,
    **kwargs: Any,
) -> None:
    """写入一条结构化日志（IC-028 emit_log）。

    Args:
        level: 日志级别（debug/info/warning/error）
        msg: 消息文本
        task_id: 任务 ID
        step: 当前步骤
        duration_ms: 耗时（毫秒）
        code: 错误码（来自 ErrorCode）
        url: 原始 URL（自动 sha256 替换；不写原始值）
        **kwargs: 任意附加字段
    """
    logger = logging.getLogger(_STRUCTURED_LOGGER_NAME)
    if not logger.handlers:
        configure_structured_logging()

    extras: dict[str, Any] = dict(kwargs)
    if task_id is not None:
        extras["task_id"] = task_id
    if step is not None:
        extras["step"] = step
    if duration_ms is not None:
        extras["duration_ms"] = duration_ms
    if code is not None:
        extras["code"] = code
    if url is not None:
        extras["url_sha256"] = hash_url(url)

    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.log(log_level, msg, extra={"extra_fields": extras})


__all__ = [
    # V1.0
    "setup_logging",
    "get_logger",
    # V1.1
    "JsonFormatter",
    "DailyRotatingHandler",
    "filter_sensitive",
    "hash_url",
    "replace_url",
    "configure_structured_logging",
    "emit_log",
    "DEFAULT_LOG_DIR",
    "DEFAULT_RETENTION_DAYS",
]
