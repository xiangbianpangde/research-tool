"""页面抓取器。

主路径：Crawl4AI（决策选定，重量级，强于 JS 渲染/反爬）。
回退路径：httpx（无浏览器环境或未安装 crawl4ai 时），便于测试与轻量部署。
两者统一返回 (markdown_text, internal_links)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

import httpx


@dataclass
class FetchResult:
    url: str
    markdown: str
    links: list[str] = field(default_factory=list)
    ok: bool = True
    error: str = ""


def _crawl4ai_available() -> bool:
    try:
        import crawl4ai  # noqa: F401

        return True
    except ImportError:
        return False


_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href=["\'](https?://[^"\'#]+)["\']', re.IGNORECASE)
_BLANKS = re.compile(r"\n{3,}")


def _html_to_markdown(html: str) -> str:
    """极简 HTML→文本：保留段落结构，去标签。重清洗交给 Cleaner。"""
    text = _SCRIPT_STYLE.sub("", html)
    text = re.sub(r"</(p|div|h[1-6]|li|tr|br)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub("", text)
    text = unescape(text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()


class Fetcher:
    def __init__(self, timeout_sec: int = 30, prefer_crawl4ai: bool = True) -> None:
        self.timeout_sec = timeout_sec
        self.use_crawl4ai = prefer_crawl4ai and _crawl4ai_available()

    async def fetch(self, url: str) -> FetchResult:
        if self.use_crawl4ai:
            return await self._fetch_crawl4ai(url)
        return await self._fetch_httpx(url)

    async def _fetch_crawl4ai(self, url: str) -> FetchResult:
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

            async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
                result = await crawler.arun(
                    url, config=CrawlerRunConfig(page_timeout=self.timeout_sec * 1000)
                )
            if not result.success:
                return FetchResult(url, "", ok=False, error=result.error_message or "crawl 失败")
            md = result.markdown
            md_text = getattr(md, "raw_markdown", None) or str(md or "")
            links = []
            if isinstance(result.links, dict):
                for item in result.links.get("internal", []):
                    href = item.get("href") if isinstance(item, dict) else item
                    if href:
                        links.append(href)
            return FetchResult(url, md_text, links=links)
        except Exception as e:  # noqa: BLE001 - 抓取失败不应中断整批
            return FetchResult(url, "", ok=False, error=str(e))

    async def _fetch_httpx(self, url: str) -> FetchResult:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self.timeout_sec,
                headers={"User-Agent": "Mozilla/5.0 (research-tool)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text
            md = _html_to_markdown(html)
            links = list(dict.fromkeys(_HREF.findall(html)))[:50]
            return FetchResult(url, md, links=links)
        except Exception as e:  # noqa: BLE001
            return FetchResult(url, "", ok=False, error=str(e))
