"""页面抓取器。

主路径：Crawl4AI（决策选定，重量级，强于 JS 渲染/反爬）。
回退路径：httpx（无浏览器环境或未安装 crawl4ai 时），便于测试与轻量部署。
两者统一返回 (markdown_text, internal_links)。

P6：``fetch`` 外层 ``asyncio.wait_for`` 硬墙，避免 Crawl4AI/httpx 单页挂死
整条 collect/deepen。墙钟 = ``timeout_sec + HARD_TIMEOUT_GRACE_SEC``。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

import httpx

from ...common.logging_config import get_logger, hash_url
from ...common.url_guard import assert_safe_url
from ...domain.errors import UrlBlockedError

logger = get_logger(__name__)

# 在 page/http timeout 之外给浏览器启停与 teardown 留的宽限（秒）
HARD_TIMEOUT_GRACE_SEC = 15

# github.com/owner/repo（排除 settings 等非仓页）
_GH_REPO_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_GH_NON_REPO_OWNERS = frozenset(
    {
        "settings",
        "marketplace",
        "features",
        "topics",
        "collections",
        "events",
        "sponsors",
        "orgs",
        "organizations",
        "users",
        "search",
        "about",
        "pricing",
        "login",
        "join",
        "notifications",
        "explore",
    }
)
_GH_NON_REPO_NAMES = frozenset(
    {
        "issues",
        "pulls",
        "actions",
        "projects",
        "security",
        "pulse",
        "settings",
        "wiki",
        "network",
        "graphs",
        "stargazers",
        "watchers",
    }
)


async def _guard_request(request: httpx.Request) -> None:
    """httpx event hook: re-validate every request URL (covers redirect targets)."""
    assert_safe_url(str(request.url))


@dataclass
class FetchResult:
    url: str
    markdown: str
    links: list[str] = field(default_factory=list)
    ok: bool = True
    error: str = ""


def _crawl4ai_available() -> bool:
    try:
        import crawl4ai  # pyright: ignore[reportMissingImports]  # noqa: F401

        return True
    except ImportError:
        return False


_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
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


def _github_meta_markdown(owner: str, repo: str, url: str, info: object) -> str:
    """Render GitHub API metadata without mutating the source response."""
    if not isinstance(info, dict):
        return ""
    desc = str(info.get("description") or "").strip()
    stars = info.get("stargazers_count", 0)
    forks = info.get("forks_count", 0)
    lang = info.get("language") or ""
    home = info.get("homepage") or ""
    topics = info.get("topics") or []
    lines = [
        f"# {owner}/{repo}",
        "",
        f"- stars: {stars}  forks: {forks}" + (f"  language: {lang}" if lang else ""),
    ]
    if desc:
        lines = [*lines, f"- description: {desc}"]
    if home:
        lines = [*lines, f"- homepage: {home}"]
    if topics:
        lines = [*lines, "- topics: " + ", ".join(str(topic) for topic in topics[:12])]
    return "\n".join([*lines, f"- html_url: {url}", ""])


class Fetcher:
    def __init__(
        self,
        timeout_sec: int = 30,
        prefer_crawl4ai: bool = True,
        *,
        parse_pdf: bool = True,
        mineru_cmd: str | None = None,
        pdf_dir: "Path | None" = None,
        proxy: str | None = None,
        _transport: httpx.AsyncBaseTransport | None = None,
        hard_timeout_grace_sec: int = HARD_TIMEOUT_GRACE_SEC,
    ) -> None:
        self.timeout_sec = timeout_sec
        self.use_crawl4ai = prefer_crawl4ai and _crawl4ai_available()
        self.parse_pdf = parse_pdf
        self.mineru_cmd = mineru_cmd
        self.pdf_dir = pdf_dir
        # 仅 config/Collector 注入的代理；不读 dotenv 死代理（P1/P12/P13 同族）
        self.proxy = proxy
        self._transport = _transport  # test seam; None in production
        self.hard_timeout_grace_sec = max(int(hard_timeout_grace_sec), 0)

    def hard_timeout_sec(self) -> float:
        """外层 wait_for 墙钟：page timeout + grace。"""
        return float(self.timeout_sec) + float(self.hard_timeout_grace_sec)

    async def fetch(self, url: str) -> FetchResult:
        # SSRF entry guard (covers pdf/crawl4ai/httpx). Never log the raw URL.
        try:
            assert_safe_url(url)
        except UrlBlockedError as e:
            logger.warning("SSRF guard blocked fetch: %s", hash_url(url))
            return FetchResult(url, "", ok=False, error=f"SSRF guard: {e}")

        hard = self.hard_timeout_sec()
        try:
            return await asyncio.wait_for(self._fetch_dispatch(url), timeout=hard)
        except asyncio.TimeoutError:
            logger.warning(
                "fetch hard timeout after %.0fs url_hash=%s", hard, hash_url(url)
            )
            return FetchResult(
                url,
                "",
                ok=False,
                error=f"fetch hard timeout after {hard:.0f}s",
            )

    async def _fetch_dispatch(self, url: str) -> FetchResult:
        if url.lower().split("?")[0].endswith(".pdf"):
            return await self._fetch_pdf(url)
        # GitHub 仓页：优先 raw README（干净 Markdown），失败再 HTML/Crawl4AI
        gh = await self._fetch_github_readme(url)
        if gh is not None and gh.ok and gh.markdown.strip():
            return gh
        if self.use_crawl4ai:
            return await self._fetch_crawl4ai(url)
        return await self._fetch_httpx(url)

    async def _fetch_github_readme(self, url: str) -> FetchResult | None:
        """github.com/owner/repo → raw.githubusercontent.com README + API 元数据。

        返回 None 表示非仓 URL 或全部失败（调用方回退 HTML）。
        """
        m = _GH_REPO_RE.match((url or "").strip())
        if not m:
            return None
        owner, repo = m.group(1), m.group(2)
        repo = repo.removesuffix(".git")
        if owner.lower() in _GH_NON_REPO_OWNERS or repo.lower() in _GH_NON_REPO_NAMES:
            return None

        meta_block = ""
        try:
            async with httpx.AsyncClient(**self._httpx_client_kwargs()) as client:
                api = await client.get(f"https://api.github.com/repos/{owner}/{repo}")
                if api.status_code == 200:
                    meta_block = _github_meta_markdown(owner, repo, url, api.json())
        except Exception as e:  # noqa: BLE001
            logger.debug("github repo API meta skip: %s", e)

        readme_text = ""
        used_branch = ""
        try:
            async with httpx.AsyncClient(**self._httpx_client_kwargs()) as client:
                for branch in ("main", "master", "develop"):
                    raw_url = (
                        f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/README.md"
                    )
                    resp = await client.get(raw_url)
                    if resp.status_code == 200 and len(resp.text.strip()) >= 40:
                        readme_text = resp.text
                        used_branch = branch
                        break
                    # 常见大写
                    raw_url2 = (
                        f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/readme.md"
                    )
                    resp2 = await client.get(raw_url2)
                    if resp2.status_code == 200 and len(resp2.text.strip()) >= 40:
                        readme_text = resp2.text
                        used_branch = branch
                        break
        except Exception as e:  # noqa: BLE001
            logger.debug("github raw README fail: %s", e)
            return None

        if not readme_text:
            # 有 API 元数据也比纯壳页强：仍返回 meta，否则 None 让 HTML 接手
            if meta_block:
                return FetchResult(
                    url,
                    meta_block + "\n<!-- note: raw README not found; meta only -->\n",
                    ok=True,
                )
            return None

        header = meta_block or f"# {owner}/{repo}\n\n"
        body = (
            f"{header}"
            f"<!-- source: raw.githubusercontent.com branch={used_branch} README.md -->\n\n"
            f"{readme_text}"
        )
        return FetchResult(url, body, ok=True)

    def _httpx_client_kwargs(self) -> dict:
        """httpx 客户端公共参数：无显式代理时 trust_env=False，避免 dotenv 劫持。"""
        return {
            "follow_redirects": True,
            "timeout": self.timeout_sec,
            "headers": {"User-Agent": "Mozilla/5.0 (research-tool)"},
            "event_hooks": {"request": [_guard_request]},
            "transport": self._transport,
            "proxy": self.proxy,
            "trust_env": bool(self.proxy),
        }

    async def _fetch_pdf(self, url: str) -> FetchResult:
        """下载 PDF 字节，交给 MinerU 解析。"""
        if not self.parse_pdf:
            return FetchResult(url, "", ok=False, error="PDF 已跳过(parse_pdf=False)")
        try:
            async with httpx.AsyncClient(**self._httpx_client_kwargs()) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.content
        except UrlBlockedError as e:
            logger.warning("SSRF guard blocked PDF redirect: %s", hash_url(url))
            return FetchResult(url, "", ok=False, error=f"SSRF guard: {e}")
        except Exception as e:  # noqa: BLE001
            return FetchResult(url, "", ok=False, error=f"PDF 下载失败: {e}")
        if not data[:5].startswith(b"%PDF"):
            return await self._fetch_httpx(url)  # 不是真 PDF，按 HTML 处理
        return await self._pdf_bytes_to_md(url, data)

    async def _pdf_bytes_to_md(self, url: str, data: bytes) -> FetchResult:
        """把 PDF 字节落盘并用 MinerU 转 Markdown；不可解析则跳过（绝不塞二进制）。"""
        import hashlib
        import tempfile

        if not self.parse_pdf:
            return FetchResult(url, "", ok=False, error="PDF 已跳过(parse_pdf=False)")
        from ..ingest.pdf import find_mineru, mineru_to_markdown

        mineru = find_mineru(self.mineru_cmd)
        if not mineru:
            return FetchResult(
                url,
                "",
                ok=False,
                error="抓到 PDF 但未找到 mineru，已跳过（用 --mineru-cmd 指定）",
            )
        try:
            root = Path(self.pdf_dir) if self.pdf_dir else Path(tempfile.gettempdir())
            root.mkdir(parents=True, exist_ok=True)
            pdf_path = root / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.pdf"
            pdf_path.write_bytes(data)
            md = await asyncio.to_thread(mineru_to_markdown, pdf_path, mineru, root, lang="ch")
            return FetchResult(url, md)
        except Exception as e:  # noqa: BLE001 - 单篇失败不中断整批
            return FetchResult(url, "", ok=False, error=f"PDF 解析失败: {e}")

    async def _fetch_crawl4ai(self, url: str) -> FetchResult:
        try:
            from crawl4ai import (  # pyright: ignore[reportMissingImports]
                AsyncWebCrawler,
                BrowserConfig,
                CrawlerRunConfig,
            )

            # 只用 self.proxy（Collector 从 config 注入）。不回退 HTTPS_PROXY 环境变量，
            # 否则 dotenv 里未启动的 127.0.0.1:10809 会拖死浏览器抓取（P1/P6）。
            proxy = (self.proxy or "").strip() or None
            browser_cfg = (
                BrowserConfig(headless=True, proxy_config={"server": proxy})
                if proxy
                else BrowserConfig(headless=True)
            )
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
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
            async with httpx.AsyncClient(**self._httpx_client_kwargs()) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").lower()
                if "application/pdf" in ctype or resp.content[:5].startswith(b"%PDF"):
                    return await self._pdf_bytes_to_md(url, resp.content)
                html = resp.text
            md = _html_to_markdown(html)
            links = list(dict.fromkeys(_HREF.findall(html)))[:50]
            return FetchResult(url, md, links=links)
        except UrlBlockedError as e:
            logger.warning("SSRF guard blocked redirect: %s", hash_url(url))
            return FetchResult(url, "", ok=False, error=f"SSRF guard: {e}")
        except Exception as e:  # noqa: BLE001
            return FetchResult(url, "", ok=False, error=str(e))
