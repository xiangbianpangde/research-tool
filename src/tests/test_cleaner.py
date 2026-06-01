from src.domain.models import CleanerConfig
from src.infrastructure.stages.cleaner import Cleaner

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
