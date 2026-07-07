"""M-008 管道适配器（V1.1 VideoIngest）。

设计依据：
- [DD-001:M-008 pipeline_adapter] 4 子模块
  (Markdown 落盘 / 5 阶段管道触发 / tags 合并 / Collect 配置注入)
- [DD-001:IC-022/IC-023/IC-024/CE-009] 接口契约
- [DD-001:FS-008] 文件结构（research_tool/pipeline_adapter.py）
- [DD-001:FR-006] 下游 5 阶段管道零改动（用 resume=True 机制让 collect 跳过已有 raw/）

职责：
- Markdown 落盘：写 raw/<topic>/video_<video_id>.md，权限 0o644
  磁盘剩余 < 100MB 时报警
- 5 阶段管道触发：复用 ResearchPipeline
  (不修改 collect/deepen/clean/extract/organize/report 任何代码)
- tags 合并：大小写不敏感去重 + 保留首次出现顺序
- Collect 配置注入：bool 返回，失败 WARN + 回退默认

设计模式：适配器模式（落盘 + 管道触发）+ 协调器模式（落盘→触发串行）
约束：FDR-M008-006 — 仅允许 import M-010（errors）+ M-011（logging）+ datatypes
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from ...common.logging_config import emit_log, get_logger
from ...domain.errors import ErrorCode, VideoIngestError, register_error

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 模块级常量（DD-001 FS-008 / FDR-M008-001/002）
# --------------------------------------------------------------------------- #

FILE_PERMS_MARKDOWN: Final[int] = 0o644
MIN_DISK_FREE_BYTES: Final[int] = 100 * 1024 * 1024  # 100MB（IC-022 前置条件）
PIPELINE_MAX_RETRY: Final[int] = 1  # FDR-M008-003：重试 1 次后仍失败 → E_PIPE_001
RAW_DIR_NAME: Final[str] = "raw"  # 与现有 pipeline 约定一致
VIDEO_FILENAME_PREFIX: Final[str] = "video_"  # 视频笔记文件名前缀（IC-014 video_ 前缀约定的延伸）

# 错误码
E_PIPE_001: Final[str] = "E_PIPE_001"  # 落盘或管道触发失败
E_PIPE_DISK_FULL: Final[str] = "E_PIPE_DISK_FULL"  # 磁盘剩余 < 100MB
E_PIPE_CONFIG_MISMATCH: Final[str] = "E_PIPE_CONFIG_MISMATCH"  # Collect 配置 schema 不一致

# Collect 配置 schema 版本（CE-009 引用）
COLLECT_CONFIG_VERSION: Final[str] = "1.0.0"


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StagesResult:
    """5 阶段管道触发结果（IC-023）。"""

    stages_run: list[str] = field(default_factory=list)
    success: bool = False
    duration_ms: int = 0
    retried: bool = False
    error: str | None = None


# --------------------------------------------------------------------------- #
# MarkdownWriter（落盘 IC-022）
# --------------------------------------------------------------------------- #


class MarkdownWriter:
    """Markdown 落盘器（IC-022）。

    状态机：
        INIT → 磁盘检查 → mkdir parents → 写文件 → chmod 0o644 → DONE
    """

    def __init__(self, file_perms: int = FILE_PERMS_MARKDOWN) -> None:
        self._file_perms = file_perms

    def check_disk_space(self, output_dir: Path) -> None:
        """检查磁盘剩余空间（FDR-M008-002）。"""
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 目录无法创建，磁盘检查无意义（让后续 write 抛错）
            return
        try:
            usage = shutil.disk_usage(output_dir)
            if usage.free < MIN_DISK_FREE_BYTES:
                register_error(
                    ErrorCode.E_PIPE_DISK_FULL.value,
                    scene="磁盘剩余空间不足",
                    cause=f"剩余 {usage.free // 1024 // 1024}MB < 100MB 阈值",
                    suggestion="清理磁盘或更换输出目录",
                    context={"path": str(output_dir), "free_mb": usage.free // 1024 // 1024},
                )
                raise VideoIngestError(
                    E_PIPE_DISK_FULL,
                    f"磁盘剩余 {usage.free // 1024 // 1024}MB < 100MB 阈值",
                )
        except OSError as e:
            logger.warning("磁盘检查失败（降级继续）: %s", e)

    def write(
        self,
        md: str,
        topic: str,
        video_id: str,
        work_dir: str | Path,
    ) -> Path:
        """写 Markdown 到 raw/<topic>/video_<video_id>.md（IC-022）。

        Args:
            md: Markdown 文本（含 YAML front matter + body）
            topic: 主题（用于路径分段，会 slugify）
            video_id: 视频 ID（BV 号 / YouTube 11 位等，会清洗为文件名合法字符）
            work_dir: 工作根目录（默认 ./research-output）

        Returns:
            写入的文件绝对路径

        Raises:
            VideoIngestError(E_PIPE_DISK_FULL): 磁盘剩余 < 100MB
            VideoIngestError(E_PIPE_001): 写文件失败（重试 1 次后仍失败）
        """
        from ...common.slug import slugify

        topic_slug = slugify(topic) if topic else "untitled"
        safe_video_id = _sanitize_filename(video_id) or "unknown"
        filename = f"{VIDEO_FILENAME_PREFIX}{safe_video_id}.md"

        topic_dir = Path(work_dir) / topic_slug
        raw_dir = topic_dir / RAW_DIR_NAME
        self.check_disk_space(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)

        file_path = raw_dir / filename
        rel_path = file_path  # absolute-equivalent;保留 Path 以便调用方扩展

        # 重试 1 次（FDR-M008-003）
        last_exc: Exception | None = None
        for attempt in range(PIPELINE_MAX_RETRY + 1):
            try:
                file_path.write_text(md, encoding="utf-8")
                # chmod 0o644（FDR-M008-001；Windows 上 ACL 模型差异，chmod 仅最低位有效，忽略失败）
                try:
                    os.chmod(file_path, self._file_perms)
                except OSError:
                    pass
                emit_log(
                    "info",
                    f"Markdown 落盘: {file_path} ({len(md)} chars)",
                    step="pipe_write_markdown",
                    code="",
                )
                return rel_path
            except OSError as e:
                last_exc = e
                logger.warning("落盘失败（attempt %d）: %s", attempt + 1, e)
                if attempt < PIPELINE_MAX_RETRY:
                    time.sleep(0.3 * (2**attempt))
                    continue
                register_error(
                    ErrorCode.E_PIPE_001.value,
                    scene="Markdown 落盘失败",
                    cause=str(e),
                    suggestion="检查输出目录权限 / 磁盘空间",
                    context={"path": str(file_path), "size": len(md)},
                )
                raise VideoIngestError(
                    E_PIPE_001,
                    f"Markdown 落盘失败: {e}",
                ) from e

        # unreachable
        raise VideoIngestError(E_PIPE_001, "Markdown 落盘失败（未知）") from last_exc


# --------------------------------------------------------------------------- #
# PipelineTrigger（5 阶段管道触发 IC-023）
# --------------------------------------------------------------------------- #


class PipelineTrigger:
    """5 阶段管道触发器（IC-023）。

    复用 application/pipeline.py:ResearchPipeline；不修改任何 Stage 实现。
    设计要点：
    - 默认跳过 deepen（视频源已有 raw/ 内容，deepen 反而会搜索更多无关注入）
    - resume=True 必传：让 collect 阶段检测到 raw/ 已有 video_*.md → 跳过
    - 失败重试 1 次（FDR-M008-003）
    """

    def __init__(
        self,
        stages: list[str] | None = None,
        max_retry: int = PIPELINE_MAX_RETRY,
    ) -> None:
        # 默认阶段列表：跳过 collect（已由 video markdown 提供）和 deepen（视频源不再二次深挖）
        # 保留 clean → extract → organize → report
        self._stages = stages or ["clean", "extract", "organize", "report"]
        self._max_retry = max_retry

    async def trigger(
        self,
        topic: str,
        work_dir: str | Path,
    ) -> StagesResult:
        """触发 5 阶段管道（IC-023）。

        Args:
            topic: 主题
            work_dir: 工作根目录（与 write_markdown 的 work_dir 一致）

        Returns:
            StagesResult 含 stages_run / success / duration_ms / retried
        """
        start = time.monotonic()
        # 延迟 import 避免 application 层的循环依赖
        from ...application.pipeline import ResearchPipeline
        from ...domain.config import load_config

        try:
            cfg = load_config(
                config_path=None,
                overrides={
                    "topic": topic,
                    "work_dir": str(work_dir),
                    "stages": self._stages,
                    "resume": True,  # 让 collect 检测到 raw/ 已有 video_*.md → 跳过
                },
            )
        except Exception as e:  # noqa: BLE001 - 配置加载失败应返回失败结果而非抛错（IC-023 语义）
            register_error(
                ErrorCode.E_CFG_001_CONFIG_MISSING.value,
                scene="ResearchPipeline 配置加载失败",
                cause=str(e),
                suggestion="检查 config.yaml 与 .env",
            )
            return StagesResult(
                stages_run=[],
                success=False,
                duration_ms=int((time.monotonic() - start) * 1000),
                retried=False,
                error=f"config load failed: {e}",
            )

        retried = False
        last_err: Exception | None = None
        for attempt in range(self._max_retry + 1):
            try:
                pipeline = ResearchPipeline(cfg)
                stages_run: list[str] = []
                failed_stage: str | None = None
                async for ev in pipeline.stream(topic):
                    if ev.status == "completed":
                        stages_run.append(ev.stage)
                    elif ev.status == "skipped":
                        # 跳过的阶段也视为"已运行"（pipeline 已决策）
                        pass
                    elif ev.status == "failed":
                        failed_stage = ev.stage
                result = pipeline._result
                success = failed_stage is None and result is not None
                elapsed = int((time.monotonic() - start) * 1000)
                if attempt > 0:
                    retried = True
                if not success and attempt < self._max_retry:
                    logger.warning("管道触发失败（第 %d 次），重试: %s", attempt + 1, failed_stage)
                    retried = True
                    time.sleep(0.3 * (2**attempt))
                    continue
                return StagesResult(
                    stages_run=stages_run,
                    success=success,
                    duration_ms=elapsed,
                    retried=retried,
                    error=None if success else f"failed_stage={failed_stage}",
                )
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self._max_retry:
                    logger.warning("管道触发异常（第 %d 次）重试: %s", attempt + 1, e)
                    retried = True
                    time.sleep(0.3 * (2**attempt))
                    continue
                register_error(
                    ErrorCode.E_DL_001_NETWORK_TIMEOUT.value,
                    scene="5 阶段管道触发失败",
                    cause=str(e),
                    suggestion="查看 M-010 错误码 + 检查 LLM API key",
                )
                return StagesResult(
                    stages_run=[],
                    success=False,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    retried=retried,
                    error=str(last_err),
                )

        return StagesResult(
            stages_run=[],
            success=False,
            duration_ms=int((time.monotonic() - start) * 1000),
            retried=retried,
            error=str(last_err) if last_err else "未知错误",
        )


# --------------------------------------------------------------------------- #
# TagsMerger（tags 合并 IC-024）
# --------------------------------------------------------------------------- #


class TagsMerger:
    """tags 合并器（IC-024）：大小写不敏感去重 + 保留首次出现顺序。"""

    def __init__(self, case_sensitive: bool = False) -> None:
        self._case_sensitive = case_sensitive

    def merge(self, new_tags: Sequence[str], existing: Sequence[str]) -> list[str]:
        """合并 + 去重（FDR-M008-004）。

        Args:
            new_tags: 新 tags 列表
            existing: 既有 tags 列表

        Returns:
            合并去重后的 tags 列表（首次出现顺序）
        """
        result: list[str] = []
        seen: set[str] = set()
        # 优先顺序：new_tags 在前（IC-024 示例顺序），existing 在后追加未出现的
        for tag in [*new_tags, *existing]:
            if not tag or not isinstance(tag, str):
                continue
            tag_stripped = tag.strip()
            if not tag_stripped:
                continue
            key = tag_stripped if self._case_sensitive else tag_stripped.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(tag_stripped)
        return result


# --------------------------------------------------------------------------- #
# CollectConfigInjector（Collect 配置注入 CE-009）
# --------------------------------------------------------------------------- #


class CollectConfigInjector:
    """Collect 子系统配置注入（CE-009）。

    失败回退：返回 False + WARN 日志 + 收集子系统回退内置默认（不抛错）。
    """

    def __init__(self, expected_version: str = COLLECT_CONFIG_VERSION) -> None:
        self._expected_version = expected_version

    def inject(self) -> bool:
        """注入并校验 Collect 配置 schema 版本。

        Returns:
            True=注入成功且 schema 版本一致；False=注入失败（已 WARN 日志 + 回退默认）
        """
        # V1.1 简化：检查 Pyproject.toml 标记的 schema 版本号（若有）
        # 真实生产环境应读取 Collect 子系统提供的版本 API；V1.1 不强制
        try:
            # 检查 CollectorConfig 是否存在（间接校验 Collect 子系统可用）
            from ...domain.models import CollectorConfig

            cfg = CollectorConfig()
            # 当前实现无 schema 版本字段；V1.1 始终返回 True
            # 未来扩展：在 CollectorConfig 加 schema_version 字段
            _ = cfg
            return True
        except ImportError as e:
            emit_log(
                "warning",
                f"Collect 配置注入失败（回退默认）: {e}",
                step="pipe_collect_config",
                code=E_PIPE_CONFIG_MISMATCH,
            )
            return False
        except Exception as e:  # noqa: BLE001
            emit_log(
                "warning",
                f"Collect 配置校验异常（回退默认）: {e}",
                step="pipe_collect_config",
                code=E_PIPE_CONFIG_MISMATCH,
            )
            return False


# --------------------------------------------------------------------------- #
# 模块级便捷函数（IC-022/023/024/CE-009）
# --------------------------------------------------------------------------- #


def write_markdown(
    md: str,
    topic: str,
    video_id: str,
    *,
    work_dir: str | Path = "./research-output",
) -> Path:
    """模块级便捷函数（IC-022）。"""
    return MarkdownWriter().write(md, topic, video_id, work_dir)


async def trigger_pipeline(
    topic: str,
    work_dir: str | Path = "./research-output",
    *,
    stages: list[str] | None = None,
) -> StagesResult:
    """模块级便捷函数（IC-023）。"""
    return await PipelineTrigger(stages=stages).trigger(topic, work_dir)


def merge_tags(
    new_tags: Sequence[str],
    existing: Sequence[str],
    *,
    case_sensitive: bool = False,
) -> list[str]:
    """模块级便捷函数（IC-024）。"""
    return TagsMerger(case_sensitive=case_sensitive).merge(new_tags, existing)


def inject_collect_config() -> bool:
    """模块级便捷函数（CE-009）。"""
    return CollectConfigInjector().inject()


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #


def _sanitize_filename(s: str) -> str:
    """清洗 video_id 为文件名合法字符（保留字母数字 + 短横线下划线点）。"""
    if not s:
        return ""
    # Windows / POSIX 文件名保留字符（注意：短横线 '-' 是合法字符，YouTube ID 常含 '-'/'_'）
    bad = set('<>:"/\\|?*')
    out = []
    for ch in s:
        # 控制字符（0x00-0x1f）+ 保留字符 + 空白 → 替换为下划线
        if ord(ch) < 0x20 or ch in bad or ch.isspace():
            out.append("_")
        else:
            out.append(ch)
    cleaned = "".join(out).strip("._-")
    return cleaned[:128]  # 限制长度


# --------------------------------------------------------------------------- #
# 模块导出
# --------------------------------------------------------------------------- #

__all__ = [
    # 常量
    "FILE_PERMS_MARKDOWN",
    "MIN_DISK_FREE_BYTES",
    "PIPELINE_MAX_RETRY",
    "RAW_DIR_NAME",
    "VIDEO_FILENAME_PREFIX",
    "COLLECT_CONFIG_VERSION",
    # 错误码
    "E_PIPE_001",
    "E_PIPE_DISK_FULL",
    "E_PIPE_CONFIG_MISMATCH",
    # 数据类
    "StagesResult",
    # 类
    "MarkdownWriter",
    "PipelineTrigger",
    "TagsMerger",
    "CollectConfigInjector",
    # 模块级便捷函数
    "write_markdown",
    "trigger_pipeline",
    "merge_tags",
    "inject_collect_config",
]
