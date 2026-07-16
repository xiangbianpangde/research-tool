"""Security regressions for rendered configuration output."""

import pytest

from research_tool.presentation.cli import _redact_config_data


def test_redaction_hides_proxy_credentials_and_tokens_without_mutating_input():
    data = {
        "llm": {"api_key": "llm-secret"},
        "collector": {
            "proxy": "http://alice:password@proxy.example:8080",
            "github_token": "github-secret",
        },
    }

    redacted = _redact_config_data(data)

    assert redacted["llm"]["api_key"] == "*" * 3
    assert redacted["collector"]["github_token"] == "*" * 3
    assert redacted["collector"]["proxy"] == "http://proxy.example:8080"
    assert data["collector"]["proxy"] == "http://alice:password@proxy.example:8080"


def test_redaction_sanitizes_credentials_in_all_nested_urls():
    data = {
        "llm": {
            "base_url": "https://alice:password@llm.example/v1?api_key=query-secret&mode=fast"
        },
        "collector": {
            "official_urls": [
                "https://papers.example/item?signature=signed-secret&id=42",
                "https://papers.example/public?id=7",
            ]
        },
    }

    redacted = _redact_config_data(data)
    rendered = repr(redacted)

    assert "password" not in rendered
    assert "query-secret" not in rendered
    assert "signed-secret" not in rendered
    assert "mode=fast" in redacted["llm"]["base_url"]
    assert "id=42" in redacted["collector"]["official_urls"][0]
    assert redacted["collector"]["official_urls"][1].endswith("?id=7")


@pytest.mark.parametrize(
    "key",
    ["key", "sig", "authorization", "AWSAccessKeyId", "x-amz-credential"],
)
def test_redaction_uses_shared_sensitive_url_key_policy(key):
    marker = "MUST-NOT-LEAK"
    redacted = _redact_config_data(
        {"llm": {"base_url": f"https://api.example/v1?{key}={marker}&mode=fast"}}
    )

    assert marker not in redacted["llm"]["base_url"]
    assert "mode=fast" in redacted["llm"]["base_url"]
