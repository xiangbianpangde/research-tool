"""M-004 缓存管理器单元测试。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from research_tool.infrastructure.ingest.cache_manager import (
    CacheEntry,
    CacheManager,
    CacheRepository,
    cleanup_expired,
    compute_url_sha256,
    query_cache,
    reset_singleton,
    write_cache,
)


class TestComputeUrlSha256:
    """URL → sha256 工具函数。"""

    def test_returns_sha256_prefix(self):
        h = compute_url_sha256("https://example.com/v")
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 64

    def test_deterministic(self):
        a = compute_url_sha256("https://x.com/a")
        b = compute_url_sha256("https://x.com/a")
        assert a == b

    def test_different_urls_differ(self):
        a = compute_url_sha256("https://x.com/a")
        b = compute_url_sha256("https://x.com/b")
        assert a != b


class TestCacheEntry:
    """CacheEntry 数据类。"""

    def test_construction(self):
        e = CacheEntry(
            url="https://x.com/v",
            url_sha256=compute_url_sha256("https://x.com/v"),
            etag="",
            payload={"k": "v"},
            created_at=time.time(),
        )
        assert e.payload == {"k": "v"}
        assert e.platform == ""
        assert e.ttl_days == 30

    def test_is_expired_false_when_fresh(self):
        e = CacheEntry(
            url="u",
            url_sha256="h",
            etag="",
            payload={},
            created_at=time.time(),
        )
        assert e.is_expired() is False

    def test_is_expired_true_when_old(self):
        e = CacheEntry(
            url="u",
            url_sha256="h",
            etag="",
            payload={},
            created_at=time.time() - 100 * 86400,
            ttl_days=1,
        )
        assert e.is_expired() is True

    def test_serialization_roundtrip(self):
        e = CacheEntry(
            url="u",
            url_sha256="h",
            etag="e1",
            payload={"k": [1, 2, 3]},
            created_at=time.time(),
        )
        row = e.to_row()
        e2 = CacheEntry.from_row(row)
        assert e.url == e2.url
        assert e.etag == e2.etag
        assert e.payload == e2.payload


class TestCacheRepository:
    """CacheRepository CRUD（用 :memory: 测试）。"""

    def _make_repo(self) -> CacheRepository:
        repo = CacheRepository(":memory:")
        with repo:
            repo.init_schema()
        # CacheRepository 设计为 with 一次性；这里用文件以便后续操作
        return repo

    def test_init_schema_creates_table(self, tmp_path: Path):
        db = tmp_path / "test.db"
        repo = CacheRepository(db)
        with repo:
            repo.init_schema()
            # 表存在
            cur = repo.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='video_cache'"
            )
            assert cur.fetchone() is not None

    def test_put_and_get(self, tmp_path: Path):
        db = tmp_path / "test.db"
        repo = CacheRepository(db)
        with repo:
            repo.init_schema()
            e = CacheEntry(
                url="https://x.com/v1",
                url_sha256=compute_url_sha256("https://x.com/v1"),
                etag="v1",
                payload={"title": "T"},
                created_at=time.time(),
            )
            assert repo.put(e) is True
            got = repo.get(e.url_sha256, e.etag)
            assert got is not None
            assert got.payload == {"title": "T"}

    def test_get_missing_returns_none(self, tmp_path: Path):
        db = tmp_path / "test.db"
        repo = CacheRepository(db)
        with repo:
            repo.init_schema()
            assert repo.get("nonexistent", "") is None

    def test_delete(self, tmp_path: Path):
        db = tmp_path / "test.db"
        repo = CacheRepository(db)
        with repo:
            repo.init_schema()
            e = CacheEntry(
                url="u",
                url_sha256="h",
                etag="",
                payload={},
                created_at=time.time(),
            )
            repo.put(e)
            assert repo.delete(e.url_sha256, e.etag) is True
            assert repo.get(e.url_sha256, e.etag) is None

    def test_cleanup_expired(self, tmp_path: Path):
        db = tmp_path / "test.db"
        repo = CacheRepository(db)
        with repo:
            repo.init_schema()
            # 老的
            old = CacheEntry(
                url="u1",
                url_sha256="h1",
                etag="",
                payload={},
                created_at=time.time() - 100 * 86400,
                ttl_days=1,
            )
            # 新的
            new = CacheEntry(
                url="u2",
                url_sha256="h2",
                etag="",
                payload={},
                created_at=time.time(),
            )
            repo.put(old)
            repo.put(new)
            deleted = repo.cleanup_expired()  # 用各自 ttl_days
            assert deleted == 1
            assert repo.get("h1", "") is None
            assert repo.get("h2", "") is not None

    def test_count(self, tmp_path: Path):
        db = tmp_path / "test.db"
        repo = CacheRepository(db)
        with repo:
            repo.init_schema()
            assert repo.count() == 0
            for i in range(3):
                repo.put(
                    CacheEntry(
                        url=f"u{i}",
                        url_sha256=f"h{i}",
                        etag="",
                        payload={},
                        created_at=time.time(),
                    )
                )
            assert repo.count() == 3


class TestCacheManagerAsync:
    """CacheManager 异步 API。"""

    @pytest.mark.asyncio
    async def test_init_creates_db(self, tmp_path: Path):
        reset_singleton()
        mgr = CacheManager(cache_dir=tmp_path)
        await mgr.init()
        assert (tmp_path / "video_cache.db").exists()
        await mgr.close()

    @pytest.mark.asyncio
    async def test_query_miss(self, tmp_path: Path):
        reset_singleton()
        mgr = CacheManager(cache_dir=tmp_path)
        await mgr.init()
        result = await mgr.query("https://x.com/nonexistent")
        assert result is None
        await mgr.close()

    @pytest.mark.asyncio
    async def test_write_then_query(self, tmp_path: Path):
        reset_singleton()
        mgr = CacheManager(cache_dir=tmp_path)
        await mgr.init()
        e = CacheEntry(
            url="https://x.com/v1",
            url_sha256=compute_url_sha256("https://x.com/v1"),
            etag="e1",
            payload={"title": "Video 1"},
            created_at=time.time(),
        )
        ok = await mgr.write(e)
        assert ok is True
        got = await mgr.query("https://x.com/v1", "e1")
        assert got is not None
        assert got.payload["title"] == "Video 1"
        await mgr.close()

    @pytest.mark.asyncio
    async def test_query_expired_returns_none(self, tmp_path: Path):
        reset_singleton()
        mgr = CacheManager(cache_dir=tmp_path)
        await mgr.init()
        e = CacheEntry(
            url="https://x.com/old",
            url_sha256=compute_url_sha256("https://x.com/old"),
            etag="",
            payload={},
            created_at=time.time() - 100 * 86400,  # 100 天前
            ttl_days=1,
        )
        await mgr.write(e)
        got = await mgr.query("https://x.com/old", "")
        # 过期 → 视为 miss（不返回）
        assert got is None
        await mgr.close()

    @pytest.mark.asyncio
    async def test_cleanup(self, tmp_path: Path):
        reset_singleton()
        mgr = CacheManager(cache_dir=tmp_path)
        await mgr.init()
        # 写入一条过期的
        e = CacheEntry(
            url="https://x.com/old",
            url_sha256=compute_url_sha256("https://x.com/old"),
            etag="",
            payload={},
            created_at=time.time() - 100 * 86400,
            ttl_days=1,
        )
        await mgr.write(e)
        count = await mgr.cleanup(ttl_days=30)
        assert count >= 1
        await mgr.close()


class TestModuleLevel:
    """模块级便捷函数。"""

    @pytest.mark.asyncio
    async def test_query_and_write(self, tmp_path: Path):
        reset_singleton()
        url = "https://x.com/module-test"
        # 写入
        e = CacheEntry(
            url=url,
            url_sha256=compute_url_sha256(url),
            etag="",
            payload={"data": "hello"},
            created_at=time.time(),
        )
        ok = await write_cache(e, cache_dir=tmp_path)
        assert ok is True
        # 查询
        got = await query_cache(url, cache_dir=tmp_path)
        assert got is not None
        assert got.payload["data"] == "hello"

    @pytest.mark.asyncio
    async def test_cleanup_expired_function(self, tmp_path: Path):
        reset_singleton()
        count = await cleanup_expired(ttl_days=30, cache_dir=tmp_path)
        assert count >= 0  # 不抛错
