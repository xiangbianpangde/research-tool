"""Stage 2: Cleaner —— 去噪、定位正文。

依据 01 §3.4 清洗规则（必须实现）、方法论 阶段2.1/2.2、05 §3 Stage2。
process() 仍是纯本地算法、同步；P2-5 新增 MinHash 去重也在 process() 内完成。
async filter_relevance() 是可选 LLM 过滤步骤，pipeline 在 process 之后串调。
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from ...domain.models import CleanerConfig, CleanResult, FileQuality
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
_AD = re.compile(r"\b(advertisement|sponsored|promoted|ad[_\-]?block|adsbygoogle)\b", re.IGNORECASE)
# 尾部截断标题（01 §3.4 规则6）
_TAIL = re.compile(
    r"^\s*#{1,6}\s*(Contributing|License|References|参考文献|致谢|License & Credits)\b",
    re.IGNORECASE,
)
# markdown 链接/纯链接行
_LINK_LINE = re.compile(r"^\s*[-*]?\s*\[[^\]]*\]\([^)]*\)\s*$")
_URL_ONLY = re.compile(r"^\s*https?://\S+\s*$")
_BLANKS = re.compile(r"\n{3,}")

_SHORT_LEN = 40  # 01 §3.4 规则3：短行阈值
_NAV_RUN_MIN = 3  # 连续 N 行
_NAV_SHORT_RATIO = 0.7  # 70% 为短行
_CONTENT_START_MIN = 120  # 正文起点：首个达此长度的实质段落


def _looks_binary(text: str, sample: int = 4000) -> bool:
    """检测 PDF 二进制残留等乱码：替换符/控制字符占比过高即判为非文本。"""
    s = text[:sample]
    if not s:
        return False
    if "%PDF" in s[:200] or "FlateDecode" in s or "/Filter" in s:
        return True
    bad = sum(1 for ch in s if ch == "�" or (ord(ch) < 32 and ch not in "\n\r\t"))
    return bad / len(s) > 0.15


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


# --- MinHash 去重（P2-5）：char n-gram + Jaccard --------------------------- #
_NGRAM = 5
_WS = re.compile(r"\s+")


def _shingles(text: str) -> set[str]:
    """text 归一化后切 char n-gram。极短文本退化为整串本身一个 shingle。"""
    s = _WS.sub(" ", text).strip().lower()
    if len(s) < _NGRAM:
        return {s} if s else set()
    return {s[i : i + _NGRAM] for i in range(len(s) - _NGRAM + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def _dedup_groups(
    items: list[tuple[str, set[str], int]],
    threshold: float,
) -> dict[str, str]:
    """items: [(name, shingles, length)]；返回 {被丢弃 name → 胜出 name}。

    union-find 合并相似文档，组内保留 length 最大者。文档数 typically <200，
    O(n²) Jaccard 在 ms 级别，无需额外依赖。
    """
    n = len(items)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if _jaccard(items[i][1], items[j][1]) >= threshold:
                union(i, j)

    # 按根分组 → 选最长保留 → 其余指向胜出者
    by_root: dict[int, list[int]] = {}
    for i in range(n):
        by_root.setdefault(find(i), []).append(i)
    losers: dict[str, str] = {}
    for group in by_root.values():
        if len(group) <= 1:
            continue
        winner_idx = max(group, key=lambda k: items[k][2])
        winner_name = items[winner_idx][0]
        for k in group:
            if k != winner_idx:
                losers[items[k][0]] = winner_name
    return losers


# --- LLM 相关性过滤（P2-5）------------------------------------------------ #


class _Scores(BaseModel):
    """LLM 批量评分返回结构，顺序对应输入条目。"""

    scores: list[float] = Field(default_factory=list)


_SCORE_SYSTEM = "你是严谨的相关性裁判，按调研主题给资料打 0-1 分（仅判相关性，不评质量）。"


def _read_title_excerpt(path: Path, excerpt_chars: int = 400) -> tuple[str, str]:
    """从 clean/*.md 提取 <!-- title --> 与去注释正文前 N 字。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return path.stem, ""
    title_m = re.search(r"<!--\s*title:\s*(.*?)\s*-->", text)
    title = title_m.group(1) if title_m else path.stem
    body = "\n".join(ln for ln in text.splitlines() if not _META_LINE.match(ln)).strip()
    return title, body[:excerpt_chars]


class Cleaner:
    def __init__(self, config: CleanerConfig | None = None) -> None:
        self.config = config or CleanerConfig()

    def clean_text(self, text: str) -> str:
        header, body = _split_meta(text)
        cfg = self.config

        # 兜底：二进制/乱码（如未解析的 PDF）直接丢弃正文，只留来源头
        if _looks_binary(body):
            return header

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
        # MinHash 去重输入：每个清洗后文档的正文 shingles + 长度
        dedup_items: list[tuple[str, set[str], int]] = []

        for src in sorted(input_dir.glob("*.md")):
            original = src.read_text(encoding="utf-8")
            cleaned = self.clean_text(original)
            out = clean_dir / src.name
            write_text(out, cleaned)
            files.append(out)

            issues: list[str] = []
            if _looks_binary(_split_meta(original)[1]):
                issues.append("binary_or_unparsed_pdf")
            cleaned_body = _split_meta(cleaned)[1]
            body_len = len(cleaned_body)
            if body_len < self.config.min_content_length:
                issues.append("too_short")
            orig_size = len(original)
            quality[src.stem] = FileQuality(
                original_size=orig_size,
                cleaned_size=len(cleaned),
                score=round(len(cleaned) / orig_size, 3) if orig_size else 0.0,
                issues=issues,
            )
            # 只把"非过短"的纳入去重：避免空壳/全是元数据被错配
            if self.config.dedup_similarity < 1.0 and body_len >= self.config.min_content_length:
                dedup_items.append((src.stem, _shingles(cleaned_body), body_len))

        # MinHash 去重：相似组保留最长，其余从 clean/ 删除并标 dedup_of:<胜者>
        if dedup_items:
            losers = _dedup_groups(dedup_items, self.config.dedup_similarity)
            for loser_name, winner_name in losers.items():
                loser_path = clean_dir / f"{loser_name}.md"
                if loser_path.exists():
                    loser_path.unlink()
                files = [f for f in files if f.stem != loser_name]
                quality[loser_name].issues.append(f"dedup_of:{winner_name}")

        write_json(
            clean_dir / "quality.json",
            {k: v.model_dump() for k, v in quality.items()},
        )
        return CleanResult(files=files, quality_report=quality, clean_dir=clean_dir)

    async def filter_relevance(
        self,
        clean_result: CleanResult,
        llm: LLMClient,
        topic: str,
    ) -> CleanResult:
        """LLM 批量给 clean/*.md 打 0-1 相关性分；低于阈值的从 clean/ 删除
        （raw/ 保留以便溯源），quality.json 加 relevance_score 并标 low_relevance。

        失败回退：单批 LLM 异常 → 该批全部默认通过（不误杀），下批继续。
        """
        threshold = self.config.relevance_threshold
        batch_size = self.config.relevance_batch_size
        files = list(clean_result.files)
        kept: list[Path] = []
        quality = dict(clean_result.quality_report)

        for start in range(0, len(files), batch_size):
            batch = files[start : start + batch_size]
            entries = [_read_title_excerpt(p) for p in batch]
            prompt = (
                f"调研主题：「{topic}」。\n"
                f"下面是 {len(entries)} 份候选资料的标题与正文片段，请对每份判断"
                f"与主题的**相关性**，返回 0-1 浮点分（0=完全无关，1=高度相关）：\n\n"
                + "\n\n".join(f"[{i}] 《{t}》\n{e}" for i, (t, e) in enumerate(entries))
                + f"\n\n返回 JSON：{{scores:[{len(entries)} 个浮点数，顺序对应]}}。"
            )
            try:
                res = await llm.chat_structured(prompt, _Scores, system=_SCORE_SYSTEM)
                scores = list(res.scores)
            except Exception:  # noqa: BLE001 - 单批失败不阻断，全批默认通过
                scores = [1.0] * len(batch)

            for i, path in enumerate(batch):
                score = scores[i] if i < len(scores) else 1.0
                stem = path.stem
                if stem in quality:
                    q = quality[stem]
                    quality[stem] = q.model_copy(
                        update={
                            "issues": q.issues
                            + ([f"relevance:{round(score,2)}"] if score < threshold else []),
                        }
                    )
                if score < threshold:
                    if path.exists():
                        path.unlink()
                    if stem in quality:
                        quality[stem].issues.append("low_relevance")
                else:
                    kept.append(path)

        write_json(
            clean_result.clean_dir / "quality.json",
            {k: v.model_dump() for k, v in quality.items()},
        )
        return CleanResult(
            files=kept,
            quality_report=quality,
            clean_dir=clean_result.clean_dir,
        )


def clean(input_dir: Path, config: CleanerConfig, work_dir: Path) -> CleanResult:
    """模块级函数（01 §3.2 签名）。"""
    return Cleaner(config).process(input_dir, work_dir)
