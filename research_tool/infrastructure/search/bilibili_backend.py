"""Bilibili 视频搜索后端（V1.1 配套 VideoIngest）。

设计：直接调用 B 站 web 搜索 API（`/x/web-interface/search/type`），先访问主页拿
`buvid3` cookie warm-up，再发起搜索请求。比 yt-dlp BiliSearch extractor 稳定得多
（后者 2025+ 起被 B 站 wbi 反爬常态化，频繁返回 HTTP 412）。

为什么做这个：项目原 10 个搜索后端没有 B 站源，导致"宋浩 多元函数"这种
明显应该走视频的主题，Collect 阶段拿不到 BV/av 号，下游 VideoIngest 无法
自动被触发。补这个口让"主题 → 自治视频笔记"链路真正闭合（GAP-V1）。
"""

from __future__ import annotations

import asyncio
import re

import httpx

from ...common.logging_config import get_logger
from ...domain.errors import SearchError
from .base import SearchBackend, SearchHit

logger = get_logger(__name__)

_SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"
_WARMUP_URL = "https://www.bilibili.com/"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_HTTP_TIMEOUT_SEC = 15.0

# B 站 title 字段含 <em class="keyword">...</em> 高亮，剥掉。
_HL_RE = re.compile(r"</?em[^>]*>")


class BilibiliBackend(SearchBackend):
    """B 站视频搜索（走 web search API + buvid3 warmup）。"""

    name = "bilibili"

    def __init__(self) -> None:
        # 共享 client，复用 cookie；首次 search 时 lazy warmup
        self._client: httpx.AsyncClient | None = None
        self._warmed: bool = False
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=_DEFAULT_HEADERS,
                timeout=_HTTP_TIMEOUT_SEC,
                follow_redirects=True,
            )
        return self._client

    async def _warmup(self) -> None:
        """访问 bilibili.com 主页，让服务器下发 buvid3 cookie。线程安全。"""
        if self._warmed:
            return
        async with self._lock:
            if self._warmed:
                return
            client = await self._get_client()
            try:
                await client.get(_WARMUP_URL)
                self._warmed = True
            except Exception as e:  # noqa: BLE001
                logger.warning("Bilibili warmup 失败（继续）: %s", e)

    async def search(
        self,
        query: str,
        max_results: int,
        language: str = "both",
        **_kw,
    ) -> list[SearchHit]:
        await self._warmup()
        client = await self._get_client()
        params = {
            "search_type": "video",
            "keyword": query,
            "page": 1,
        }
        try:
            resp = await client.get(_SEARCH_URL, params=params)
        except httpx.HTTPError as e:
            raise SearchError(f"Bilibili 搜索 HTTP 失败: {e}") from e
        if resp.status_code != 200:
            raise SearchError(f"Bilibili 搜索 HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as e:
            raise SearchError(f"Bilibili 响应非 JSON: {resp.text[:200]}") from e

        code = data.get("code", -1)
        if code != 0:
            # code -412 = 反爬触发；code -101 = 未登录；都视为搜索失败
            raise SearchError(f"Bilibili 搜索业务码 {code}: {data.get('message')}")

        results = (data.get("data") or {}).get("result") or []
        hits: list[SearchHit] = []
        for it in results[:max_results]:
            bvid = it.get("bvid") or ""
            aid = it.get("aid") or ""
            # 优先 BV 号，没有就 av 号；都没有就跳过（多半是付费课程/番剧）
            if bvid:
                url = f"https://www.bilibili.com/video/{bvid}"
            elif aid:
                url = f"https://www.bilibili.com/video/av{aid}"
            else:
                continue
            raw_title = it.get("title") or ""
            title = _HL_RE.sub("", raw_title)
            author = it.get("author") or ""
            duration = it.get("duration") or ""  # 形如 "62:48"
            desc = (it.get("description") or "")[:200]
            snippet_parts: list[str] = []
            if author:
                snippet_parts.append(f"UP主: {author}")
            if duration:
                snippet_parts.append(f"时长 {duration}")
            if desc:
                snippet_parts.append(desc)
            hits.append(
                SearchHit(
                    url=url,
                    title=title,
                    snippet=" | ".join(snippet_parts),
                    source_engine=self.name,
                )
            )
        return hits

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
