from src.infrastructure.stages.base import domain_of, safe_filename


def test_preserves_cjk():
    assert safe_filename("离散数学") == "离散数学"
    assert safe_filename("v1.5 9.3群格布尔代数") == "v1.5-9.3群格布尔代数"


def test_lowercases_ascii_and_keeps_dot_hyphen():
    assert safe_filename("Example.COM") == "example.com"
    assert safe_filename("arxiv.org") == "arxiv.org"


def test_strips_illegal_fs_chars():
    assert "/" not in safe_filename("a/b:c*d?")
    assert "|" not in safe_filename("x|y<z>")


def test_empty_fallback():
    assert safe_filename("///") == "file"
    assert safe_filename("   ") == "file"


def test_domain_still_works():
    assert domain_of("https://www.example.com/x") == "example.com"
