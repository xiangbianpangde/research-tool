from research_tool.common.slug import slugify


def test_english():
    assert slugify("Transformer Architecture!!") == "transformer-architecture"


def test_chinese_pinyin():
    assert slugify("大语言模型") == "da-yu-yan-mo-xing"


def test_mixed():
    assert slugify("Transformer 架构") == "transformer-jia-gou"


def test_empty():
    assert slugify("!!!") == "untitled"


def test_long_topic_truncated():
    # P2：超长主题不炸目录名；截断后仍含 hash 后缀
    long = "paper video matching " * 20
    s = slugify(long)
    assert len(s) <= 80
    assert s.count("-") >= 1
    # 同一主题稳定
    assert slugify(long) == s
