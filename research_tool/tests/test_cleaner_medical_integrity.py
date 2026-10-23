"""Scientific comparisons are source text, not HTML spanning unrelated paragraphs."""

from research_tool.domain.models import CleanerConfig
from research_tool.infrastructure.stages.cleaner import Cleaner


def cleaner():
    return Cleaner(CleanerConfig(strip_nav=False, strip_ads=False, find_content_start=False))


def test_html_cleanup_preserves_numeric_comparisons_between_distant_angle_brackets():
    text = ("Hypodysfibrinogenemia has fibrinogen activity/antigen <0.7.\n\n"
            "A different paragraph records severe bleeding and 19/89 deliveries.\n\n"
            "Women above the typical fertile age (>50 years) were included.")
    result = cleaner().clean_text(text)
    assert result == text


def test_hidden_html_comments_are_removed_without_erasing_visible_source_or_metadata():
    text = ("<!-- source: https://example.test/study -->\n\n"
            "Observed P < .05.<!-- private hidden claim\nacross lines --> Visible result.")
    result = cleaner().clean_text(text)
    assert result == ("<!-- source: https://example.test/study -->\n\n"
                      "Observed P < .05. Visible result.")
    assert "private hidden" not in result


def test_inline_html_is_removed_without_erasing_pvalues_or_inequalities():
    text = "<p>The <em>adjusted</em> OR was 5.8, P < .03; ratio < 0.7 and age > 50.</p>"
    result = cleaner().clean_text(text)
    assert result == "The adjusted OR was 5.8, P < .03; ratio < 0.7 and age > 50."


def test_short_clinical_table_is_not_misclassified_as_a_navigation_block():
    text = ("<table><tr><th>Group</th><th>PPH</th></tr>"
            "<tr><td>Dys</td><td>12/34</td></tr>"
            "<tr><td>Hypo</td><td>4/11</td></tr></table>")
    result = Cleaner().clean_text(text)
    assert "Group | PPH" in result
    assert "Dys | 12/34" in result
    assert "Hypo | 4/11" in result


def test_html_table_cells_and_rows_do_not_merge_clinically_different_values():
    text = ("<table><tr><th>Group</th><th>PPH</th><th>Live births</th></tr>"
            "<tr><td>Dys</td><td>12</td><td>34</td></tr>"
            "<tr><td>Hypo</td><td>4</td><td>11</td></tr></table>")
    result = cleaner().clean_text(text)
    assert "Group | PPH | Live births" in result
    assert "Dys | 12 | 34" in result
    assert "Hypo | 4 | 11" in result
    assert result.index("Dys") < result.index("Hypo")
    assert "Dys1234" not in result
