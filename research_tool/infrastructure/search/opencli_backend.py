"""OpenCLI browser-backed search fallback.

This backend intentionally stays optional: it reuses a local logged-in Chrome session and is
therefore suitable for CAPTCHA/403 fallback, not unattended server-side batch collection.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Any

from ...domain.errors import SearchError
from ...domain.models import CollectorConfig
from .base import SearchBackend, SearchHit


class OpenCLISearchBackend(SearchBackend):
    name = "opencli"

    def __init__(self, config: CollectorConfig) -> None:
        self.config = config

    def _command(self, query: str, max_results: int) -> list[str]:
        command = shutil.which(self.config.opencli_cmd) or self.config.opencli_cmd
        limit = max(1, min(int(max_results), 100))
        return [
            command,
            self.config.opencli_site,
            "search",
            query,
            "--limit",
            str(limit),
            "-f",
            "json",
        ]

    @staticmethod
    def _decode(output: str) -> Any:
        text = output.strip()
        starts = [index for index in (text.find("["), text.find("{")) if index >= 0]
        if not starts:
            raise ValueError("OpenCLI 未返回 JSON")
        value, _ = json.JSONDecoder().raw_decode(text[min(starts) :])
        return value

    @staticmethod
    def _items(value: Any) -> list[dict]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for key in ("results", "items", "data", "papers", "entries"):
                items = value.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
            return [value]
        return []

    def _search_sync(self, query: str, max_results: int) -> list[SearchHit]:
        args = self._command(query, max_results)
        try:
            result = subprocess.run(  # noqa: S603 - argv only; no shell interpolation
                args,
                capture_output=True,
                text=True,
                timeout=max(10, self.config.timeout_sec),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise SearchError(
                "OpenCLI 不可用；请安装并运行 opencli doctor，确认 Chrome Bridge 已连接"
            ) from exc
        if result.returncode != 0:
            raise SearchError(
                "OpenCLI 搜索适配器执行失败；请先运行 opencli doctor，再单独验证 "
                f"opencli {self.config.opencli_site} search"
            )
        try:
            items = self._items(self._decode(result.stdout or ""))
        except (json.JSONDecodeError, ValueError) as exc:
            raise SearchError("OpenCLI 搜索输出不是可识别的 JSON") from exc

        hits: list[SearchHit] = []
        for item in items:
            url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            snippet = str(
                item.get("snippet")
                or item.get("description")
                or item.get("abstract")
                or item.get("content")
                or ""
            ).strip()
            hits.append(
                SearchHit(
                    url=url,
                    title=title,
                    snippet=snippet,
                    source_engine=self.name,
                )
            )
            if len(hits) >= max_results:
                break
        return hits

    async def search(
        self,
        query: str,
        max_results: int,
        language: str = "both",
        **_kwargs,
    ) -> list[SearchHit]:
        del language
        return await asyncio.to_thread(self._search_sync, query, max_results)
