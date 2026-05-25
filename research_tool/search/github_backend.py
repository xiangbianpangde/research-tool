"""GitHub 仓库搜索后端（免费 REST API）。

技术调研必备：开源实现、README/文档、社区活跃度（stars/forks）。
API: https://api.github.com/search/repositories

无 Key 时搜索限 10 次/分钟；配置 github_token 可提升到 30 次/分钟。
限流返回 403/429，已在 _http.get_json 做退避。
"""

from __future__ import annotations

from ..errors import SearchError
from ._http import describe, get_json
from .base import SearchBackend, SearchHit

_ENDPOINT = "https://api.github.com/search/repositories"


class GitHubBackend(SearchBackend):
    name = "github"

    def __init__(self, token: str | None = None) -> None:
        self.token = token

    async def search(
        self, query: str, max_results: int, language: str = "both"
    ) -> list[SearchHit]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        params = {
            "q": query,
            "per_page": min(max_results, 30),
            "sort": "stars",
            "order": "desc",
        }
        try:
            data = await get_json(_ENDPOINT, params=params, headers=headers)
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"github 搜索失败: {describe(e)}") from e

        items = (data.get("items") or []) if isinstance(data, dict) else []
        hits: list[SearchHit] = []
        for repo in items:
            url = repo.get("html_url")
            if not url:
                continue
            stars = repo.get("stargazers_count", 0)
            forks = repo.get("forks_count", 0)
            lang = repo.get("language") or ""
            desc = repo.get("description") or ""
            meta = f"★{stars} ⑂{forks}" + (f" | {lang}" if lang else "")
            snippet = f"{meta}\n{desc}".strip()
            hits.append(
                SearchHit(
                    url=url,
                    title=repo.get("full_name", ""),
                    snippet=snippet,
                    source_engine=self.name,
                )
            )
        return hits
