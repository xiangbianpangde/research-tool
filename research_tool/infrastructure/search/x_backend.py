"""X/Twitter search backend.

认证策略（默认 opencli）：
- **opencli 自动从浏览器登录态取 cookie**，本仓库不持久化 TWITTER_AUTH_TOKEN/CT0。
- preflight 会跑 ``opencli doctor``；未登录时尝试常见 refresh/login 子命令一次。
- twitter-cli 才需要用户自备 cookie/env。

P0：
- opencli 传 ``-n max_results``
- ``preflight_x``：CLI 存在 + doctor/status
P1：
- 过滤空/广告样文本
- 若 CLI 字段含 likes/rts，按 ``x_min_engagement`` 门槛过滤
- 尽量保留 thread/全文字段
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...common.logging_config import get_logger
from ...domain.errors import SearchError
from ...domain.models import CollectorConfig
from .base import SearchBackend, SearchHit

logger = get_logger(__name__)

_URL_ONLY_RE = re.compile(r"https?://\S+", re.I)
_INSTALL_HINT = """\
X 源不可用。请先完成其一：

【推荐 OpenCLI — cookie 自动获取，勿手填到 .env】
  1) 安装 opencli（见 README「X/Twitter」）
  2) 浏览器装 opencli 扩展并登录 x.com（登录态由 opencli 自动读取 cookie）
  3) 验收：opencli doctor
  4) research collect "主题" -s x --x-backend opencli
  research-tool 不会保存 TWITTER_AUTH_TOKEN/CT0；opencli 负责会话刷新。

【twitter-cli — 仅当不用 opencli 时】
  1) 安装 twitter CLI 并保证 `twitter` 在 PATH
  2) 配置 TWITTER_AUTH_TOKEN / TWITTER_CT0
  3) 验收：twitter status
  4) research collect "主题" -s x --x-backend twitter-cli --x-cmd twitter

不要把 cookie 写入仓库。失败时本源会以 SearchError/warnings 暴露，不会静默。
"""


@dataclass(frozen=True)
class XPreflightResult:
    ok: bool
    command: str
    backend: str
    message: str
    doctor_ok: bool | None = None  # None=未跑 doctor/status


def preflight_x(config: CollectorConfig, *, run_doctor: bool = True) -> XPreflightResult:
    """检查 X CLI 是否可用；可选跑 doctor/status。

    不抛异常：返回 XPreflightResult，由调用方决定 raise 或 warning。
    """
    backend = config.x_backend
    if backend == "opencli":
        cmd = shutil.which("opencli.cmd") or shutil.which("opencli") or "opencli"
    else:
        cmd = shutil.which(config.x_cmd) or config.x_cmd

    resolved = shutil.which(cmd) if not Path(cmd).exists() else cmd
    if resolved is None and not Path(cmd).exists():
        return XPreflightResult(
            ok=False,
            command=cmd,
            backend=backend,
            message=f"X 后端命令不可用: {cmd}\n{_INSTALL_HINT}",
            doctor_ok=None,
        )

    if not run_doctor:
        return XPreflightResult(
            ok=True,
            command=cmd,
            backend=backend,
            message=f"X CLI 已找到: {cmd}",
            doctor_ok=None,
        )

    # 轻量验收：doctor / status；opencli 未登录时尝试自动刷新浏览器 cookie 会话
    doctor_ok: bool | None = None
    detail = ""
    try:
        if backend == "opencli":
            doctor_ok, detail = _opencli_doctor_and_refresh(cmd)
        else:
            r = subprocess.run(  # noqa: S603 - configured CLI, argv list without shell
                [cmd, "status"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
            detail = out[-400:] if out else f"exit={r.returncode}"
            blob = out.lower()
            if r.returncode == 0:
                doctor_ok = True
            elif any(k in blob for k in ("ok", "ready", "logged", "authenticated", "success")):
                doctor_ok = True
            else:
                doctor_ok = False
    except FileNotFoundError:
        return XPreflightResult(
            ok=False,
            command=cmd,
            backend=backend,
            message=f"X 后端命令不可用: {cmd}\n{_INSTALL_HINT}",
            doctor_ok=False,
        )
    except subprocess.TimeoutExpired:
        doctor_ok = False
        detail = "doctor/status 超时"
    except Exception as e:  # noqa: BLE001
        doctor_ok = False
        detail = str(e)

    if doctor_ok is False:
        return XPreflightResult(
            ok=False,
            command=cmd,
            backend=backend,
            message=(
                f"X CLI 存在但登录/健康检查未通过 ({cmd}): {detail}\n{_INSTALL_HINT}"
            ),
            doctor_ok=False,
        )
    return XPreflightResult(
        ok=True,
        command=cmd,
        backend=backend,
        message=(
            f"X preflight OK ({backend}: {cmd}; opencli 自动 cookie)"
            if backend == "opencli"
            else f"X preflight OK ({backend}: {cmd})"
        )
        + (f" — {detail[:120]}" if detail else ""),
        doctor_ok=doctor_ok,
    )


def _run_capture(args: list[str], *, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - validated command argv, never executed through a shell
        args, capture_output=True, text=True, timeout=timeout
    )


def _looks_healthy(returncode: int, out: str) -> bool:
    blob = (out or "").lower()
    if returncode == 0:
        return True
    return any(k in blob for k in ("ok", "ready", "logged", "authenticated", "success", "healthy"))


def _opencli_doctor_and_refresh(cmd: str) -> tuple[bool, str]:
    """opencli doctor；失败则尝试一次会话刷新（cookie 由 opencli 从浏览器自动取）。

    不解析、不落盘 cookie 内容。刷新子命令因 opencli 版本而异，失败仅记入 detail。
    """
    r = _run_capture([cmd, "doctor"], timeout=30)
    out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    if _looks_healthy(r.returncode, out):
        return True, out[-400:] if out else "doctor ok"

    # 尝试常见 refresh / auth 命令（存在即跑，忽略未知子命令）
    refresh_attempts = [
        [cmd, "auth", "refresh"],
        [cmd, "login", "refresh"],
        [cmd, "twitter", "auth"],
        [cmd, "session", "refresh"],
    ]
    refresh_notes: list[str] = []
    for args in refresh_attempts:
        try:
            rr = _run_capture(args, timeout=60)
            note = ((rr.stdout or "") + "\n" + (rr.stderr or "")).strip()
            refresh_notes.append(f"{' '.join(args[1:])}: exit={rr.returncode}")
            # 任一 refresh 后再 doctor
            if rr.returncode == 0 or _looks_healthy(rr.returncode, note):
                r2 = _run_capture([cmd, "doctor"], timeout=30)
                out2 = ((r2.stdout or "") + "\n" + (r2.stderr or "")).strip()
                if _looks_healthy(r2.returncode, out2):
                    return True, (out2 or "doctor ok after refresh")[-400:]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            refresh_notes.append(f"{' '.join(args[1:])}: timeout/missing")
        except Exception as e:  # noqa: BLE001
            refresh_notes.append(f"{' '.join(args[1:])}: {e}")

    detail = (out[-200:] if out else f"exit={r.returncode}") + (
        " | refresh: " + "; ".join(refresh_notes[:4]) if refresh_notes else ""
    )
    return False, detail


class XBackend(SearchBackend):
    name = "x"

    def __init__(self, config: CollectorConfig) -> None:
        self.config = config

    @staticmethod
    def _resolve_command(name: str) -> str:
        return shutil.which(name) or name

    def _opencli_command(self) -> str:
        return self._resolve_command(
            "opencli.cmd" if shutil.which("opencli.cmd") else "opencli"
        )

    def _twitter_command(self) -> str:
        return self._resolve_command(self.config.x_cmd)

    def _command(self, query: str, max_results: int) -> list[str]:
        n = max(1, min(int(max_results), 100))
        if self.config.x_backend == "opencli":
            # opencli v1.8+：用 --limit（不是 -n）；-f json 给机器可读输出
            return [
                self._opencli_command(),
                "twitter",
                "search",
                query,
                "--limit",
                str(n),
                "-f",
                "json",
            ]
        return [
            self._twitter_command(),
            "search",
            query,
            "-n",
            str(n),
            "--json",
        ]

    def _ensure_available(self) -> None:
        pf = preflight_x(self.config, run_doctor=False)
        if not pf.ok:
            raise SearchError(pf.message)

    @staticmethod
    def _loads_json_output(output: str) -> Any:
        text = output.strip()
        starts = [idx for idx in (text.find("["), text.find("{")) if idx >= 0]
        if not starts:
            raise json.JSONDecodeError("no JSON object found", text, 0)
        data, _ = json.JSONDecoder().raw_decode(text[min(starts) :])
        return data

    @staticmethod
    def _items(data: Any) -> list[dict]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("tweets", "results", "items", "data", "timeline"):
                val = data.get(key)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)]
            return [data]
        return []

    @staticmethod
    def _url(item: dict) -> str:
        for key in ("url", "link", "tweet_url", "permalink"):
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
    def _text(item: dict) -> str:
        # P1：优先全文 / thread 拼接字段
        for key in (
            "full_text",
            "fullText",
            "thread_text",
            "threadText",
            "text",
            "content",
            "body",
            "rawContent",
        ):
            val = item.get(key)
            if val and str(val).strip():
                return str(val).strip()
        # thread 数组
        thread = item.get("thread") or item.get("conversation")
        if isinstance(thread, list):
            parts = []
            for t in thread:
                if isinstance(t, dict):
                    parts.append(XBackend._text(t))
                elif t:
                    parts.append(str(t))
            joined = "\n".join(p for p in parts if p)
            if joined.strip():
                return joined.strip()
        return ""

    @staticmethod
    def _title(item: dict) -> str:
        user = item.get("username") or item.get("author") or item.get("user") or "x"
        if isinstance(user, dict):
            user = user.get("username") or user.get("name") or "x"
        text = XBackend._text(item)
        return f"@{str(user).lstrip('@')}: {text[:80]}"

    @staticmethod
    def _engagement(item: dict) -> int | None:
        """合计 likes+rts+replies；字段全无则 None（不应用门槛）。"""
        keys_like = ("like_count", "likes", "favorite_count", "favorites", "favourite_count")
        keys_rt = ("retweet_count", "retweets", "repost_count", "reposts")
        keys_rp = ("reply_count", "replies")
        found = False
        total = 0
        for keys in (keys_like, keys_rt, keys_rp):
            for k in keys:
                if k in item and item[k] is not None:
                    try:
                        total += int(item[k])
                        found = True
                        break
                    except (TypeError, ValueError):
                        continue
        return total if found else None

    @staticmethod
    def _is_noise(text: str) -> bool:
        t = (text or "").strip()
        if len(t) < 8:
            return True
        # 几乎只有 URL / 标签
        without_urls = _URL_ONLY_RE.sub("", t).strip()
        if len(without_urls) < 6:
            return True
        # 重复字符刷屏
        if len(set(without_urls.replace(" ", ""))) <= 2 and len(without_urls) > 12:
            return True
        return False

    async def search(
        self,
        query: str,
        max_results: int,
        language: str = "both",
        **_kw,
    ) -> list[SearchHit]:
        # 完整 preflight（含 doctor）；失败给出安装说明
        pf = preflight_x(self.config, run_doctor=True)
        if not pf.ok:
            raise SearchError(pf.message)
        logger.info("%s", pf.message)

        cmd = self._command(query, max_results)
        try:
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired as e:
            raise SearchError("X 搜索超时（60s）") from e

        if result.returncode != 0:
            raise SearchError(
                f"X 搜索失败 code={result.returncode}: "
                f"{(result.stderr or result.stdout)[-500:]}\n{_INSTALL_HINT}"
            )
        try:
            data = self._loads_json_output(result.stdout)
        except json.JSONDecodeError as e:
            raise SearchError(
                f"X 后端未输出 JSON: {result.stdout[:300]}\n{_INSTALL_HINT}"
            ) from e

        min_eng = int(getattr(self.config, "x_min_engagement", 0) or 0)
        hits: list[SearchHit] = []
        for item in self._items(data):
            if len(hits) >= max_results:
                break
            text = self._text(item)
            if self._is_noise(text):
                continue
            if min_eng > 0:
                eng = self._engagement(item)
                if eng is not None and eng < min_eng:
                    continue
            url = self._url(item)
            if not url and not text:
                continue
            # snippet：保留更长 thread 文本（P1）
            snippet = text[:2000]
            if min_eng > 0:
                eng = self._engagement(item)
                if eng is not None:
                    snippet = f"[engagement={eng}]\n{snippet}"
            hits.append(
                SearchHit(
                    url=url or f"x://search/{len(hits)}",
                    title=self._title(item),
                    snippet=snippet,
                    source_engine=self.name,
                )
            )
        return hits
