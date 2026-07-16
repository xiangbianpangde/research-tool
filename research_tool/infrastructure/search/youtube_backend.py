"""YouTube 视频搜索后端（CVPRTalk / VideoIngest 发现层）。

用项目已有依赖 ``yt-dlp`` 的 ``ytsearchN:<query>`` extractor，无需 YouTube Data
API key。返回 ``watch`` URL + 频道/时长 snippet，供 Collect 阶段发现视频；
实际下载转写仍走 ``video_pipeline``（阶段 D 的 host 路由或 CLI ``--video-url``）。

镜像 ``bilibili_backend`` 的 SearchHit 形态：snippet = ``频道 | 时长 | 简介``。
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

from ...common.logging_config import get_logger
from ...domain.errors import SearchError
from .base import SearchBackend, SearchHit

logger = get_logger(__name__)


def _fmt_duration(sec: Any) -> str:
    try:
        s = int(sec)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        return ""
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _ytsearch_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    """在线程里跑 yt-dlp flat extract，避免阻塞事件循环。"""
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise SearchError(
            "youtube 源需要 yt-dlp：pip install yt-dlp（或 research-tool[video] extras）"
        ) from e

    n = max(1, min(int(max_results), 50))
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 20,
        # 禁用 dotenv 注入的死代理（P1/P12）：yt-dlp 默认读 HTTPS_PROXY；
        # 空串 = 不走代理。需要代理时设 YTDLP_PROXY / HTTPS_PROXY 且代理进程在线。
        "proxy": "",
    }
    # 若显式提供可用代理再启用（避免 127.0.0.1:10809 未开导致全失败）
    import os

    explicit = (os.environ.get("YTDLP_PROXY") or "").strip()
    if explicit:
        ydl_opts["proxy"] = explicit
    target = f"ytsearch{n}:{query}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target, download=False)
    if not info:
        return []
    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return []
    return [e for e in entries if isinstance(e, dict)]


class YouTubeBackend(SearchBackend):
    """YouTube 搜索（yt-dlp ytsearch，零 API key）。"""

    name = "youtube"

    async def search(
        self,
        query: str,
        max_results: int,
        language: str = "both",
        **_kw,
    ) -> list[SearchHit]:
        q = (query or "").strip()
        if not q:
            return []
        if shutil.which("yt-dlp") is None:
            # 二进制可选；Python 包 yt_dlp 即可。仅作提示，真正依赖 import。
            logger.debug("PATH 无 yt-dlp 可执行文件，将尝试 python 包 yt_dlp")

        try:
            entries = await asyncio.to_thread(_ytsearch_sync, q, max_results)
        except SearchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SearchError(f"youtube 搜索失败: {e}") from e

        hits: list[SearchHit] = []
        for it in entries[:max_results]:
            vid = it.get("id") or ""
            url = it.get("url") or it.get("webpage_url") or ""
            if not url and vid:
                url = f"https://www.youtube.com/watch?v={vid}"
            if not url:
                continue
            # extract_flat 有时只给 id 相对路径
            if url.startswith("http") is False and vid:
                url = f"https://www.youtube.com/watch?v={vid}"

            title = (it.get("title") or "").strip()
            channel = (
                it.get("channel")
                or it.get("uploader")
                or it.get("channel_id")
                or ""
            )
            duration = _fmt_duration(it.get("duration"))
            desc = (it.get("description") or it.get("title") or "")[:200]
            parts: list[str] = []
            if channel:
                parts.append(str(channel))
            if duration:
                parts.append(f"时长 {duration}")
            if desc and desc != title:
                parts.append(desc)
            hits.append(
                SearchHit(
                    url=url,
                    title=title,
                    snippet=" | ".join(parts),
                    source_engine=self.name,
                )
            )
        return hits
