"""Stage 2: Cleaner —— 去噪、定位正文。

依据 01 §3.4 清洗规则（必须实现）、方法论 阶段2.1/2.2、05 §3 Stage2。
纯本地算法，无 LLM、同步执行（03 §3 用 cleaner.process(...)）。
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

from ..models import CleanerConfig, CleanResult, FileQuality
from .base import ensure_dir, write_json, write_text

# 元数据头：连续的 <!-- ... --> 行
_META_LINE = re.compile(r"^\s*<!--.*-->\s*$")
# 整块标签去除
_BLOCK_TAGS = re.compile(
    r"<(script|style|noscript|nav|footer|form|header|aside)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_ANY_TAG = re.compile(r"<[^>]+>")
# 广告关键词（01 §3.4 规则4）
_AD = re.compile(
    r"\b(advertisement|sponsored|promoted|ad[_\-]?block|adsbygoogle)\b", re.IGNORECASE
)
# 尾部截断标题（01 §3.4 规则6）
_TAIL = re.compile(
    r"^\s*#{1,6}\s*(Contributing|License|References|参考文献|致谢|License & Credits)\b",
    re.IGNORECASE,
)
# markdown 链接/纯链接行
_LINK_LINE = re.compile(r"^\s*[-*]?\s*\[[^\]]*\]\([^)]*\)\s*$")
_URL_ONLY = re.compile(r"^\s*https?://\S+\s*$")
_BLANKS = re.compile(r"\n{3,}")

_SHORT_LEN = 40           # 01 §3.4 规则3：短行阈值
_NAV_RUN_MIN = 3          # 连续 N 行
_NAV_SHORT_RATIO = 0.7    # 70% 为短行
_CONTENT_START_MIN = 120  # 正文起点：首个达此长度的实质段落


def _split_meta(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (_META_LINE.match(lines[i]) or not lines[i].strip()):
        i += 1
    header = "\n".join(lines[:i]).strip()
    body = "\n".join(lines[i:])
    return header, body


def _strip_html(text: str) -> str:
    text = _BLOCK_TAGS.sub("\n", text)
    text = _ANY_TAG.sub("", text)
    return unescape(text)


def _is_short(line: str) -> bool:
    return len(line.strip()) < _SHORT_LEN


def _remove_nav_blocks(lines: list[str]) -> list[str]:
    """删除连续 >=3 行且 70% 为短行的导航块（01 §3.4 规则3）。"""
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if not lines[i].strip():
            out.append(lines[i])
            i += 1
            continue
        # 收集一段连续非空行
        j = i
        while j < n and lines[j].strip():
            j += 1
        block = lines[i:j]
        short = sum(1 for ln in block if _is_short(ln))
        if len(block) >= _NAV_RUN_MIN and short / len(block) >= _NAV_SHORT_RATIO:
            pass  # 视为导航，丢弃
        else:
            out.extend(block)
        i = j
    return out


def _is_linkish(line: str) -> bool:
    return bool(_LINK_LINE.match(line) or _URL_ONLY.match(line))


def _find_content_start(lines: list[str]) -> int:
    """返回首个达到 _CONTENT_START_MIN 长度且非链接的行索引（01 §3.4 规则5）。"""
    for idx, ln in enumerate(lines):
        if _is_linkish(ln):
            continue
        if len(ln.strip()) >= _CONTENT_START_MIN:
            return idx
    return 0  # 未找到则不裁剪


def _truncate_tail(lines: list[str]) -> list[str]:
    for idx, ln in enumerate(lines):
        if _TAIL.match(ln):
            return lines[:idx]
    return lines


class Cleaner:
    def __init__(self, config: CleanerConfig | None = None) -> None:
        self.config = config or CleanerConfig()

    def clean_text(self, text: str) -> str:
        header, body = _split_meta(text)
        cfg = self.config

        if cfg.strip_html:
            body = _strip_html(body)

        lines = body.splitlines()

        if cfg.strip_ads:
            lines = [ln for ln in lines if not _AD.search(ln)]

        if cfg.strip_nav:
            lines = _remove_nav_blocks(lines)

        if cfg.find_content_start:
            start = _find_content_start(lines)
            lines = lines[start:]

        lines = _truncate_tail(lines)

        cleaned = _BLANKS.sub("\n\n", "\n".join(lines)).strip()
        if header:
            cleaned = header + "\n\n" + cleaned
        return cleaned

    def process(self, input_dir: Path, work_dir: Path | None = None) -> CleanResult:
        """读 raw/*.md，写 clean/*.md + quality.json。

        默认输出目录：把路径中的 raw 替换为 clean（02 §3）。
        """
        input_dir = Path(input_dir)
        if work_dir is not None:
            clean_dir = Path(work_dir) / "clean"
        elif input_dir.name == "raw":
            clean_dir = input_dir.parent / "clean"
        else:
            clean_dir = input_dir.parent / "clean"
        ensure_dir(clean_dir)

        files: list[Path] = []
        quality: dict[str, FileQuality] = {}

        for src in sorted(input_dir.glob("*.md")):
            original = src.read_text(encoding="utf-8")
            cleaned = self.clean_text(original)
            out = clean_dir / src.name
            write_text(out, cleaned)
            files.append(out)

            issues: list[str] = []
            body_len = len(_split_meta(cleaned)[1])
            if body_len < self.config.min_content_length:
                issues.append("too_short")
            orig_size = len(original)
            quality[src.stem] = FileQuality(
                original_size=orig_size,
                cleaned_size=len(cleaned),
                score=round(len(cleaned) / orig_size, 3) if orig_size else 0.0,
                issues=issues,
            )

        write_json(
            clean_dir / "quality.json",
            {k: v.model_dump() for k, v in quality.items()},
        )
        return CleanResult(files=files, quality_report=quality, clean_dir=clean_dir)


def clean(input_dir: Path, config: CleanerConfig, work_dir: Path) -> CleanResult:
    """模块级函数（01 §3.2 签名）。"""
    return Cleaner(config).process(input_dir, work_dir)
