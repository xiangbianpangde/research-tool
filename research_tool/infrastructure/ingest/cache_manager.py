"""M-004 缓存管理器（V1.1 VideoIngest）。

设计依据：[DD-001:M-004 缓存管理器]

双键：sha256(url) + etag（NFR4：同 URL 跳过转写）
存储：sqlite3（本地，零外部依赖）
写串行：asyncio.Lock + 装饰器模式（with_lock）
TTL 清理：默认 30 天（YouTube 固定 24h）

API：
- query_cache(url, etag="") -> Optional[CacheEntry]
- write_cache(entry) -> bool
- cleanup_expired(ttl_days=30) -> int
- compute_url_sha256(url) -> str

失败显式：E_CK_001 错误码登记，不抛错（降级返回 None/False）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ...common.logging_config import get_logger
from ...domain.errors import ErrorCode, register_error

logger = get_logger(__name__)

# 模块级默认
DEFAULT_CACHE_DIR = Path("./research-output/cache")
DEFAULT_TTL_DAYS = 30
YOUTUBE_TTL_DAYS = 1  # YouTube 固定 24h（满足 NFR4 实时性）
DEFAULT_DB_FILENAME = "video_cache.db"
_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CacheEntry:
    """单条缓存记录（DE-009）。"""

    url: str
    url_sha256: str
    etag: str
    payload: dict[str, Any]
    created_at: float
    platform: str = ""  # "youtube" / "bilibili" / ...
    ttl_days: int = DEFAULT_TTL_DAYS

    def is_expired(self, now: float | None = None) -> bool:
        """是否过期。"""
        now = now or time.time()
        return now - self.created_at > self.ttl_days * 86400

    def to_row(self) -> tuple:
        """序列化为 sqlite 行。"""
        return (
            self.url_sha256,
            self.etag,
            self.platform,
            self.ttl_days,
            self.created_at,
            json.dumps(self.payload, ensure_ascii=False, default=str),
            self.url,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row | tuple) -> "CacheEntry":
        """从 sqlite 行反序列化。"""
        return cls(
            url=row[6],
            url_sha256=row[0],
            etag=row[1],
            platform=row[2] or "",
            ttl_days=int(row[3]) if row[3] else DEFAULT_TTL_DAYS,
            created_at=float(row[4]),
            payload=json.loads(row[5]) if row[5] else {},
        )


# --------------------------------------------------------------------------- #
# CacheRepository（Repository 模式封装 sqlite3 CRUD）
# --------------------------------------------------------------------------- #


class CacheRepository:
    """sqlite3 缓存存储（Repository 模式）。

    - 连接管理：__enter__/__exit__（with 语法）
    - 模式初始化：init_schema()
    - 基础 CRUD：get / put / delete / cleanup_expired
    - 线程安全：每个连接独立（sqlite 默认 thread-local）
    """

    SCHEMA_SQL: ClassVar[str] = f"""
        CREATE TABLE IF NOT EXISTS video_cache (
            url_sha256 TEXT NOT NULL,
            etag TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            ttl_days INTEGER NOT NULL DEFAULT {DEFAULT_TTL_DAYS},
            created_at REAL NOT NULL,
            payload TEXT NOT NULL,
            url TEXT NOT NULL,
            PRIMARY KEY (url_sha256, etag)
        );
        CREATE INDEX IF NOT EXISTS idx_url_sha256 ON video_cache(url_sha256);
        CREATE INDEX IF NOT EXISTS idx_created_at ON video_cache(created_at);
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        """打开连接（幂等）。"""
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    def close(self) -> None:
        """关闭连接。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "CacheRepository":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("CacheRepository 未连接（需先 open() 或用 with 语句）")
        return self._conn

    def init_schema(self) -> None:
        """初始化 sqlite schema（幂等）。"""
        self.conn.executescript(self.SCHEMA_SQL)
        # 记录 schema 版本
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("version", str(_SCHEMA_VERSION)),
        )
        self.conn.commit()

    def get(self, url_sha256: str, etag: str = "") -> CacheEntry | None:
        """单键查询。"""
        cur = self.conn.execute(
            "SELECT * FROM video_cache WHERE url_sha256 = ? AND etag = ? LIMIT 1",
            (url_sha256, etag),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return CacheEntry.from_row(row)

    def put(self, entry: CacheEntry) -> bool:
        """写入或覆盖。"""
        try:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO video_cache
                    (url_sha256, etag, platform, ttl_days, created_at, payload, url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                entry.to_row(),
            )
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error("cache 写入失败: %s", e)
            return False

    def delete(self, url_sha256: str, etag: str = "") -> bool:
        """删除条目。"""
        try:
            cur = self.conn.execute(
                "DELETE FROM video_cache WHERE url_sha256 = ? AND etag = ?",
                (url_sha256, etag),
            )
            self.conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error as e:
            logger.error("cache 删除失败: %s", e)
            return False

    def cleanup_expired(self, ttl_days: int | None = None) -> int:
        """清理过期条目，返回删除数。

        Args:
            ttl_days: TTL 天数（None=用 entry.ttl_days 各自判断）

        Returns:
            删除条目数
        """
        now = time.time()
        if ttl_days is None:
            # 各自用 entry.ttl_days
            cur = self.conn.execute("SELECT * FROM video_cache")
            to_delete = []
            for row in cur.fetchall():
                entry = CacheEntry.from_row(row)
                if entry.is_expired(now):
                    to_delete.append((entry.url_sha256, entry.etag))
        else:
            cutoff = now - ttl_days * 86400
            cur = self.conn.execute(
                "SELECT url_sha256, etag FROM video_cache WHERE created_at < ?",
                (cutoff,),
            )
            to_delete = [(r[0], r[1]) for r in cur.fetchall()]

        if not to_delete:
            return 0
        try:
            self.conn.executemany(
                "DELETE FROM video_cache WHERE url_sha256 = ? AND etag = ?",
                to_delete,
            )
            self.conn.commit()
            return len(to_delete)
        except sqlite3.Error as e:
            logger.error("cache 清理失败: %s", e)
            return 0

    def count(self) -> int:
        """当前条目数。"""
        cur = self.conn.execute("SELECT COUNT(*) FROM video_cache")
        return int(cur.fetchone()[0])


# --------------------------------------------------------------------------- #
# CacheManager（高阶 API：query / write / cleanup + asyncio.Lock）
# --------------------------------------------------------------------------- #


class CacheManager:
    """视频缓存管理器（Repository + LockManager + 高阶 API）。

    用法：
        mgr = CacheManager()
        await mgr.init()
        entry = await mgr.query(url, etag)
        await mgr.write(CacheEntry(...))
        await mgr.cleanup(ttl_days=30)
        await mgr.close()
    """

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        db_filename: str = DEFAULT_DB_FILENAME,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.db_path = self.cache_dir / db_filename
        self._repo: CacheRepository | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    @property
    def repo(self) -> CacheRepository:
        if self._repo is None:
            raise RuntimeError("CacheManager 未连接（需先 await init()）")
        return self._repo

    async def init(self) -> None:
        """初始化：建目录 + 建表。幂等。"""
        if self._initialized:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._init_sync)
        self._initialized = True

    def _init_sync(self) -> None:
        """同步初始化（在线程池跑）。"""
        repo = CacheRepository(self.db_path)
        repo.open()
        repo.init_schema()
        self._repo = repo

    async def query(
        self,
        url: str,
        etag: str = "",
    ) -> CacheEntry | None:
        """查询缓存（IC-009）。

        Args:
            url: 视频 URL
            etag: ETag/Last-Modified（默认空）

        Returns:
            CacheEntry 命中；None 未命中或 DB 不可用

        Note:
            E_CK_001 触发降级返回 None，不抛错
        """
        if not self._initialized:
            await self.init()
        url_sha = compute_url_sha256(url)
        loop = asyncio.get_running_loop()
        try:
            entry: CacheEntry | None = await loop.run_in_executor(
                None, self.repo.get, url_sha, etag
            )
        except (sqlite3.Error, OSError) as e:
            register_error(
                ErrorCode.E_CK_001_CACHE_DB_UNAVAILABLE.value,
                scene="缓存查询失败",
                cause=str(e),
                suggestion="检查 cache_dir 权限或磁盘空间",
                context={"db": str(self.db_path)},
            )
            return None
        if entry is not None:
            if entry.is_expired():
                logger.debug("cache 命中但已过期: %s", url_sha[:12])
                return None
            logger.info("cache 命中: %s", url_sha[:12])
        return entry

    async def write(self, entry: CacheEntry) -> bool:
        """写入缓存（IC-010）。

        Args:
            entry: CacheEntry（必填 url_sha256/etag/payload/created_at）

        Returns:
            True 成功；False 失败（重试 1 次后仍失败）

        Note:
            写锁串行化 + UNIQUE 约束
        """
        if not self._initialized:
            await self.init()
        async with self._lock:
            loop = asyncio.get_running_loop()
            for attempt in (1, 2):
                try:
                    ok: bool = await loop.run_in_executor(
                        None, self.repo.put, entry
                    )
                    if ok:
                        logger.info(
                            "cache 写入: %s etag=%r (%d bytes)",
                            entry.url_sha256[:12], entry.etag, len(json.dumps(entry.payload)),
                        )
                        return True
                except (sqlite3.Error, OSError) as e:
                    if attempt == 2:
                        register_error(
                            ErrorCode.E_CK_001_CACHE_DB_UNAVAILABLE.value,
                            scene="缓存写入失败",
                            cause=str(e),
                            suggestion="重试或更换 cache_dir",
                            context={"db": str(self.db_path), "url_sha": entry.url_sha256[:12]},
                        )
                        return False
                    await asyncio.sleep(0.1)
        return False

    async def cleanup(self, ttl_days: int = DEFAULT_TTL_DAYS) -> int:
        """清理过期条目（IC-011）。

        Args:
            ttl_days: 统一 TTL 天数（覆盖 entry 各自的 ttl_days）

        Returns:
            删除条目数
        """
        if not self._initialized:
            await self.init()
        loop = asyncio.get_running_loop()
        try:
            count: int = await loop.run_in_executor(
                None, self.repo.cleanup_expired, ttl_days
            )
            if count > 0:
                logger.info("cache 清理: 删除 %d 条过期条目（TTL=%d 天）", count, ttl_days)
            return count
        except (sqlite3.Error, OSError) as e:
            register_error(
                ErrorCode.E_CK_001_CACHE_DB_UNAVAILABLE.value,
                scene="缓存清理失败",
                cause=str(e),
                suggestion="后台任务，失败不阻塞主链",
            )
            return 0

    async def close(self) -> None:
        """关闭连接。"""
        if self._repo is not None:
            self._repo.close()
        self._repo = None
        self._initialized = False


# --------------------------------------------------------------------------- #
# 模块级工具函数
# --------------------------------------------------------------------------- #


def compute_url_sha256(url: str) -> str:
    """URL → sha256 前缀（用于缓存键）。

    Args:
        url: 原始 URL

    Returns:
        "sha256:" + 64 位 hex
    """
    return "sha256:" + hashlib.sha256(url.encode("utf-8")).hexdigest()


# 单例 + 便捷函数（向后兼容 + 简易使用）
_MANAGER_SINGLETON: CacheManager | None = None
_SINGLETON_LOCK = asyncio.Lock()


async def get_manager(
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> CacheManager:
    """获取全局单例 CacheManager。"""
    global _MANAGER_SINGLETON
    async with _SINGLETON_LOCK:
        if _MANAGER_SINGLETON is None:
            _MANAGER_SINGLETON = CacheManager(cache_dir=cache_dir)
            await _MANAGER_SINGLETON.init()
        return _MANAGER_SINGLETON


async def query_cache(
    url: str,
    etag: str = "",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> CacheEntry | None:
    """模块级便捷函数：查询缓存。"""
    mgr = await get_manager(cache_dir)
    return await mgr.query(url, etag)


async def write_cache(
    entry: CacheEntry,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> bool:
    """模块级便捷函数：写入缓存。"""
    mgr = await get_manager(cache_dir)
    return await mgr.write(entry)


async def cleanup_expired(
    ttl_days: int = DEFAULT_TTL_DAYS,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> int:
    """模块级便捷函数：清理过期条目。"""
    mgr = await get_manager(cache_dir)
    return await mgr.cleanup(ttl_days)


def reset_singleton() -> None:
    """重置单例（测试用）。"""
    global _MANAGER_SINGLETON
    _MANAGER_SINGLETON = None


__all__ = [
    "CacheEntry",
    "CacheRepository",
    "CacheManager",
    "compute_url_sha256",
    "get_manager",
    "query_cache",
    "write_cache",
    "cleanup_expired",
    "reset_singleton",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_TTL_DAYS",
    "YOUTUBE_TTL_DAYS",
]
