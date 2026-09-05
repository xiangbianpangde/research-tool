"""Tavily 搜索后端（需 API Key，稳定高质量）。

依据决策：搜索后端含 Tavily。Key 来自 CollectorConfig.tavily_api_key
（可由 ${TAVILY_API_KEY} 注入）。

支持多 key 轮询：api_key 可以是逗号/分号分隔的多个 key（或通过环境变量
TAVILY_KEYS 提供额外 key）。当某个 key 触发 Tavily 配额限制
（UsageLimitExceededError，HTTP 429/432/433）时，自动轮换到下一个 key
重试；所有 key 均耗尽才抛 SearchError。单 key 时行为与之前完全一致。
"""

from __future__ import annotations

import asyncio
import os
import threading

from ...domain.errors import SearchError
from ._http import get_default_proxy
from .base import SearchBackend, SearchHit


def _split_keys(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [k.strip() for k in raw.replace(";", ",").split(",") if k.strip()]


class TavilyBackend(SearchBackend):
    name = "tavily"

    def __init__(self, api_key: str | None) -> None:
        pool = _split_keys(api_key)
        for extra in _split_keys(os.environ.get("TAVILY_KEYS")):
            if extra not in pool:
                pool.append(extra)
        if not pool:
            raise SearchError(
                "Tavily 后端需要 api_key：在 config.yaml 设 collector.tavily_api_key "
                "或环境变量 TAVILY_API_KEY"
            )
        self._keys = pool
        self._idx = 0
        self._lock = threading.Lock()
        self._exhausted_keys: set[str] = set()
        self._inflight: dict[str, int] = {k: 0 for k in pool}

    def _search_with_key(self, key: str, query: str, max_results: int) -> list[SearchHit]:
        from tavily import TavilyClient  # noqa: PLC0415

        proxy = get_default_proxy()
        client = TavilyClient(
            api_key=key,
            proxies={"http": proxy, "https": proxy} if proxy else None,
        )
        resp = client.search(query=query, max_results=max_results)
        hits: list[SearchHit] = []
        for r in resp.get("results", []):
            url = r.get("url", "")
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=r.get("title", ""),
                    snippet=r.get("content", ""),
                    source_engine=self.name,
                )
            )
        return hits

    def _search_sync(self, query: str, max_results: int) -> list[SearchHit]:
        from tavily import UsageLimitExceededError  # noqa: PLC0415

        last_err: Exception | None = None
        while True:
            with self._lock:
                available = [k for k in self._keys if k not in self._exhausted_keys]
                if not available:
                    break
                # 原子 in-flight 预占（P1-D 闭环）：优先分配在途并发最少的 key，高并发下分散负载
                available.sort(key=lambda k: self._inflight.get(k, 0))
                key = available[0]
                self._inflight[key] = self._inflight.get(key, 0) + 1

            try:
                return self._search_with_key(key, query, max_results)
            except UsageLimitExceededError as e:
                last_err = e
                with self._lock:
                    # 发生 429/超额立即原子加入黑名单，后续所有并发线程绝不会再撞该 key
                    self._exhausted_keys.add(key)
                continue
            except SearchError:
                raise
            except Exception as e:  # noqa: BLE001
                raise SearchError(f"Tavily 搜索失败: {e}") from e
            finally:
                with self._lock:
                    self._inflight[key] = max(0, self._inflight.get(key, 1) - 1)

        raise SearchError(f"Tavily 搜索失败: 所有 {len(self._keys)} 个 key 配额均已耗尽 ({last_err})")

    async def search(
        self, query: str, max_results: int, language: str = "both", **_kw
    ) -> list[SearchHit]:
        # **_kw 吸收 P2 的时间/排序/分页：Tavily 不支持，忽略
        try:
            return await asyncio.to_thread(self._search_sync, query, max_results)
        except SearchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"Tavily 搜索失败: {e}") from e
