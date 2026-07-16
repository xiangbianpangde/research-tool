"""X 源 P0/P1：preflight、max_results 传参、噪音过滤、互动门槛。"""

from __future__ import annotations

import pytest

from research_tool.domain.models import CollectorConfig
from research_tool.infrastructure.search.x_backend import XBackend, preflight_x
from research_tool.infrastructure.stages.collector import Collector


def test_preflight_missing_cli(monkeypatch):
    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.shutil.which", lambda c: None
    )
    cfg = CollectorConfig(x_backend="opencli", search_cache=False)
    pf = preflight_x(cfg, run_doctor=False)
    assert pf.ok is False
    assert "opencli" in pf.message.lower() or "安装" in pf.message
    assert "doctor" in pf.message.lower() or "twitter status" in pf.message.lower()


def test_install_hint_opencli_auto_cookie():
    """opencli 路径强调 cookie 自动获取，不要求手填到仓库。"""
    from research_tool.infrastructure.search import x_backend as xb

    assert "自动" in xb._INSTALL_HINT
    assert "opencli" in xb._INSTALL_HINT.lower()


def test_opencli_command_includes_max_results():
    cfg = CollectorConfig(x_backend="opencli", search_cache=False)
    x = XBackend(cfg)
    cmd = x._command("VGGT CVPR", 7)
    # opencli v1.8+ uses --limit (not -n)
    assert "--limit" in cmd
    assert "7" in cmd
    assert "-f" in cmd and "json" in cmd


def test_noise_and_engagement_filters():
    cfg = CollectorConfig(x_backend="opencli", x_min_engagement=10, search_cache=False)
    x = XBackend(cfg)
    assert x._is_noise("http://t.co/abc") is True
    assert x._is_noise("short") is True
    assert x._is_noise("Real discussion about VGGT geometry transformers at CVPR") is False
    assert x._engagement({"like_count": 3, "retweet_count": 2, "reply_count": 1}) == 6
    assert x._engagement({"text": "no metrics"}) is None


@pytest.mark.asyncio
async def test_search_filters_low_engagement(monkeypatch):
    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.shutil.which", lambda c: c
    )
    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.preflight_x",
        lambda cfg, run_doctor=True: type(
            "R",
            (),
            {
                "ok": True,
                "command": "opencli",
                "backend": "opencli",
                "message": "ok",
                "doctor_ok": True,
            },
        )(),
    )

    class R:
        returncode = 0
        stderr = ""
        stdout = """[
          {"id":"1","username":"a","text":"low engagement post about vggt research",
           "like_count":0,"retweet_count":0,"reply_count":0},
          {"id":"2","username":"b","text":"high engagement post about vggt geometry",
           "like_count":50,"retweet_count":10,"reply_count":5,
           "url":"https://x.com/b/status/2"}
        ]"""

    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.subprocess.run",
        lambda *a, **k: R(),
    )
    cfg = CollectorConfig(
        x_backend="opencli", x_min_engagement=20, search_cache=False
    )
    hits = await XBackend(cfg).search("vggt", 10)
    assert len(hits) == 1
    assert hits[0].url.endswith("/2")
    assert "engagement=" in hits[0].snippet


@pytest.mark.asyncio
async def test_collector_x_preflight_skips_engine(monkeypatch):
    """-s x 且 CLI 缺失时：warning 含安装说明，不把 x 打进任务列表。"""
    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.shutil.which", lambda c: None
    )
    cfg = CollectorConfig(
        search_engines=["x", "github"],
        search_cache=False,
        search_relevance_min_overlap=0.0,
    )
    c = Collector(cfg)

    async def fake_gh(self, query, max_results, language="both", **kw):
        from research_tool.infrastructure.search.base import SearchHit

        return [
            SearchHit(
                url="https://github.com/foo/bar",
                title="foo/bar",
                source_engine="github",
            )
        ]

    monkeypatch.setattr(
        "research_tool.infrastructure.search.github_backend.GitHubBackend.search",
        fake_gh,
    )
    sr = await c.search_only("vggt")
    assert any("X" in w or "opencli" in w.lower() or "安装" in w for w in sr.warnings)
    # github 仍有结果
    assert any(h.source_engine == "github" for h in sr.hits)


@pytest.mark.asyncio
async def test_thread_text_preferred(monkeypatch):
    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.preflight_x",
        lambda cfg, run_doctor=True: type(
            "R", (), {"ok": True, "command": "t", "backend": "opencli", "message": "ok", "doctor_ok": True}
        )(),
    )
    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.shutil.which", lambda c: c
    )

    class R:
        returncode = 0
        stderr = ""
        stdout = """[{
          "id": "9",
          "username": "lab",
          "text": "short",
          "full_text": "Longer full_text about VGGT paper thread part 1",
          "thread": [
            {"text": "part1 abstract"},
            {"text": "part2 method"}
          ]
        }]"""

    monkeypatch.setattr(
        "research_tool.infrastructure.search.x_backend.subprocess.run",
        lambda *a, **k: R(),
    )
    hits = await XBackend(CollectorConfig(search_cache=False)).search("vggt", 5)
    assert hits
    # full_text 优先于 text
    assert "Longer full_text" in hits[0].snippet or "part1" in hits[0].snippet
