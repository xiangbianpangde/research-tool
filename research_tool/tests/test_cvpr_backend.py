"""CvprBackend（DBLP）解析测试：固定 JSON fixture，不打真实网络。"""

import pytest

from research_tool.infrastructure.search.cvpr_backend import CvprBackend

_DBLP_JSON = {
    "result": {
        "hits": {
            "@total": "2",
            "hit": [
                {
                    "info": {
                        "title": "VGGT: Visual Geometry Grounded Transformer",
                        "venue": "CVPR",
                        "year": "2025",
                        "ee": "https://openaccess.thecvf.com/content/CVPR2025/html/vggt.html",
                        "authors": {
                            "author": [
                                {"text": "Alice Vision"},
                                {"text": "Bob Geometry"},
                            ]
                        },
                        "key": "conf/cvpr/vggt25",
                    }
                },
                {
                    "info": {
                        "title": "Unrelated NLP Paper",
                        "venue": "ACL",
                        "year": "2024",
                        "ee": "https://example.com/nlp",
                        "authors": {"author": {"text": "Carol NLP"}},
                        "key": "conf/acl/nlp24",
                    }
                },
                {
                    "info": {
                        "title": "A Study of Point Cloud Segmentation",
                        "venue": "CVPR",
                        "year": "2024",
                        "ee": "https://openaccess.thecvf.com/content/CVPR2024/papers/pc.pdf",
                        "authors": {"author": {"text": "Dan Point"}},
                        "key": "conf/cvpr/pc24",
                    }
                },
            ],
        }
    }
}


@pytest.mark.asyncio
async def test_cvpr_filters_venue_and_query(monkeypatch):
    calls: list[dict] = []

    async def fake(url, **kw):
        calls.append({"url": url, **kw})
        return _DBLP_JSON

    monkeypatch.setattr(
        "research_tool.infrastructure.search.cvpr_backend.get_json", fake
    )
    hits = await CvprBackend().search("VGGT geometry", 10, from_year=2025, to_year=2025)

    assert len(calls) == 1
    params = calls[0].get("params") or {}
    q = params.get("q", "")
    # 查询用关键词 + CVPR 文本锚 + year；venue 严格过滤在客户端
    assert "VGGT" in q or "geometry" in q.lower()
    assert "CVPR" in q
    assert "year:2025" in q

    # ACL 被 venue 闸掉；point cloud 因 query 词不重叠被滤；只留 VGGT
    assert len(hits) == 1
    h = hits[0]
    assert h.title.startswith("VGGT")
    assert "openaccess.thecvf.com" in h.url
    assert "2025" in h.snippet
    assert "Alice Vision" in h.snippet
    assert h.source_engine == "cvpr"


@pytest.mark.asyncio
async def test_cvpr_empty_query_keeps_cvpr_only(monkeypatch):
    async def fake(url, **kw):
        return _DBLP_JSON

    monkeypatch.setattr(
        "research_tool.infrastructure.search.cvpr_backend.get_json", fake
    )
    hits = await CvprBackend().search("", 10)
    assert len(hits) == 2  # 两篇 CVPR，ACL 丢弃
    assert all(h.source_engine == "cvpr" for h in hits)


@pytest.mark.asyncio
async def test_cvpr_respects_max_results(monkeypatch):
    async def fake(url, **kw):
        return _DBLP_JSON

    monkeypatch.setattr(
        "research_tool.infrastructure.search.cvpr_backend.get_json", fake
    )
    hits = await CvprBackend().search("", 1)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_cvpr_year_window_client_filter(monkeypatch):
    async def fake(url, **kw):
        return _DBLP_JSON

    monkeypatch.setattr(
        "research_tool.infrastructure.search.cvpr_backend.get_json", fake
    )
    # 跨年：不写单 year: 到 DBLP，客户端滤 2024–2025 → 两篇 CVPR
    hits = await CvprBackend().search("", 10, from_year=2024, to_year=2025)
    years = {h.snippet.split("\n")[0] for h in hits}
    assert years <= {"2024", "2025"}
    assert len(hits) == 2
