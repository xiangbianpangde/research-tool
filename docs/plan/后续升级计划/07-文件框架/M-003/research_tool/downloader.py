"""
[文件路径] research_tool/downloader.py
[文件职责]  M-003 下载器：yt-dlp 包装 + YouTube/B站/本地三平台适配 + Cookie 注入
[所属模块]  M-003（来自 DD-001 模块分配）
[关联设计规范] FS-VideoIngest-V1.1 / MD-VideoIngest-V1.1 §M-003 / IC-VideoIngest-V1.1 §IC-005/007/008
[设计模式]  适配器模式（Adapter） + 策略模式（Strategy）—— YouTube/B站/本地三个平台适配器实现统一下载接口
[功能描述]
  功能1: yt-dlp 版本校验（启动期一次性），确保 >= 2023.07.06
  功能2: YouTube 平台下载（需 Deno 桥接 JS 挑战）
  功能3: B 站平台下载（支持 Cookie 注入避免 403）
  功能4: 本地文件解析（mp4/webm/mkv），无下载步骤
  功能5: Cookie 文件安全注入（0o600 权限校验）
[输入输出]
  输入:  VideoURL（DE-001 统一视频 URL 表示，platform ∈ {YOUTUBE, BILIBILI, LOCAL}）
  输出:  DownloadTask（DE-005 下载结果：file_path / size_mb / duration_sec / video_id / etag）
[依赖关系]
  依赖文件: research_tool/datatypes.py (DE-001/005 VideoURL/DownloadTask)
            research_tool/cache_manager.py (M-004 缓存命中查询，IC-009)
            research_tool/error_handler.py (M-010 错误码登记，IC-026)
            research_tool/structured_logger.py (M-011 JSON Lines 日志，IC-028)
  被依赖文件: research_tool/cli.py (M-001 调用 resolve_local/download_youtube 等)
              research_tool/concurrent_orchestrator.py (M-012 调度 downloader 作为 task_func)
[注意事项]
  注意1: subprocess.run 必须在异步上下文用 asyncio.to_thread 包装，避免阻塞事件循环
  注意2: Cookie 文件权限必须为 0o600，否则视为不合法直接拒绝（防泄露）
  注意3: YouTube 下载需 Deno 在 PATH 中（依赖 M-002 预检）；Deno 缺失直接 E_DL_001_DENO_MISSING
  注意4: 失败重试 1 次后仍失败，必须登记 E_DL_003_NETWORK 并由 M-010 接管，不无限循环
  注意5: subprocess 调用捕获 stdout/stderr 但不暴露到调用方（敏感信息可能含 Cookie）
  注意6: B 站 403 严格不绕过（AR 调研 S-101 明确），只提示用户提供 Cookie
[代码风格] 遵循 CS-001（Python 3.11+ / 4 空格 / Google docstring / 类型注解必填）
[创建日期] 2026-06-01
[修改历史]
  2026-06-01: DD-M-003 - 初始文件框架（仅注释，无业务代码）
[作者]      DD-M-003-20260601
[来源标注]  [DD-001:FS-VideoIngest-V1.1 §M-003] [DD-001:MD-VideoIngest-V1.1 §M-003] [DD-001:IC-VideoIngest-V1.1 §IC-007/008]
"""

# === 标准库导入（CS-001 导入顺序：标准库 → 第三方 → 本地） ===
import asyncio
import hashlib
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

# === 第三方导入 ===
# 注：yt-dlp 在运行时由 M-002 preflight 验证存在；本文件仅做包装，避免硬依赖
# [DD-M推断: 第三方导入顺序按 CS-001 §导入规范 排列]

# === 本地导入 ===
# [DD-001:MD-VideoIngest-V1.1 §M-003] 依赖声明


# === 模块级常量（CS-001：UPPER_SNAKE_CASE） ===

#: yt-dlp 最低版本要求（AR 调研 S-001 指定）
#: [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003] [调研:S-001]
YTDLP_MIN_VERSION: str = "2023.07.06"

#: YouTube 平台标识（与 M-001 PlatformResolver 枚举对齐）
#: [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
PLATFORM_YOUTUBE: str = "YOUTUBE"

#: B 站平台标识
#: [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
PLATFORM_BILIBILI: str = "BILIBILI"

#: 本地文件平台标识
#: [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
PLATFORM_LOCAL: str = "LOCAL"

#: 本地文件支持的后缀
#: [来源标注] [DD-001:IC-VideoIngest-V1.1 §IC-008]
LOCAL_SUPPORTED_EXTS: tuple[str, ...] = (".mp4", ".webm", ".mkv")

#: Cookie 文件要求权限（仅所有者可读写）
#: [来源标注] [DD-M推断: AR 调研 Cookie 安全规范，0o600 防同机其他用户读取]
COOKIE_REQUIRED_PERMS: int = 0o600

#: yt-dlp 下载重试次数（首次失败后）
#: [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003 异常处理]
DOWNLOAD_RETRY_TIMES: int = 1

#: yt-dlp 默认输出格式
#: [来源标注] [DD-001:IC-VideoIngest-V1.1 §IC-007]
DEFAULT_YT_DLP_FORMAT: str = "bestvideo+bestaudio/best"

#: 错误码常量（CS-001：E_<CATEGORY>_<NUMBER>_<DETAIL>）
E_DL_001: str = "E_DL_001"  # 非法 URL
E_DL_002_VERSION_TOO_OLD: str = "E_DL_002_VERSION_TOO_OLD"  # yt-dlp 版本过低
E_DL_003_NETWORK: str = "E_DL_003_NETWORK"  # 网络错误
E_DL_BILI_403: str = "E_DL_BILI_403"  # B 站 403
E_DL_LOCAL_001: str = "E_DL_LOCAL_001"  # 本地文件不存在
E_DL_LOCAL_002: str = "E_DL_LOCAL_002"  # 不支持的文件格式
E_DL_001_DENO_MISSING: str = "E_DL_001_DENO_MISSING"  # YouTube 依赖 Deno 缺失


# === 异常类型定义（领域异常，便于 M-010 错误码登记） ===

class DownloadError(Exception):
    """下载器领域异常基类。所有下载相关失败必须转换为 DownloadError 并携带 error_code。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# === 子模块 1：version_validator ===

class YtDlpVersionValidator:
    """
    [类名] YtDlpVersionValidator
    [职责]  yt-dlp 版本校验，确保 >= YTDLP_MIN_VERSION
    [关联设计规范] MD-VideoIngest-V1.1 §M-003 version_validator
    [属性]
      属性1: min_version str 最低版本要求，默认 YTDLP_MIN_VERSION
      属性2: ytdlp_path str yt-dlp 可执行路径，默认 "yt-dlp"（PATH 中查找）
    [方法列表]
      方法1: check() -> str - 执行 yt-dlp --version 并返回实际版本字符串
      方法2: compare(actual: str) -> bool - 比较实际版本与 min_version
      方法3: validate() -> bool - check + compare 一体化，失败时抛出 DownloadError
    [状态机] N/A（启动期一次性调用）
    [异常处理]
      异常1: DownloadError(E_DL_002_VERSION_TOO_OLD) - 实际版本低于 min_version
      异常2: DownloadError(E_DL_003_NETWORK) - subprocess 执行失败（非版本问题）
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
    """

    min_version: str
    ytdlp_path: str

    def __init__(self, min_version: str = YTDLP_MIN_VERSION, ytdlp_path: str = "yt-dlp") -> None:
        """
        [函数名] __init__
        [职责]  初始化版本校验器
        [参数说明]
          参数1: min_version str 必填 最低版本字符串，默认 YTDLP_MIN_VERSION
          参数2: ytdlp_path str 可选 yt-dlp 可执行路径，默认 "yt-dlp"
        [返回值] None
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...

    def check(self) -> str:
        """
        [函数名] check
        [职责]  执行 yt-dlp --version 并返回版本字符串
        [关联接口契约] IC-VideoIngest-V1.1 §IC-007 前置条件
        [参数说明] 无
        [返回值]
          类型: str
          描述: yt-dlp 实际版本字符串，格式 "2023.07.06" 或更新
          特殊值: 失败时抛出 DownloadError(E_DL_003_NETWORK)
        [错误码]
          E_DL_003_NETWORK: subprocess 执行失败（yt-dlp 不在 PATH 或执行异常）
        [前置条件] yt-dlp 已安装
        [后置条件] 返回版本字符串
        [并发安全] 否（启动期一次性）
        [性能约束] < 1s
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003 version_validator]
        """
        ...

    def compare(self, actual: str) -> bool:
        """
        [函数名] compare
        [职责]  字符串式版本号比较（YYYY.MM.DD 格式）
        [参数说明]
          参数1: actual str 必填 yt-dlp 实际版本字符串
        [返回值]
          类型: bool
          描述: True 表示实际版本 >= min_version
        [错误码] -（不抛错）
        [前置条件] actual 字符串格式合法
        [后置条件] 始终返回布尔值
        [并发安全] 是（纯函数）
        [性能约束] < 1ms
        [来源标注] [DD-M推断: YYYY.MM.DD 格式直接字典序比较即可满足需求]
        """
        ...

    def validate(self) -> bool:
        """
        [函数名] validate
        [职责]  check + compare 一体化，校验失败时抛出 DownloadError
        [关联接口契约] IC-VideoIngest-V1.1 §IC-007 前置条件
        [参数说明] 无
        [返回值]
          类型: bool
          描述: True 表示版本合法
        [错误码]
          E_DL_002_VERSION_TOO_OLD: 实际版本 < min_version
        [前置条件] yt-dlp 已安装
        [后置条件] 调用方收到 True 或异常
        [并发安全] 否
        [幂等性] 是（多次调用结果相同）
        [性能约束] < 1.5s
        [示例]
          ```
          validator = YtDlpVersionValidator()
          if not validator.validate():
              # 升级提示
          ```
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...


# === 子模块 2：youtube_downloader ===

class YouTubeDownloader:
    """
    [类名] YouTubeDownloader
    [职责]  YouTube 平台视频下载（依赖 Deno 桥接 JS 挑战）
    [关联设计规范] MD-VideoIngest-V1.1 §M-003 youtube_downloader
    [属性]
      属性1: deno_path str Deno 可执行路径（用于 yt-dlp --js-runtimes）
      属性2: cookie_path Optional[str] Cookie 文件路径，可选
      属性3: output_dir Path 输出目录，default = /tmp/research_tool/downloads
    [方法列表]
      方法1: download(url: VideoURL) -> DownloadTask - 实际下载入口
      方法2: get_metadata(url: VideoURL) -> dict - 仅获取元数据（yt-dlp --dump-json）
      方法3: _build_args(url: VideoURL) -> list[str] - 构造 yt-dlp CLI 参数
    [状态机]
      INIT → [validate_yt_dlp_version] → READY
      READY → [download] → DOWNLOADING
      DOWNLOADING → [success] → DONE
      DOWNLOADING → [error] → RETRY (1 次) → STILL_FAIL → ERROR_REPORTED
    [异常处理]
      异常1: DownloadError(E_DL_001) - 非法 URL
      异常2: DownloadError(E_DL_001_DENO_MISSING) - Deno 缺失
      异常3: DownloadError(E_DL_003_NETWORK) - 网络错误
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
    """

    deno_path: str
    cookie_path: Optional[str]
    output_dir: Path

    def __init__(
        self,
        deno_path: str = "deno",
        cookie_path: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> None:
        """
        [函数名] __init__
        [职责]  初始化 YouTube 下载器
        [参数说明]
          参数1: deno_path str 可选 Deno 可执行路径，默认 "deno"
          参数2: cookie_path str 可选 Cookie 文件路径
          参数3: output_dir Path 可选 输出目录
        [返回值] None
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...

    async def download(self, url: VideoURL) -> DownloadTask:
        """
        [函数名] download
        [职责]  异步执行 YouTube 视频下载
        [关联接口契约] IC-VideoIngest-V1.1 §IC-007
        [参数说明]
          参数1: url VideoURL 必填 视频 URL（platform=YOUTUBE）
        [返回值]
          类型: DownloadTask
          描述: 下载结果（file_path / size_mb / duration_sec / video_id / etag）
          特殊值: 失败时抛出 DownloadError
        [错误码]
          E_DL_001: url.platform != YOUTUBE
          E_DL_001_DENO_MISSING: Deno 不在 PATH
          E_DL_003_NETWORK: yt-dlp subprocess 失败（非版本问题）
        [前置条件] yt-dlp >= YTDLP_MIN_VERSION; Deno 已安装; url.platform == YOUTUBE
        [后置条件] file_path 存在 + 0o644 权限; DownloadTask 已返回
        [并发安全] 是（由调用方 M-012 Semaphore 保护）
        [幂等性]
          是否幂等: 否（下载有副作用，但命中 M-004 缓存可短路）
          幂等键来源: url + etag/last_modified
        [性能约束] 视频大小相关（10-300s，10MB-2GB）
        [示例]
          ```
          downloader = YouTubeDownloader(cookie_path="~/.config/yt-dlp/cookies.txt")
          task = await downloader.download(VideoURL(platform=YOUTUBE, url="..."))
          print(task.file_path, task.size_mb)
          ```
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003] [DD-001:IC-VideoIngest-V1.1 §IC-007]
        """
        ...

    async def get_metadata(self, url: VideoURL) -> dict:
        """
        [函数名] get_metadata
        [职责]  仅获取视频元数据（yt-dlp --dump-json），不下载
        [参数说明]
          参数1: url VideoURL 必填 视频 URL（platform=YOUTUBE）
        [返回值]
          类型: dict
          描述: 视频元数据（含 title / uploader / duration / id / ext）
        [错误码] E_DL_001 / E_DL_003_NETWORK
        [前置条件] url 非空
        [后置条件] dict 至少含 id / title / duration 字段
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 5s
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...

    def _build_args(self, url: VideoURL) -> list[str]:
        """
        [函数名] _build_args
        [职责]  构造 yt-dlp CLI 参数列表
        [参数说明]
          参数1: url VideoURL 必填
        [返回值]
          类型: list[str]
          描述: yt-dlp CLI 参数（含 --js-runtimes deno / --output / --format）
        [错误码] E_DL_001（url 非法时）
        [前置条件] url 合法
        [后置条件] args 非空
        [并发安全] 是
        [来源标注] [DD-M推断: 参数构造封装便于 M-012 测试时 mock]
        """
        ...


# === 子模块 3：bilibili_downloader ===

class BilibiliDownloader:
    """
    [类名] BilibiliDownloader
    [职责]  B 站平台视频下载（支持 Cookie 注入避免 403）
    [关联设计规范] MD-VideoIngest-V1.1 §M-003 bilibili_downloader
    [属性]
      属性1: cookie_path Optional[str] Cookie 文件路径
      属性2: output_dir Path 输出目录
    [方法列表]
      方法1: download(url: VideoURL) -> DownloadTask
      方法2: get_metadata(url: VideoURL) -> dict
      方法3: _handle_403(output: str) -> bool - 解析 yt-dlp 输出判断 403
    [状态机]
      READY → [download] → DOWNLOADING
      DOWNLOADING → [403 错误] → DENIED（不重试不绕过）
    [异常处理]
      异常1: DownloadError(E_DL_BILI_403) - B 站 403，必须由用户提供 Cookie
      异常2: DownloadError(E_DL_003_NETWORK) - 其他网络错误
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003] [调研:S-101 不绕过原则]
    """

    cookie_path: Optional[str]
    output_dir: Path

    def __init__(
        self,
        cookie_path: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> None:
        """
        [函数名] __init__
        [职责]  初始化 B 站下载器
        [参数说明]
          参数1: cookie_path str 可选 Cookie 文件路径
          参数2: output_dir Path 可选 输出目录
        [返回值] None
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...

    async def download(self, url: VideoURL) -> DownloadTask:
        """
        [函数名] download
        [职责]  异步执行 B 站视频下载
        [关联接口契约] IC-VideoIngest-V1.1 §IC-007
        [参数说明]
          参数1: url VideoURL 必填 视频 URL（platform=BILIBILI）
        [返回值]
          类型: DownloadTask
        [错误码]
          E_DL_001: url.platform != BILIBILI
          E_DL_BILI_403: B 站 403，需 Cookie
          E_DL_003_NETWORK: 网络错误
        [前置条件] url.platform == BILIBILI
        [后置条件] file_path 存在（成功时）
        [并发安全] 是
        [幂等性] 否（除非 M-004 缓存命中）
        [性能约束] 视频大小相关
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003] [DD-001:IC-VideoIngest-V1.1 §IC-007]
        """
        ...

    async def get_metadata(self, url: VideoURL) -> dict:
        """
        [函数名] get_metadata
        [职责]  仅获取 B 站视频元数据
        [参数说明]
          参数1: url VideoURL 必填
        [返回值] dict
        [错误码] E_DL_001 / E_DL_BILI_403 / E_DL_003_NETWORK
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 5s
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...

    def _handle_403(self, output: str) -> bool:
        """
        [函数名] _handle_403
        [职责]  解析 yt-dlp 输出判断是否 403
        [参数说明]
          参数1: output str 必填 yt-dlp stderr 输出
        [返回值]
          类型: bool
          描述: True 表示遇到 403
        [错误码] -（不抛错）
        [并发安全] 是
        [来源标注] [DD-M推断: 通过 stderr 中 "403" / "Forbidden" 关键词判定]
        """
        ...


# === 子模块 4：local_file_resolver ===

class LocalFileResolver:
    """
    [类名] LocalFileResolver
    [职责]  本地文件解析（mp4/webm/mkv），无下载步骤
    [关联设计规范] MD-VideoIngest-V1.1 §M-003 local_file_resolver
    [属性]
      属性1: supported_ext tuple[str, ...] 支持的文件后缀
    [方法列表]
      方法1: resolve(path: str) -> DownloadTask
      方法2: validate(path: str) -> bool - 校验文件存在 + 读权限 + 格式
      方法3: _probe_duration(path: Path) -> int - ffprobe 时长探测（可选）
    [状态机] N/A（同步解析）
    [异常处理]
      异常1: DownloadError(E_DL_LOCAL_001) - 文件不存在或不可读
      异常2: DownloadError(E_DL_LOCAL_002) - 不支持的文件格式
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003] [DD-001:IC-VideoIngest-V1.1 §IC-005/008]
    """

    supported_ext: tuple[str, ...]

    def __init__(self, supported_ext: tuple[str, ...] = LOCAL_SUPPORTED_EXTS) -> None:
        """
        [函数名] __init__
        [职责]  初始化本地文件解析器
        [参数说明]
          参数1: supported_ext tuple 可选 支持的后缀元组，默认 LOCAL_SUPPORTED_EXTS
        [返回值] None
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...

    def resolve(self, path: str) -> DownloadTask:
        """
        [函数名] resolve
        [职责]  解析本地文件路径，返回 DownloadTask
        [关联接口契约] IC-VideoIngest-V1.1 §IC-005/008
        [参数说明]
          参数1: path str 必填 文件绝对路径
        [返回值]
          类型: DownloadTask
          描述: 含 file_path / size_mb / duration_sec / video_id（path stem）
        [错误码]
          E_DL_LOCAL_001: 文件不存在或不可读
          E_DL_LOCAL_002: 不支持的文件格式
        [前置条件] path 字符串合法
        [后置条件] 路径存在 + 格式合法时返回 DownloadTask
        [并发安全] 是
        [幂等性]
          是否幂等: 是
          幂等键来源: 路径字符串
          重复请求处理: 直接返回相同结果
        [性能约束] < 1s（不含 ffprobe）/< 5s（含 ffprobe）
        [示例]
          ```
          resolver = LocalFileResolver()
          task = resolver.resolve("/data/lecture.mp4")
          print(task.file_path, task.duration_sec)
          ```
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003] [DD-001:IC-VideoIngest-V1.1 §IC-005]
        """
        ...

    def validate(self, path: str) -> bool:
        """
        [函数名] validate
        [职责]  校验文件存在 + 读权限 + 格式
        [参数说明]
          参数1: path str 必填
        [返回值]
          类型: bool
          描述: True 表示文件合法
          特殊值: 不合法时抛出 DownloadError
        [错误码] E_DL_LOCAL_001 / E_DL_LOCAL_002
        [前置条件] path 字符串合法
        [后置条件] 调用方收到 True 或异常
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 100ms
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...

    def _probe_duration(self, path: Path) -> int:
        """
        [函数名] _probe_duration
        [职责]  ffprobe 时长探测（可选步骤，依赖 ffmpeg）
        [参数说明]
          参数1: path Path 必填
        [返回值]
          类型: int
          描述: 时长（秒），失败时返回 0
        [错误码] -（失败不抛错，仅返回 0）
        [并发安全] 是
        [性能约束] < 3s
        [来源标注] [DD-M推断: ffprobe 比 ffmpeg 快，仅做时长探测]
        """
        ...


# === 子模块 5：cookie_injector ===

class CookieInjector:
    """
    [类名] CookieInjector
    [职责]  Cookie 文件安全注入（0o600 权限校验）
    [关联设计规范] MD-VideoIngest-V1.1 §M-003 cookie_injector
    [属性]
      属性1: cookie_path str Cookie 文件绝对路径
      属性2: perms int 要求的文件权限，默认 0o600
    [方法列表]
      方法1: validate_perms() -> bool - 校验文件权限
      方法2: inject_args(args: list[str]) -> list[str] - 注入 --cookies 参数
    [状态机] N/A
    [异常处理]
      异常1: DownloadError(E_DL_001) - Cookie 文件不存在或权限不合法
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003] [DD-M推断: 0o600 强制]
    """

    cookie_path: str
    perms: int

    def __init__(self, cookie_path: str, perms: int = COOKIE_REQUIRED_PERMS) -> None:
        """
        [函数名] __init__
        [职责]  初始化 Cookie 注入器
        [参数说明]
          参数1: cookie_path str 必填 Cookie 文件绝对路径
          参数2: perms int 可选 要求的文件权限（八进制），默认 0o600
        [返回值] None
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...

    def validate_perms(self) -> bool:
        """
        [函数名] validate_perms
        [职责]  校验 Cookie 文件权限是否等于 0o600
        [参数说明] 无
        [返回值]
          类型: bool
          描述: True 表示权限合法
        [错误码]
          E_DL_001: 文件不存在或权限非 0o600
        [前置条件] cookie_path 已设置
        [后置条件] 调用方收到 True 或异常
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 10ms
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003 cookie_injector]
        """
        ...

    def inject_args(self, args: list[str]) -> list[str]:
        """
        [函数名] inject_args
        [职责]  将 --cookies PATH 注入到 yt-dlp 参数列表
        [参数说明]
          参数1: args list[str] 必填 原始 yt-dlp 参数列表
        [返回值]
          类型: list[str]
          描述: 注入 Cookie 后的参数列表
        [错误码] E_DL_001（validate_perms 失败时）
        [前置条件] args 非空
        [后置条件] 返回列表含 --cookies <cookie_path>
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 1ms
        [示例]
          ```
          injector = CookieInjector("~/.config/yt-dlp/cookies.txt")
          args = injector.inject_args(["yt-dlp", "--format", "best", "URL"])
          # args = ["yt-dlp", "--format", "best", "--cookies", "~/.config/.../cookies.txt", "URL"]
          ```
        [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003]
        """
        ...


# === 模块级公开函数（IC 契约对应） ===

def validate_yt_dlp_version() -> bool:
    """
    [函数名] validate_yt_dlp_version
    [职责]  模块级封装：启动期一次性 yt-dlp 版本校验
    [关联接口契约] IC-VideoIngest-V1.1 §IC-007 前置条件
    [参数说明] 无
    [返回值]
      类型: bool
      描述: True 表示版本合法
      特殊值: 失败时抛出 DownloadError
    [错误码]
      E_DL_002_VERSION_TOO_OLD: 实际版本 < YTDLP_MIN_VERSION
      E_DL_003_NETWORK: subprocess 失败
    [前置条件] yt-dlp 已安装
    [后置条件] 返回 True 或异常
    [并发安全] 否（启动期一次性）
    [幂等性] 是
    [性能约束] < 1.5s
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003 函数签名]
    """
    ...


def download_youtube(url: VideoURL) -> DownloadTask:
    """
    [函数名] download_youtube
    [职责]  同步入口：YouTube 视频下载（实际为 async 的同步包装）
    [关联接口契约] IC-VideoIngest-V1.1 §IC-007
    [参数说明]
      参数1: url VideoURL 必填（platform=YOUTUBE）
    [返回值]
      类型: DownloadTask
      描述: 下载结果
    [错误码] E_DL_001 / E_DL_001_DENO_MISSING / E_DL_003_NETWORK
    [前置条件] url 合法 + yt-dlp 版本已校验
    [后置条件] file_path 存在
    [并发安全] 否（由调用方 M-012 Semaphore 保护）
    [幂等性] 否（除非缓存命中）
    [性能约束] 视频大小相关
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003 函数签名]
    """
    ...


def download_bilibili(url: VideoURL) -> DownloadTask:
    """
    [函数名] download_bilibili
    [职责]  同步入口：B 站视频下载
    [关联接口契约] IC-VideoIngest-V1.1 §IC-007
    [参数说明]
      参数1: url VideoURL 必填（platform=BILIBILI）
    [返回值]
      类型: DownloadTask
    [错误码] E_DL_001 / E_DL_BILI_403 / E_DL_003_NETWORK
    [前置条件] url 合法
    [后置条件] file_path 存在
    [并发安全] 否
    [幂等性] 否
    [性能约束] 视频大小相关
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003 函数签名]
    """
    ...


def resolve_local(path: str) -> DownloadTask:
    """
    [函数名] resolve_local
    [职责]  同步入口：本地文件解析
    [关联接口契约] IC-VideoIngest-V1.1 §IC-005/008
    [参数说明]
      参数1: path str 必填 文件绝对路径
    [返回值]
      类型: DownloadTask
    [错误码] E_DL_LOCAL_001 / E_DL_LOCAL_002
    [前置条件] path 合法字符串
    [后置条件] 文件存在时返回 DownloadTask
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 1s（不含 ffprobe）
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003 函数签名]
    """
    ...


def inject_cookie(args: list[str], cookie_path: str) -> list[str]:
    """
    [函数名] inject_cookie
    [职责]  模块级封装：注入 Cookie 到 yt-dlp 参数
    [关联接口契约] IC-VideoIngest-V1.1 §IC-007 cookie_path 入参
    [参数说明]
      参数1: args list[str] 必填 原始参数列表
      参数2: cookie_path str 必填 Cookie 文件路径
    [返回值]
      类型: list[str]
      描述: 注入 Cookie 后的参数
    [错误码] E_DL_001（权限校验失败）
    [前置条件] args 非空 + cookie_path 存在
    [后置条件] 返回列表含 --cookies
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 1ms
    [来源标注] [DD-001:MD-VideoIngest-V1.1 §M-003 函数签名]
    """
    ...


# === 模块入口（可选，CLI 调试用） ===

def main() -> None:
    """
    [函数名] main
    [职责]  M-003 模块 CLI 调试入口（仅开发期使用）
    [参数说明] 无
    [返回值] None
    [错误码] -（CLI 调试用，按 exit code 返回）
    [来源标注] [DD-M推断: 便于 M-001 在 CLI 模式下单独验证下载器]
    """
    ...
