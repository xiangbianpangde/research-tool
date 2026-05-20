"""Stage 公共工具：文件 I/O、目录、URL 处理、幂等检查。

依据 05-数据流与文件规范.md §5（幂等性）、§6（编码）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data) -> None:
    """UTF-8, indent=2, ensure_ascii=False（05 §6）。"""
    ensure_dir(path.parent)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def domain_of(url: str) -> str:
    """从 URL 取域名，用于 raw/XX-{domain}.md 命名。"""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return "unknown"
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or "unknown"


_SAFE = re.compile(r"[^a-z0-9.\-]+")


def safe_filename(name: str) -> str:
    return _SAFE.sub("-", name.lower()).strip("-") or "file"


def has_output(directory: Path, patterns: list[str]) -> bool:
    """目录存在且至少有一个匹配文件 → 视为该 Stage 已完成（05 §5）。"""
    if not directory.exists():
        return False
    for pat in patterns:
        if any(directory.glob(pat)):
            return True
    return False
