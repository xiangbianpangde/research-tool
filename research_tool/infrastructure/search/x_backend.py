"""X/Twitter search backend.

This backend intentionally delegates authentication to external read-only CLIs
(`twitter-cli` or OpenCLI). The project does not store X cookies or tokens.
"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
import shutil
import subprocess
from typing import Any

from ...domain.errors import SearchError
from ...domain.models import CollectorConfig
from .base import SearchBackend, SearchHit


class XBackend(SearchBackend):
    name = "x"

    def __init__(self, config: CollectorConfig) -> None:
        self.config = config

    @staticmethod
    def _resolve_command(name: str) -> str:
        return shutil.which(name) or name

    def _opencli_command(self) -> str:
        return self._resolve_command("opencli.cmd" if shutil.which("opencli.cmd") else "opencli")

    def _twitter_command(self) -> str:
        return self._resolve_command(self.config.x_cmd)

    def _command(self, query: str, max_results: int) -> list[str]:
        if self.config.x_backend == "opencli":
            return [self._opencli_command(), "twitter", "search", query, "-f", "json"]
        return [self._twitter_command(), "search", query, "-n", str(max_results), "--json"]

    def _ensure_available(self) -> None:
        cmd = self._opencli_command() if self.config.x_backend == "opencli" else self._twitter_command()
        if shutil.which(cmd) is None and not Path(cmd).exists():
            raise SearchError(
                f"X 后端命令不可用: {cmd}。请安装/配置 twitter-cli 或改用 opencli。"
            )

    @staticmethod
    def _loads_json_output(output: str) -> Any:
        text = output.strip()
        starts = [idx for idx in (text.find("["), text.find("{")) if idx >= 0]
        if not starts:
            raise json.JSONDecodeError("no JSON object found", text, 0)
        data, _ = json.JSONDecoder().raw_decode(text[min(starts):])
        return data

    @staticmethod
    def _items(data: Any) -> list[dict]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("tweets", "results", "items", "data"):
                val = data.get(key)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
            return [data]
        return []

    @staticmethod
    def _url(item: dict) -> str:
        for key in ("url", "link", "tweet_url"):
            if item.get(key):
                return str(item[key])
        tid = item.get("id") or item.get("tweet_id") or item.get("rest_id")
        user = item.get("username") or item.get("user") or item.get("screen_name")
        if isinstance(user, dict):
            user = user.get("username") or user.get("screen_name")
        if tid and user:
            return f"https://x.com/{str(user).lstrip('@')}/status/{tid}"
        if tid:
            return f"https://x.com/i/status/{tid}"
        return ""

    @staticmethod
    def _title(item: dict) -> str:
        user = item.get("username") or item.get("author") or item.get("user") or "x"
        if isinstance(user, dict):
            user = user.get("username") or user.get("name") or "x"
        text = item.get("text") or item.get("content") or item.get("full_text") or ""
        return f"@{str(user).lstrip('@')}: {str(text)[:80]}"

    async def search(
        self,
        query: str,
        max_results: int,
        language: str = "both",
        **_kw,
    ) -> list[SearchHit]:
        self._ensure_available()
        cmd = self._command(query, max_results)
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=45
        )
        if result.returncode != 0:
            raise SearchError(
                f"X 搜索失败 code={result.returncode}: {(result.stderr or result.stdout)[-500:]}"
            )
        try:
            data = self._loads_json_output(result.stdout)
        except json.JSONDecodeError as e:
            raise SearchError(f"X 后端未输出 JSON: {result.stdout[:300]}") from e
        hits: list[SearchHit] = []
        for item in self._items(data)[:max_results]:
            url = self._url(item)
            text = item.get("text") or item.get("content") or item.get("full_text") or ""
            if not url and not text:
                continue
            hits.append(
                SearchHit(
                    url=url or f"x://search/{len(hits)}",
                    title=self._title(item),
                    snippet=str(text)[:500],
                    source_engine=self.name,
                )
            )
        return hits
