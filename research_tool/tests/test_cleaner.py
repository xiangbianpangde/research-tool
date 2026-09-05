import pytest

from research_tool.domain.models import CleanerConfig
from research_tool.infrastructure.stages.cleaner import Cleaner

NOISY = """<!-- source: https://x.com/a -->
<!-- title: Test -->

Home
About
Contact
Login
<script>var x=1;</script>
advertisement: buy now
This is the first real substantial paragraph of the article that clearly exceeds the content start threshold so it becomes the body start point and everything above is removed.
More body text continuing the discussion with enough substance to keep.
## References
[1] some ref
"""


def test_full_clean():
    out = Cleaner(CleanerConfig()).clean_text(NOISY)
    assert "Home" not in out and "Login" not in out  # nav
    assert "advertisement" not in out  # ad
    assert "var x" not in out  # script
    assert "References" not in out  # tail truncation
    assert "first real substantial" in out  # content kept
    assert out.startswith("<!-- source")  # meta header preserved


def test_disable_all():
    cfg = CleanerConfig(
        strip_html=False, strip_nav=False, strip_ads=False, find_content_start=False
    )
    out = Cleaner(cfg).clean_text(NOISY)
    assert "Home" in out  # nav kept when disabled


def test_clean_strips_base64_data_uris():
    noisy_with_image = (
        "<!-- title: Image Test -->\n\n"
        "This is a real substantial paragraph that has enough length to qualify as the start of the body content for this document.\n"
        "![diagram](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==)\n"
        "Following discussion after the stripped embedded base64 image data."
    )
    out = Cleaner(CleanerConfig()).clean_text(noisy_with_image)
    assert "data:image/png;base64" not in out
    assert "[embedded image removed]" in out
    assert "Following discussion" in out


def test_clean_strips_cookie_banners():
    noisy_with_cookie = (
        "<!-- title: Cookie Test -->\n\n"
        "This is a real substantial paragraph that has enough length to qualify as the start of the body content for this document.\n"
        "We use cookies to improve your experience. Accept all cookies\n"
        "Important research findings that should definitely be kept in the output."
    )
    out = Cleaner(CleanerConfig()).clean_text(noisy_with_cookie)
    assert "Accept all cookies" not in out
    assert "Important research findings" in out


def test_clean_truncates_oversized_content():
    huge_body = (
        "<!-- title: Huge Test -->\n\n"
        + ("Substantial paragraph with facts and data points. " * 50 + "\n\n") * 20
    )
    cfg = CleanerConfig(max_content_length=1500)
    out = Cleaner(cfg).clean_text(huge_body)
    assert len(out) < 2000
    assert "truncated to max_content_length" in out


def test_clean_truncation_does_not_overtruncate_when_newline_is_early():
    """Sol 终审反例验证：当 50k 之前唯一的换行很靠前时，严禁无限向前回退导致正文被清空。"""
    # 前 10 字符有唯一换行，后续 60,000 字符为长段文本无任何换行
    body_content = "Hello\n" + ("A" * 60000)
    cfg = CleanerConfig(max_content_length=50000)
    out = Cleaner(cfg).clean_text(body_content)
    # 必须保留接近 50,000 字符，绝对不能退回 index 5 只剩下 'Hello'
    assert len(out) >= 49000
    assert "truncated to max_content_length" in out


def test_clean_dedup_similarity_zero_disables_deduplication(tmp_path):
    """Sol 终审 P1 验证：dedup_similarity=0 表示关闭去重，严禁误将所有文档合并。"""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    doc1 = raw_dir / "01.md"
    doc2 = raw_dir / "02.md"
    content = "Substantial text paragraph with distinct data and keywords. " * 10
    doc1.write_text(content, encoding="utf-8")
    doc2.write_text(content, encoding="utf-8")

    cfg = CleanerConfig(min_content_length=50, dedup_similarity=0.0)
    res = Cleaner(cfg).process(raw_dir, tmp_path)
    # 两篇相同内容的文档均应保留，不触发任何 dedup 合并
    assert len(res.files) == 2
    assert (tmp_path / "clean" / "01.md").exists()
    assert (tmp_path / "clean" / "02.md").exists()


@pytest.mark.asyncio
async def test_clean_filter_relevance_fail_closed(tmp_path):
    """Sol 终审 P1 验证：默认 fail_closed，当 LLM 抛出异常时抛出 StageError，严禁无脑返回 1.0 放行脏数据。"""
    import pytest
    from research_tool.domain.errors import StageError
    from research_tool.domain.models import CleanResult, FileQuality
    from research_tool.infrastructure.llm.mock import MockLLMClient

    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    p = clean_dir / "01.md"
    p.write_text("content", encoding="utf-8")
    cr = CleanResult(files=[p], quality_report={"01": FileQuality(original_size=7, cleaned_size=7, score=1)}, clean_dir=clean_dir)

    class FailingLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            raise RuntimeError("MiniMax 429 RateLimit")

    with pytest.raises(StageError, match="避免 fail-open"):
        await Cleaner(CleanerConfig(relevance_fail_open=False)).filter_relevance(cr, FailingLLM(), "topic")


@pytest.mark.asyncio
async def test_clean_filter_relevance_rejects_cardinality_mismatch(tmp_path):
    """Sol 终审 P0-A 闭环：LLM 返回打分数量与批次不匹配时，fail-closed 拒绝放行未评分文档。"""
    from research_tool.domain.errors import StageError
    from research_tool.domain.models import CleanResult, FileQuality
    from research_tool.infrastructure.llm.mock import MockLLMClient

    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    p1 = clean_dir / "01.md"
    p2 = clean_dir / "02.md"
    p1.write_text("doc1", encoding="utf-8")
    p2.write_text("doc2", encoding="utf-8")
    cr = CleanResult(
        files=[p1, p2],
        quality_report={
            "01": FileQuality(original_size=4, cleaned_size=4, score=1),
            "02": FileQuality(original_size=4, cleaned_size=4, score=1),
        },
        clean_dir=clean_dir,
    )

    class IncompleteLLM(MockLLMClient):
        async def chat_structured(self, prompt, schema, system=None):
            # 2 篇文档仅返回 1 个分数（反例）
            return schema(scores=[0.1])

    with pytest.raises(StageError, match="返回数量不匹配"):
        await Cleaner(CleanerConfig(relevance_fail_open=False)).filter_relevance(cr, IncompleteLLM(), "topic")
