"""页面抓取器。

主路径：Crawl4AI（决策选定，重量级，强于 JS 渲染/反爬）。
回退路径：httpx（无浏览器环境或未安装 crawl4ai 时），便于测试与轻量部署。
两者统一返回 (markdown_text, internal_links)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

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
    def __init__(
        self,
        timeout_sec: int = 30,
        prefer_crawl4ai: bool = True,
        *,
        parse_pdf: bool = True,
        mineru_cmd: str | None = None,
        pdf_dir: "Path | None" = None,
    ) -> None:
        self.timeout_sec = timeout_sec
        self.use_crawl4ai = prefer_crawl4ai and _crawl4ai_available()
        self.parse_pdf = parse_pdf
        self.mineru_cmd = mineru_cmd
        self.pdf_dir = pdf_dir

    async def fetch(self, url: str) -> FetchResult:
        if url.lower().split("?")[0].endswith(".pdf"):
            return await self._fetch_pdf(url)
        if self.use_crawl4ai:
            return await self._fetch_crawl4ai(url)
        return await self._fetch_httpx(url)

    async def _fetch_pdf(self, url: str) -> FetchResult:
        """下载 PDF 字节，交给 MinerU 解析。"""
        if not self.parse_pdf:
            return FetchResult(url, "", ok=False, error="PDF 已跳过(parse_pdf=False)")
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=self.timeout_sec,
                headers={"User-Agent": "Mozilla/5.0 (research-tool)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.content
        except Exception as e:  # noqa: BLE001
            return FetchResult(url, "", ok=False, error=f"PDF 下载失败: {e}")
        if not data[:5].startswith(b"%PDF"):
            return await self._fetch_httpx(url)  # 不是真 PDF，按 HTML 处理
        return await self._pdf_bytes_to_md(url, data)

    async def _pdf_bytes_to_md(self, url: str, data: bytes) -> FetchResult:
        """把 PDF 字节落盘并用 MinerU 转 Markdown；不可解析则跳过（绝不塞二进制）。"""
        import asyncio
        import hashlib
        import tempfile

        if not self.parse_pdf:
            return FetchResult(url, "", ok=False, error="PDF 已跳过(parse_pdf=False)")
        from ..ingest.pdf import find_mineru, mineru_to_markdown

        mineru = find_mineru(self.mineru_cmd)
        if not mineru:
            return FetchResult(
                url, "", ok=False,
                error="抓到 PDF 但未找到 mineru，已跳过（用 --mineru-cmd 指定）",
            )
        try:
            root = Path(self.pdf_dir) if self.pdf_dir else Path(tempfile.gettempdir())
            root.mkdir(parents=True, exist_ok=True)
            pdf_path = root / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.pdf"
            pdf_path.write_bytes(data)
            md = await asyncio.to_thread(
                mineru_to_markdown, pdf_path, mineru, root, lang="ch"
            )
            return FetchResult(url, md)
        except Exception as e:  # noqa: BLE001 - 单篇失败不中断整批
            return FetchResult(url, "", ok=False, error=f"PDF 解析失败: {e}")

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
                ctype = resp.headers.get("content-type", "").lower()
                if "application/pdf" in ctype or resp.content[:5].startswith(b"%PDF"):
                    return await self._pdf_bytes_to_md(url, resp.content)
                html = resp.text
            md = _html_to_markdown(html)
            links = list(dict.fromkeys(_HREF.findall(html)))[:50]
            return FetchResult(url, md, links=links)
        except Exception as e:  # noqa: BLE001
            return FetchResult(url, "", ok=False, error=str(e))
