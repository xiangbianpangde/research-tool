"""
[文件路径] research_tool/pipeline_adapter.py
[文件职责] M-008 管道适配器：Markdown 落盘 + 既有管道触发 + tags 合并 + Collect 配置注入
[所属模块] M-008（来自 DD-001）
[关联设计规范] FS-008 / MD-008 / IC-022 / IC-023 / IC-024（来自 DD-001）
[功能描述]
  功能1: Markdown 落盘 —— 将 M-007 拼装好的 Markdown 写入 raw/<topic>/<video-id>.md，权限 0o644
  功能2: 5 阶段管道触发 —— subprocess 异步调用既有管道的 5 阶段处理（落盘→分析→索引→通知→清理）
  功能3: tags 合并与冲突仲裁 —— 合并新 tags 与既有 tags，冲突保留两版（union + dedup 稳定排序）
  功能4: Collect 配置注入 —— 注入/校验 Collect 子系统的运行时配置（CE-009 配置版本）
[输入输出]
  输入: M-007 组装后的 Markdown 字符串、VideoMeta、Tags 列表、Collect 配置路径
  输出: 落盘后的文件绝对路径、管道阶段执行结果、合并后的 tags、配置注入结果
[依赖关系]
  依赖文件: research_tool.error_handler（M-010，错误码登记）
            research_tool.structured_logger（M-011，结构化日志）
            research_tool.datatypes（DE-002 VideoMeta / DE-006 LLMSummary / DE-008 Transcript）
  被依赖文件: research_tool.notes_schema（M-007，调用 write_markdown + trigger_pipeline）
              research_tool.cli（M-001，调用 dispatch_tasks 末段 → 落盘+管道）
[注意事项]
  注意1: 落盘前必须检查磁盘剩余空间（shutil.disk_usage free > 100MB），不足拒绝并 E_PIPE_001
  注意2: 落盘失败 / 管道失败按 1 次重试策略（CONTRACT IC-022/IC-023），重试仍失败 → M-010 登记
  注意3: 管道触发为 subprocess 异步调用，subprocess 异常 ≠ 业务异常，必须解析退出码判定
  注意4: tags 冲突保留两版（大小写不敏感去重 + 保持首次出现顺序），不允许覆盖既有 tags
  注意5: Collect 配置注入失败时不允许静默吞掉，必须回退到默认配置并 WARN
  注意6: 文件权限统一 0o644（FS-008 规范），不允许 0o600 / 0o755 等其他权限
  注意7: 5 阶段管道触发耗时 1-30s（IC-023 性能约束），超时需在配置层定义，不在本模块硬编码
[代码风格] 遵循 CS-001（来自 DD-001）
[创建日期] 2026-06-01
[修改历史]
  2026-06-01: DD-M-008 - 初始文件框架（注释 + 文件骨架，不含业务代码）
[作者] DD-M-008-20260601
[来源标注] [DD-001:FS-008/MD-008] [DD-001:IC-022/IC-023/IC-024] [DD-001:CS-001]
"""

# 1. 标准库
import os
import shutil
import subprocess  # 5 阶段管道触发使用
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# 3. 本地（来自同包，文件头注释的"依赖关系"已声明）
# 注意：以下 import 在业务代码编写时按 M-008 实际依赖解耦，注释中保留契约引用
# from research_tool.datatypes import VideoMeta, LLMSummary, Transcript
# from research_tool.error_handler import register_error
# from research_tool.structured_logger import emit_log


# =============================================================================
# 模块级常量（FS-008 / IC-022 / IC-023 / IC-024 提取）
# =============================================================================

#: 默认输出根目录（与 FS-002 部署拓扑 raw/ 目录对应）
RAW_OUTPUT_ROOT: Final[Path] = Path("raw")

#: 落盘文件权限（FS-008 规范，0o644）
FILE_PERMS_MARKDOWN: Final[int] = 0o644

#: 磁盘剩余空间下限（IC-022 前置条件，< 100MB 拒绝）
DISK_FREE_MIN_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MB

#: 落盘/管道触发最大重试次数（IC-022/IC-023 异常处理，重试 1 次）
PIPELINE_MAX_RETRY: Final[int] = 1

#: 5 阶段管道命令模板（IC-023 时序，subprocess 异步调用）
#: 占位符 {file_path} 由 trigger_pipeline 时填充
PIPELINE_CMD_TEMPLATE: Final[str] = "research-pipeline {file_path}"

#: Collect 配置 schema 版本（CE-009，不一致 → 拒绝注入并 WARN）
COLLECT_CONFIG_VERSION: Final[str] = "1.1.0"

#: 错误码（FS-008 / MD-008 异常处理）
E_PIPE_001: Final[str] = "E_PIPE_001"  # 落盘失败 / 管道失败
E_PIPE_DISK_FULL: Final[str] = "E_PIPE_DISK_FULL"  # 磁盘剩余 < 100MB
E_PIPE_CONFIG_MISMATCH: Final[str] = "E_PIPE_CONFIG_MISMATCH"  # Collect 配置版本不匹配


# =============================================================================
# 数据模型（FS-008 4 项子模块的入参/出参契约）
# =============================================================================

@dataclass
class MarkdownWriter:
    """[类名] MarkdownWriter

    [职责] 负责将 Markdown 文本落盘到 raw/<topic>/<video-id>.md。

    [关联设计规范] MD-008 子模块1（markdown_writer）

    [属性]
      属性1: output_dir Path 输出根目录（默认 RAW_OUTPUT_ROOT）
      属性2: perms int 文件权限（默认 FILE_PERMS_MARKDOWN = 0o644）
      属性3: ensure_dir_created bool 是否已确保目录存在

    [方法列表]
      方法1: write(md, topic, video_id) -> str - 落盘并返回绝对路径
      方法2: ensure_dir(topic) -> Path - 确保 raw/<topic>/ 目录存在
      方法3: chmod(path) -> None - 设置文件权限为 0o644

    [异常处理]
      异常1: E_PIPE_DISK_FULL - 磁盘剩余 < 100MB 时抛出
      异常2: E_PIPE_001 - 写文件失败（OSError/PermissionError）时抛出
    """

    output_dir: Path = RAW_OUTPUT_ROOT
    perms: int = FILE_PERMS_MARKDOWN
    ensure_dir_created: bool = False


@dataclass
class PipelineTrigger:
    """[类名] PipelineTrigger

    [职责] 通过 subprocess 触发既有管道的 5 阶段处理。

    [关联设计规范] MD-008 子模块2（pipeline_trigger）

    [属性]
      属性1: pipeline_cmd str 管道命令模板（默认 PIPELINE_CMD_TEMPLATE）

    [方法列表]
      方法1: trigger(file_path) -> StagesResult - 触发管道并返回阶段结果
      方法2: retry(file_path) -> StagesResult - 重试 1 次（IC-023 异常处理）
      方法3: parse_stages(stdout) -> list[str] - 解析 stdout 提取已运行阶段

    [异常处理]
      异常1: E_PIPE_001 - subprocess 失败（exit != 0）时抛出
    """

    pipeline_cmd: str = PIPELINE_CMD_TEMPLATE


@dataclass
class TagsMerger:
    """[类名] TagsMerger

    [职责] 合并新 tags 与既有 tags，冲突保留两版（union + dedup）。

    [关联设计规范] MD-008 子模块3（tags_merger）

    [属性]
      属性1: case_sensitive bool 是否大小写敏感（默认 False）

    [方法列表]
      方法1: merge(new_tags, existing) -> list[str] - 合并并去重
      方法2: resolve_conflict(new, existing) -> list[str] - 冲突保留两版

    [异常处理] 无（冲突保留两版，不抛错）
    """

    case_sensitive: bool = False


@dataclass
class CollectConfigInjector:
    """[类名] CollectConfigInjector

    [职责] 注入/校验 Collect 子系统的运行时配置。

    [关联设计规范] MD-008 子模块4（collect_config_injector）+ CE-009 Collect 配置

    [属性]
      属性1: config_path Path Collect 配置文件路径

    [方法列表]
      方法1: inject() -> bool - 注入配置并返回是否成功
      方法2: verify_version() -> bool - 校验 schema 版本（与 COLLECT_CONFIG_VERSION 一致）
    """

    config_path: Path = Path("~/.config/research/collect.toml").expanduser()


@dataclass
class StagesResult:
    """[类名] StagesResult

    [职责] 5 阶段管道的执行结果封装（IC-023 出参定义）。

    [属性]
      属性1: stages_run list[str] 已运行阶段列表
      属性2: success bool 是否成功
      属性3: duration_ms int 总耗时
      属性4: retried bool 是否经过重试
    """
    stages_run: list[str] = field(default_factory=list)
    success: bool = False
    duration_ms: int = 0
    retried: bool = False


# =============================================================================
# 模块级函数（MD-008 4 项函数签名）
# =============================================================================

def write_markdown(md: str, topic: str, video_id: str) -> str:
    """[函数名] write_markdown

    [职责] 将 Markdown 写入 raw/<topic>/<video-id>.md 并返回绝对路径。

    [关联接口契约] IC-022（来自 DD-001）

    [参数说明]
      参数1: md str 必填 Markdown 文本
      参数2: topic str 必填 主题分类（用于路径分段）
      参数3: video_id str 必填 视频 ID（用于文件名）

    [返回值]
      类型: str
      描述: 写入的文件绝对路径
      特殊值: 无（成功时返回路径，失败时抛出异常）

    [错误码]
      错误码1: E_PIPE_DISK_FULL - 磁盘剩余 < DISK_FREE_MIN_BYTES (100MB)
      错误码2: E_PIPE_001 - 写文件失败（OSError / PermissionError，重试 1 次后仍失败）

    [前置条件] 工作目录可写 + 磁盘剩余 > 100MB
    [后置条件] 文件存在 + 权限为 FILE_PERMS_MARKDOWN (0o644)
    [并发安全] 否（V1.1 不支持多用户并发写同一文件）
    [幂等性]
      是否幂等: 是
      幂等键来源: video_id
      幂等有效期: 永久
      重复请求处理: 覆盖写入
    [性能约束] < 1s

    [来源标注] [DD-001:IC-022] [DD-001:MD-008 子模块1]
    """
    pass  # 业务代码由 DD-S（结构设计师）填充


def trigger_pipeline(file_path: str) -> StagesResult:
    """[函数名] trigger_pipeline

    [职责] 触发既有管道的 5 阶段处理（subprocess 异步）。

    [关联接口契约] IC-023（来自 DD-001）

    [参数说明]
      参数1: file_path str 必填 落盘后的 Markdown 绝对路径

    [返回值]
      类型: StagesResult
      描述: 含 stages_run / success / duration_ms / retried
      特殊值: success=False 时 retried 表示是否已重试

    [错误码]
      错误码1: E_PIPE_001 - 管道失败（subprocess exit != 0，重试 1 次后仍失败）

    [前置条件] file_path 存在
    [后置条件] 既有管道已接收并处理
    [并发安全] 是（经 M-012 Semaphore(3)）
    [幂等性]
      是否幂等: 否
      幂等键来源: N/A
      幂等有效期: N/A
      重复请求处理: 重新触发（既有管道负责幂等）
    [性能约束] 1-30s

    [来源标注] [DD-001:IC-023] [DD-001:MD-008 子模块2] [DD-001:SR-007] [DD-001:CE-009]
    """
    pass  # 业务代码由 DD-S（结构设计师）填充


def merge_tags(new_tags: Sequence[str], existing: Sequence[str]) -> list[str]:
    """[函数名] merge_tags

    [职责] 合并新 tags 与既有 tags，冲突保留两版。

    [关联接口契约] IC-024（来自 DD-001）

    [参数说明]
      参数1: new_tags Sequence[str] 必填 新 tags 列表
      参数2: existing Sequence[str] 必填 既有 tags 列表

    [返回值]
      类型: list[str]
      描述: 合并并去重后的 tags（union，保持首次出现顺序）
      特殊值: 空列表（两个入参均为空时）

    [错误码] 无（冲突保留两版，不抛错）

    [前置条件] new_tags 非空
    [后置条件] merged_tags 含并集 + 大小写不敏感去重
    [并发安全] 是（无副作用）
    [幂等性]
      是否幂等: 是
      幂等键来源: (new_tags, existing)
      幂等有效期: 永久
      重复请求处理: 始终返回相同结果
    [性能约束] < 10ms

    [来源标注] [DD-001:IC-024] [DD-001:MD-008 子模块3]
    """
    pass  # 业务代码由 DD-S（结构设计师）填充


def inject_collect_config() -> bool:
    """[函数名] inject_collect_config

    [职责] 注入/校验 Collect 子系统的运行时配置。

    [关联接口契约] CE-009 Collect 配置（来自 DD-001）

    [参数说明] 无

    [返回值]
      类型: bool
      描述: True=注入成功且 schema 版本一致；False=注入失败
      特殊值: False 时已 WARN 日志 + 回退默认配置

    [错误码]
      错误码1: E_PIPE_CONFIG_MISMATCH - Collect 配置 schema 版本与 COLLECT_CONFIG_VERSION 不一致

    [前置条件] config_path 存在 + 可读
    [后置条件] Collect 子系统已加载新配置
    [并发安全] 是（配置加载一次性）
    [幂等性]
      是否幂等: 是
      幂等键来源: config_path 内容
      幂等有效期: 配置未变更期间
      重复请求处理: 重复注入（Collect 内部去重）
    [性能约束] < 100ms

    [来源标注] [DD-001:MD-008 子模块4] [DD-001:CE-009]
    """
    pass  # 业务代码由 DD-S（结构设计师）填充


# =============================================================================
# 模块入口（FS-008 __init__ 导出）
# =============================================================================

__all__ = [
    "MarkdownWriter",
    "PipelineTrigger",
    "TagsMerger",
    "CollectConfigInjector",
    "StagesResult",
    "write_markdown",
    "trigger_pipeline",
    "merge_tags",
    "inject_collect_config",
    "RAW_OUTPUT_ROOT",
    "FILE_PERMS_MARKDOWN",
    "DISK_FREE_MIN_BYTES",
    "PIPELINE_MAX_RETRY",
    "PIPELINE_CMD_TEMPLATE",
    "COLLECT_CONFIG_VERSION",
    "E_PIPE_001",
    "E_PIPE_DISK_FULL",
    "E_PIPE_CONFIG_MISMATCH",
]
