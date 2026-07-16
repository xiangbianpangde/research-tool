"""主题 → topic_slug 生成。

依据：05-数据流与文件规范.md §4。
- 英文 → 小写 + 连字符
- 中文 → 全拼转写（连字符分隔）
- 混合 → 英文与中文拼音拼接

注：设计文档示例 "Transformer 架构" → "transformer-architecture" 中的
"架构→architecture" 属语义翻译，超出无外部翻译服务的能力范围；本实现
按"中文转全拼"产出 "transformer-jia-gou"，保证确定性与可逆追溯。
"""

from __future__ import annotations

import hashlib
import re

try:
    from pypinyin import Style, lazy_pinyin

    _HAS_PINYIN = True
except ImportError:  # pragma: no cover - 退化路径
    _HAS_PINYIN = False


_NON_SLUG = re.compile(r"[^a-z0-9]+")
_CJK = re.compile(r"[一-鿿]")

# macOS 文件名上限 255 字节；主题过长（P2）时截断并挂短 hash，保证可辨且唯一。
_DEFAULT_MAX_LEN = 80


def slugify(topic: str, max_len: int = _DEFAULT_MAX_LEN) -> str:
    """把任意主题转为文件系统安全的 slug。

    保证：纯 ASCII、小写、连字符分隔、首尾无连字符、非空。
    超过 ``max_len`` 时截断并追加 8 位 sha1 后缀（P2 修复）。
    """
    if _CJK.search(topic) and _HAS_PINYIN:
        # 逐字转拼音，英文 token 原样保留（lazy_pinyin 对非汉字返回原串）
        parts = lazy_pinyin(topic, style=Style.NORMAL, errors="default")
        text = " ".join(parts)
    else:
        text = topic

    text = text.lower().strip()
    slug = _NON_SLUG.sub("-", text).strip("-")
    if not slug:
        return "untitled"
    if max_len > 0 and len(slug) > max_len:
        digest = hashlib.sha1(slug.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        # 留 1 连字符 + 8 hash
        keep = max(max_len - 9, 8)
        slug = slug[:keep].rstrip("-") + "-" + digest
    return slug
