from src.common.slug import slugify


def test_english():
    assert slugify("Transformer Architecture!!") == "transformer-architecture"


def test_chinese_pinyin():
    assert slugify("大语言模型") == "da-yu-yan-mo-xing"


def test_mixed():
    assert slugify("Transformer 架构") == "transformer-jia-gou"


def test_empty():
    assert slugify("!!!") == "untitled"
