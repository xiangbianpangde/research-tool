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
