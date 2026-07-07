"""统一异常体系 + 错误码登记 + 3 段式报错。

依据 03-Python库接口设计.md §7 + DD-001 M-010 错误处理器。

扩展点（V1.1 VideoIngest）：
- 13 个 E_* 错误码常量（download/transcribe/ffmpeg/llm/cache/preflight/...）
- ErrorRecord 数据类（DE-010）：错误码 + 场景/原因/建议 + 触发时间
- register_error / format_error / resolve_exit_code / lookup_code 公共 API
- 进程退出码仲裁：403 > 401 > 500 > 0（DD-001 M-010 状态机）
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------- #
# 异常基类（V1.0 已有，保持向后兼容）
# --------------------------------------------------------------------------- #


class ResearchToolError(Exception):
    """所有本工具异常的基类。"""


class ConfigValidationError(ResearchToolError):
    """配置加载/校验失败。附带详细错误信息与修复建议。"""


class StageError(ResearchToolError):
    """某个 Stage 执行失败。"""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage}] {message}")


class LLMError(ResearchToolError):
    """LLM 调用失败。"""


class SearchError(ResearchToolError):
    """搜索后端失败。"""


class CollectError(StageError):
    """采集阶段失败。"""

    def __init__(self, message: str) -> None:
        super().__init__("collect", message)


class UrlBlockedError(ResearchToolError):
    """URL rejected by SSRF guard (internal/private/link-local/cloud-metadata)。"""


# --------------------------------------------------------------------------- #
# V1.1 新增：异常子类（按 12 个错误码家族）
# --------------------------------------------------------------------------- #


class VideoIngestError(ResearchToolError):
    """VideoIngest 流程失败（V1.1 新增）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.timestamp = time.time()
        super().__init__(f"[{code}] {message}")


class DownloadError(VideoIngestError):
    """视频下载失败（yt-dlp / 网络）。"""


class TranscribeError(VideoIngestError):
    """转写失败（faster-whisper）。"""


class FFmpegError(VideoIngestError):
    """ffmpeg/ffprobe 子进程失败。"""


class CacheError(VideoIngestError):
    """缓存读写失败。"""


class PreflightError(VideoIngestError):
    """预检失败（缺关键工具）。"""


class ConfigError(VideoIngestError):
    """配置缺失/非法。"""


# --------------------------------------------------------------------------- #
# V1.1 新增：13 个错误码常量（DD-001 M-010 错误码字典）
# --------------------------------------------------------------------------- #


class ErrorCode(str, Enum):
    """13 个 VideoIngest 错误码（V1.1 范围）。

    命名规则：[E_]_[CATEGORY]_[NUMBER]_[DETAIL]
    - E_VID_* = 视频相关
    - E_DL_*  = 下载（yt-dlp）
    - E_TX_*  = 转写（faster-whisper）
    - E_FM_*  = ffmpeg
    - E_LLM_* = LLM
    - E_CK_*  = 缓存
    - E_PF_*  = 预检
    - E_CFG_* = 配置
    - E_SYS_* = 系统级
    """

    # 视频（3）
    E_VID_001_VIDEO_NOT_FOUND = "E_VID_001_VIDEO_NOT_FOUND"
    E_VID_002_INVALID_URL = "E_VID_002_INVALID_URL"
    E_VID_003_PIPELINE_FAIL = "E_VID_003_PIPELINE_FAIL"

    # 下载（2）
    E_DL_001_NETWORK_TIMEOUT = "E_DL_001_NETWORK_TIMEOUT"
    E_DL_002_YT_DLP_FAILED = "E_DL_002_YT_DLP_FAILED"

    # 转写（2）
    E_TX_001_WHISPER_INIT_FAILED = "E_TX_001_WHISPER_INIT_FAILED"
    E_TX_002_AUDIO_EXTRACT_FAILED = "E_TX_002_AUDIO_EXTRACT_FAILED"

    # ffmpeg（1）
    E_FM_001_FFMPEG_INVOKE_FAILED = "E_FM_001_FFMPEG_INVOKE_FAILED"

    # LLM（1）
    E_LLM_001_LLM_CALL_FAILED = "E_LLM_001_LLM_CALL_FAILED"

    # 缓存（1）
    E_CK_001_CACHE_DB_UNAVAILABLE = "E_CK_001_CACHE_DB_UNAVAILABLE"

    # 预检（1）
    E_PF_001_TOOL_MISSING = "E_PF_001_TOOL_MISSING"

    # 配置（1）
    E_CFG_001_CONFIG_MISSING = "E_CFG_001_CONFIG_MISSING"

    # 系统（1）
    E_SYS_001_UNKNOWN_ERROR_CODE = "E_SYS_001_UNKNOWN_ERROR_CODE"


# --------------------------------------------------------------------------- #
# V1.1 新增：ErrorInfo（错误码字典项）+ ErrorRecord（DE-010）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ErrorInfo:
    """错误码字典条目（lookup 表的一项）。

    Attributes:
        code: 错误码字符串（来自 ErrorCode）
        category: 类别缩写（DL/TX/FM/LLM/CK/PF/CFG/SYS/VID）
        exit_code_hint: 退出码建议（403/401/500/0），用于 resolve_exit_code
        default_scene: 默认场景描述（"发生了什么"）
        default_cause: 默认原因描述（"为什么发生"）
        default_suggestion: 默认修复建议（"如何解决"）
    """

    code: str
    category: str
    exit_code_hint: int
    default_scene: str
    default_cause: str
    default_suggestion: str


@dataclass
class ErrorRecord:
    """DE-010 错误记录（一次失败的现场快照）。

    Attributes:
        code: 错误码（ErrorCode.value）
        scene: 场景描述（覆盖 default_scene）
        cause: 原因描述（覆盖 default_cause）
        suggestion: 修复建议（覆盖 default_suggestion）
        timestamp: 触发时间（epoch 秒）
        stack: 异常堆栈（可选）
        context: 额外上下文（module/URL/task_id 等）
    """

    code: str
    scene: str
    cause: str
    suggestion: str
    timestamp: float = field(default_factory=time.time)
    stack: str = ""
    context: dict = field(default_factory=dict)

    def to_3section(self) -> str:
        """3 段式输出：场景/原因/建议（中文模板）。

        Returns:
            多行字符串，便于 CLI 打印或日志写入。
        """
        return f"场景: {self.scene}\n" f"原因: {self.cause}\n" f"建议: {self.suggestion}"


# --------------------------------------------------------------------------- #
# V1.1 新增：错误码注册表 + 登记 / 格式化 / 仲裁 API
# --------------------------------------------------------------------------- #


# 默认错误码字典（V1.1 全部 13 个；扩展时往 _ERROR_REGISTRY 追加）
_ERROR_REGISTRY: dict[str, ErrorInfo] = {
    # 视频
    ErrorCode.E_VID_001_VIDEO_NOT_FOUND.value: ErrorInfo(
        code=ErrorCode.E_VID_001_VIDEO_NOT_FOUND.value,
        category="VID",
        exit_code_hint=404,
        default_scene="视频不存在或已被删除",
        default_cause="URL 对应的视频在源平台上下线/设为私有",
        default_suggestion="检查 URL 是否可手动打开；或换一个 URL 重试",
    ),
    ErrorCode.E_VID_002_INVALID_URL.value: ErrorInfo(
        code=ErrorCode.E_VID_002_INVALID_URL.value,
        category="VID",
        exit_code_hint=400,
        default_scene="URL 格式非法",
        default_cause="输入字符串不是支持的平台 URL（Bilibili/YouTube/X 等）",
        default_suggestion="复制浏览器地址栏的完整 URL 重新输入",
    ),
    ErrorCode.E_VID_003_PIPELINE_FAIL.value: ErrorInfo(
        code=ErrorCode.E_VID_003_PIPELINE_FAIL.value,
        category="VID",
        exit_code_hint=500,
        default_scene="视频管道处理失败",
        default_cause="所有视频 URL 处理失败（下载/转写/总结均未成功）",
        default_suggestion="检查上游错误；或减少 URL 数量重试",
    ),
    # 下载
    ErrorCode.E_DL_001_NETWORK_TIMEOUT.value: ErrorInfo(
        code=ErrorCode.E_DL_001_NETWORK_TIMEOUT.value,
        category="DL",
        exit_code_hint=500,
        default_scene="下载超时",
        default_cause="网络不稳定或远端服务器响应慢",
        default_suggestion="检查网络后重试；可调大 config.yaml 的 network.timeout_sec",
    ),
    ErrorCode.E_DL_002_YT_DLP_FAILED.value: ErrorInfo(
        code=ErrorCode.E_DL_002_YT_DLP_FAILED.value,
        category="DL",
        exit_code_hint=500,
        default_scene="yt-dlp 调用失败",
        default_cause="yt-dlp 二进制缺失/版本过旧/无法解析该 URL",
        default_suggestion="pip install -U yt-dlp；确认 URL 在浏览器可正常打开",
    ),
    # 转写
    ErrorCode.E_TX_001_WHISPER_INIT_FAILED.value: ErrorInfo(
        code=ErrorCode.E_TX_001_WHISPER_INIT_FAILED.value,
        category="TX",
        exit_code_hint=500,
        default_scene="Whisper 模型初始化失败",
        default_cause="faster-whisper 未安装/模型权重缺失/CUDA 不可用",
        default_suggestion="pip install faster-whisper；或切到 medium → small/base 降档",
    ),
    ErrorCode.E_TX_002_AUDIO_EXTRACT_FAILED.value: ErrorInfo(
        code=ErrorCode.E_TX_002_AUDIO_EXTRACT_FAILED.value,
        category="TX",
        exit_code_hint=500,
        default_scene="音频抽取失败",
        default_cause="ffmpeg 抽音轨子进程失败/视频无音轨/格式不受支持",
        default_suggestion="检查 ffmpeg 是否安装；用 ffprobe 确认音轨存在",
    ),
    # ffmpeg
    ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value: ErrorInfo(
        code=ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
        category="FM",
        exit_code_hint=500,
        default_scene="ffmpeg 调用失败",
        default_cause="ffmpeg 二进制缺失/参数错误/超时",
        default_suggestion="ffmpeg -version 验证；用更小的 count 重试",
    ),
    # LLM
    ErrorCode.E_LLM_001_LLM_CALL_FAILED.value: ErrorInfo(
        code=ErrorCode.E_LLM_001_LLM_CALL_FAILED.value,
        category="LLM",
        exit_code_hint=500,
        default_scene="LLM 调用失败",
        default_cause="API key 缺失/余额不足/5xx 重试超限",
        default_suggestion="检查 config.yaml 的 llm.api_key；重试或换 provider",
    ),
    # 缓存
    ErrorCode.E_CK_001_CACHE_DB_UNAVAILABLE.value: ErrorInfo(
        code=ErrorCode.E_CK_001_CACHE_DB_UNAVAILABLE.value,
        category="CK",
        exit_code_hint=500,
        default_scene="缓存数据库不可用",
        default_cause="sqlite 文件锁/磁盘满/权限不足",
        default_suggestion="检查 cache_dir 路径与写权限；可手动删除损坏的 .db",
    ),
    # 预检
    ErrorCode.E_PF_001_TOOL_MISSING.value: ErrorInfo(
        code=ErrorCode.E_PF_001_TOOL_MISSING.value,
        category="PF",
        exit_code_hint=403,
        default_scene="环境工具缺失",
        default_cause="yt-dlp / ffmpeg / faster-whisper 至少一项未安装",
        default_suggestion="pip install -e .[video]；ffmpeg 走系统包管理器",
    ),
    # 配置
    ErrorCode.E_CFG_001_CONFIG_MISSING.value: ErrorInfo(
        code=ErrorCode.E_CFG_001_CONFIG_MISSING.value,
        category="CFG",
        exit_code_hint=401,
        default_scene="配置缺失",
        default_cause="config.yaml 不存在或必填字段缺失（llm.api_key 等）",
        default_suggestion="cp docs/config.example.yaml config.yaml 并填入 API key",
    ),
    # 系统
    ErrorCode.E_SYS_001_UNKNOWN_ERROR_CODE.value: ErrorInfo(
        code=ErrorCode.E_SYS_001_UNKNOWN_ERROR_CODE.value,
        category="SYS",
        exit_code_hint=500,
        default_scene="错误码未注册",
        default_cause="register_error 收到不在 _ERROR_REGISTRY 中的 code",
        default_suggestion="检查代码拼写；如确为新错误码，先 lookup_code 注册再调用",
    ),
}


# 线程安全：单进程内 _ERROR_REGISTRY 修改与查询都加锁
_REGISTRY_LOCK = threading.Lock()


def lookup_code(code: str) -> ErrorInfo:
    """查询错误码字典（IC-026）。

    Args:
        code: 错误码字符串（ErrorCode.value）

    Returns:
        对应的 ErrorInfo（含默认 scene/cause/suggestion）

    Raises:
        ConfigError: code 不在 _ERROR_REGISTRY（未知错误码）
    """
    with _REGISTRY_LOCK:
        info = _ERROR_REGISTRY.get(code)
    if info is None:
        raise ConfigError(
            ErrorCode.E_SYS_001_UNKNOWN_ERROR_CODE.value,
            f"未注册错误码: {code!r}",
        )
    return info


def register_error(
    code: str,
    *,
    scene: str | None = None,
    cause: str | None = None,
    suggestion: str | None = None,
    context: dict | None = None,
    include_stack: bool = True,
) -> ErrorRecord:
    """登记一次错误，返回 ErrorRecord（IC-026 / DE-010）。

    Args:
        code: 错误码（必须已在 _ERROR_REGISTRY 中）
        scene: 场景覆盖（None=用 default_scene）
        cause: 原因覆盖
        suggestion: 建议覆盖
        context: 额外上下文（module/URL/task_id 等，会写入 ErrorRecord.context）
        include_stack: 是否捕获当前调用堆栈

    Returns:
        ErrorRecord 实例

    Raises:
        ConfigError: code 未注册（E_SYS_001）
    """
    info = lookup_code(code)  # 失败时抛 E_SYS_001
    stack_str = "".join(traceback.format_stack()[:-1]) if include_stack else ""
    record = ErrorRecord(
        code=code,
        scene=scene or info.default_scene,
        cause=cause or info.default_cause,
        suggestion=suggestion or info.default_suggestion,
        context=context or {},
        stack=stack_str,
    )
    return record


def format_error(record: ErrorRecord) -> str:
    """3 段式格式化（IC-027）：场景/原因/建议。

    Args:
        record: ErrorRecord 实例

    Returns:
        多行字符串（中文模板），可直接打印或写日志
    """
    lines = [
        f"[{record.code}]",
        f"  场景: {record.scene}",
        f"  原因: {record.cause}",
        f"  建议: {record.suggestion}",
    ]
    if record.context:
        ctx = ", ".join(f"{k}={v!r}" for k, v in record.context.items())
        lines.append(f"  上下文: {ctx}")
    return "\n".join(lines)


def resolve_exit_code(records: list[ErrorRecord]) -> int:
    """状态机仲裁：取所有记录中优先级最高的退出码（DD-001 M-010）。

    优先级：403 > 401 > 404 > 400 > 500 > 0
    - 403 = 预检/工具缺失（用户需先安装，阻断一切）
    - 401 = 配置缺失（用户需填 API key）
    - 404 = 视频不存在/已下线（用户需换 URL）
    - 400 = URL 非法/格式错（用户需改正输入）
    - 500 = 系统/网络/转写/ffmpeg/LLM/缓存（瞬时/重试可解）
    - 0   = 无错误

    确定性错误（404/400，用户必须处理）排在瞬时错误（500，可重试）之前——
    无论是否遇到瞬时抖动，用户都得修 URL。本序列扩展自 DD-001 M-010 原始
    状态机（原仅考虑 403/401/500），覆盖 V1.1 新增的 404/400 提示。

    防御性兜底：任何未列入上述序列的非零 hint 仍返回非零退出码，
    避免新增 hint 值时再次落入“非零却退出 0”的陷阱。

    Args:
        records: ErrorRecord 列表

    Returns:
        进程退出码（int），0=无错误
    """
    if not records:
        return 0
    codes = [lookup_code(r.code).exit_code_hint for r in records]
    # 优先级序列（高→低）：环境阻断 > 配置缺失 > 用户输入错误 > 瞬时错误
    for priority in (403, 401, 404, 400, 500):
        if priority in codes:
            return priority
    # 防御性兜底：未列入序列的非零 hint 仍退出非零，杜绝“非零却返回 0”
    non_zero = [c for c in codes if c != 0]
    return non_zero[0] if non_zero else 0


# --------------------------------------------------------------------------- #
# 模块导出
# --------------------------------------------------------------------------- #


__all__ = [
    # V1.0 异常
    "ResearchToolError",
    "ConfigValidationError",
    "StageError",
    "LLMError",
    "SearchError",
    "CollectError",
    "UrlBlockedError",
    # V1.1 新增
    "VideoIngestError",
    "DownloadError",
    "TranscribeError",
    "FFmpegError",
    "CacheError",
    "PreflightError",
    "ConfigError",
    "ErrorCode",
    "ErrorInfo",
    "ErrorRecord",
    "lookup_code",
    "register_error",
    "format_error",
    "resolve_exit_code",
]
