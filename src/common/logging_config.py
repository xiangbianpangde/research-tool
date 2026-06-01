"""项目级日志配置。

依据 02-代码编写规范 §四：统一 logging 模块，禁止 print()。
CLI 工具向 stdout 输出用户消息（INFO 无时间戳），stderr 输出问题（WARNING+ 带戳）。
"""

from __future__ import annotations

import logging
import sys

_DETAIL_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
_DATE_FMT = "%H:%M:%S"


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
