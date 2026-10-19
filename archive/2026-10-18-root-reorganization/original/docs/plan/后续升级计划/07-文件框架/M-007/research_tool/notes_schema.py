"""
M-007 笔记组装器（notes_schema）

[文件路径] research_tool/notes_schema.py
[文件职责] 6 步 Markdown 拼装（YAML/VideoMeta/章节降级/截图/参考/Markdown 总装）
[所属模块] M-007（来自 DD-001 分配）
[关联设计规范] MD-007（DD-001 模块细化方案）/ FS-007（文件结构）/ IC-016/017/018/019/020/021（接口契约）
[设计模式] 管道-过滤器 + 模板方法（6 步 Markdown 拼装）

[功能描述]
  功能1: 解析 YAML front_matter 并校验 video_ 字段名前缀
  功能2: 注入 VideoMeta（标题/作者/时长/平台）到 front_matter
  功能3: 章节降级：LLM 未返回 video_chapters 时按 5min 等距切片
  功能4: 嵌入截图引用 ![](path)，路径不存在时占位图兜底
  功能5: 生成 ## 参考来源 章节（URL/平台/作者）
  功能6: 总装 front_matter + body + screenshots + references 为完整 Markdown

[输入输出]
  输入: VideoMeta + LLMSummary + Transcript + list[ScreenshotFrame]（由 M-001 编排时注入）
  输出: str Markdown 文本（交付 M-008 落盘）

[依赖关系]
  依赖文件:
    - research_tool.datatypes (DE-002 VideoMeta / DE-006 LLMSummary / DE-008 Transcript / DE-005 ScreenshotFrame / DE-003 Chapter)
    - research_tool.error_handler (M-010，降级与错误码登记)
    - research_tool.structured_logger (M-011，JSON Lines 日志)
  被依赖文件:
    - research_tool.pipeline_adapter (M-008，调用本模块输出 Markdown)
    - research_tool.cli (M-001，调用本模块 assemble_markdown)

[注意事项]
  注意1: 本模块不导入 M-005/M-006/M-009，避免反向依赖；产出物经参数注入
  注意2: 6 步均为幂等操作，相同输入必产生相同输出
  注意3: 降级路径必须记录 WARN 日志到 M-011
  注意4: YAML 解析失败不阻塞主链（字段 fallback 到 null）
  注意5: 钩子方法 _build_chapter_fallback 是 EP-002 扩展点（V1.2 可替换）

[代码风格] 遵循 CS-001 Python 风格（DD-001）
[创建日期] 2026-06-01
[修改历史]
  2026-06-01: DD-M-007 - 初始文件框架（仅注释，无业务代码）
[作者] DD-M-007-20260601
[来源标注] [DD-001:MD-007] [DD-001:FS-007] [DD-001:IC-016/017/018/019/020/021]
"""

# 1. 标准库
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

# 2. 第三方
import yaml

# 3. 本地
from research_tool.datatypes import (
    Chapter,
    LLMSummary,
    ScreenshotFrame,
    Transcript,
    VideoMeta,
)
from research_tool.error_handler import register_error
from research_tool.structured_logger import emit_log


# ============================================================
# 常量定义
# ============================================================

#: 章节降级默认等距切片间隔（5 分钟）
#: [来源标注] [DD-001:IC-016 interval_min 默认 5] [DD-001:MD-007 §ChapterDegrader]
DEFAULT_CHAPTER_INTERVAL_MIN: int = 5

#: front_matter 字段名强制前缀
#: [来源标注] [DD-001:IC-014] [DD-001:SR-002]
FRONT_MATTER_KEY_PREFIX: str = "video_"

#: 截图缺失时的占位图路径
#: [来源标注] [DD-M推断:依据 = DD-001:MD-007 §异常处理 截图路径不存在 → 占位图]
PLACEHOLDER_SCREENSHOT_PATH: str = "assets/placeholder.png"

#: 参考来源章节标题
#: [来源标注] [DD-001:IC-019 §后置条件 references_md 含 ## 参考来源 标题]
REFERENCES_SECTION_TITLE: str = "## 参考来源"

#: 任务 ID 日志字段
#: [来源标注] [DD-001:IC-028]
TASK_ID_LOG_KEY: str = "task_id"

#: 错误码：章节降级
#: [来源标注] [DD-001:MD-007 §异常处理] [DD-001:IC-016]
E_LLM_002_CHAPTERS_FALLBACK: str = "E_LLM_002_CHAPTERS_FALLBACK"

#: 错误码：YAML 解析失败
#: [来源标注] [DD-M推断:依据 = DD-001:MD-007 §异常处理 YAML 解析失败 → 字段 fallback]
E_NS_001_YAML_PARSE_FAIL: str = "E_NS_001_YAML_PARSE_FAIL"

#: 错误码：截图路径不存在
#: [来源标注] [DD-M推断:依据 = DD-001:MD-007 §异常处理 截图路径不存在 → 占位图]
E_NS_002_SCREENSHOT_MISSING: str = "E_NS_002_SCREENSHOT_MISSING"


# ============================================================
# 类定义
# ============================================================


class FrontMatterParser:
    """[类名] FrontMatterParser

    [职责] YAML front_matter 解析与字段名校验
    [关联设计规范] MD-007 §front_matter_parser（DD-001）

    [属性]
      属性1: yaml_loader - yaml.SafeLoader 类型 - YAML 加载器实例
      属性2: whitelist_prefix - str 类型 - 字段名白名单前缀

    [方法列表]
      方法1: parse(yaml_str) -> dict - 解析 YAML 字符串
      方法2: dump(front_matter) -> str - 序列化 front_matter
      方法3: validate_keys(front_matter) -> list[str] - 校验键名前缀

    [异常处理]
      异常1: yaml.YAMLError - YAML 解析失败时触发，记录 E_NS_001_YAML_PARSE_FAIL 并降级

    [来源标注] [DD-001:MD-007 §FrontMatterParser]
    """

    def __init__(self) -> None:
        """[函数名] __init__

        [职责] 初始化 YAML 加载器与白名单前缀
        [参数说明] 无
        [返回值] None
        [并发安全] 是（无状态初始化）
        [来源标注] [DD-M推断:依据 = DD-001:CS-001 §类型注解规范]
        """
        ...

    def parse(self, yaml_str: str) -> Dict[str, object]:
        """[函数名] parse

        [职责] 解析 YAML 字符串为 dict
        [关联接口契约] IC-016（前置步骤）

        [参数说明]
          参数1: yaml_str - str - 必填 - YAML 字符串

        [返回值]
          类型: Dict[str, object]
          描述: 解析后的字典
          特殊值: 解析失败时返回空 dict（降级路径）

        [错误码]
          错误码1: E_NS_001_YAML_PARSE_FAIL - YAML 解析失败 - 触发条件 yaml.YAMLError

        [前置条件] yaml_str 为合法字符串
        [后置条件] 返回值 dict 可为空
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 10ms（典型 100 行 YAML）
        [来源标注] [DD-001:MD-007 §front_matter_parser] [DD-001:CS-001 §异常处理]
        """
        ...

    def dump(self, front_matter: Dict[str, object]) -> str:
        """[函数名] dump

        [职责] 序列化 front_matter 为 YAML 字符串
        [参数说明]
          参数1: front_matter - Dict[str, object] - 必填 - front_matter 字典
        [返回值]
          类型: str
          描述: YAML 字符串
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 10ms
        [来源标注] [DD-001:MD-007 §front_matter_parser]
        """
        ...

    def validate_keys(self, front_matter: Dict[str, object]) -> List[str]:
        """[函数名] validate_keys

        [职责] 校验 front_matter 键名是否以 video_ 开头
        [关联接口契约] IC-014（字段名前缀校验）

        [参数说明]
          参数1: front_matter - Dict[str, object] - 必填 - 待校验字典

        [返回值]
          类型: List[str]
          描述: 非法键名列表
          特殊值: 全合法时返回空列表

        [前置条件] front_matter 非空
        [后置条件] 返回的非法键不包含以 video_ 开头的项
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 10ms
        [来源标注] [DD-001:IC-014] [DD-001:SR-002]
        """
        ...


class VideoMetaInjector:
    """[类名] VideoMetaInjector

    [职责] 将 VideoMeta 注入 front_matter
    [关联设计规范] MD-007 §video_meta_injector（DD-001）

    [属性]
      属性1: schema - Dict[str, str] - 字段映射（VideoMeta → front_matter 字段名）

    [方法列表]
      方法1: inject(meta) -> dict - 主注入方法
      方法2: fill_defaults(meta) -> VideoMeta - 默认值填充

    [异常处理]
      异常1: 字段为 None 时保留 null 占位（不抛错）

    [来源标注] [DD-001:MD-007 §VideoMetaInjector] [DD-001:IC-017]
    """

    def __init__(self) -> None:
        """[函数名] __init__

        [职责] 初始化字段映射 schema
        [参数说明] 无
        [返回值] None
        [来源标注] [DD-M推断:依据 = DD-001:IC-017 §后置条件 必含 video_title / video_author / video_duration / video_platform]
        """
        ...

    def inject(self, meta: VideoMeta) -> Dict[str, object]:
        """[函数名] inject

        [职责] 将 VideoMeta 注入为 front_matter dict
        [关联接口契约] IC-017

        [参数说明]
          参数1: meta - VideoMeta - 必填 - 视频元数据

        [返回值]
          类型: Dict[str, object]
          描述: 含 video_* 字段的 front_matter

        [前置条件] meta 非空
        [后置条件] 必含 video_title / video_author / video_duration / video_platform
        [并发安全] 是
        [幂等性] 是（与 meta 一一对应）
        [性能约束] < 10ms
        [来源标注] [DD-001:IC-017] [DD-001:MD-007 §VideoMetaInjector]
        """
        ...

    def fill_defaults(self, meta: VideoMeta) -> VideoMeta:
        """[函数名] fill_defaults

        [职责] 为 VideoMeta 缺失字段填充默认值
        [参数说明]
          参数1: meta - VideoMeta - 必填 - 视频元数据
        [返回值]
          类型: VideoMeta
          描述: 填充后的 VideoMeta
        [并发安全] 是
        [幂等性] 是
        [来源标注] [DD-M推断:依据 = DD-001:MD-007 §VideoMetaInjector fill_defaults 方法]
        """
        ...


class ChapterDegrader:
    """[类名] ChapterDegrader

    [职责] LLM 未返回 video_chapters 时按等距切片降级
    [关联设计规范] MD-007 §chapter_degrader（DD-001）

    [属性]
      属性1: interval_min - int - 等距切片间隔（默认 5 分钟）

    [方法列表]
      方法1: degrade(transcript, interval_min) -> list[Chapter] - 主降级方法
      方法2: build_chapter_list(transcript, interval_min) -> list[Chapter] - 构建章节列表

    [状态机]
      输入有 chapters → 返回原 chapters
      输入无 chapters → 按 interval_min 等距切片生成

    [异常处理]
      异常1: transcript.segments 为空 → 返回单个空 chapter
      异常2: E_LLM_002_CHAPTERS_FALLBACK 触发 WARN 日志

    [来源标注] [DD-001:MD-007 §ChapterDegrader] [DD-001:IC-016] [DD-001:ADR-008 EP-002]
    """

    def __init__(self, interval_min: int = DEFAULT_CHAPTER_INTERVAL_MIN) -> None:
        """[函数名] __init__

        [职责] 初始化降级间隔
        [参数说明]
          参数1: interval_min - int - 可选 - 默认 5 - 等距切片间隔
        [返回值] None
        [来源标注] [DD-001:IC-016 §入参 interval_min 默认 5]
        """
        ...

    def degrade(self, transcript: Transcript, interval_min: int) -> List[Chapter]:
        """[函数名] degrade

        [职责] 降级生成章节列表
        [关联接口契约] IC-016

        [参数说明]
          参数1: transcript - Transcript - 必填 - 转写稿
          参数2: interval_min - int - 必填 - 等距切片间隔（分钟）

        [返回值]
          类型: List[Chapter]
          描述: 章节列表
          特殊值: 至少 1 个 chapter

        [错误码]
          错误码1: E_LLM_002_CHAPTERS_FALLBACK - 章节降级 - 触发条件 LLM 未返回 chapters

        [前置条件] transcript.segments 非空
        [后置条件] chapters 至少 1 个
        [并发安全] 是
        [幂等性] 是（与 transcript + interval_min 一一对应）
        [性能约束] < 100ms
        [来源标注] [DD-001:IC-016] [DD-001:MD-007 §chapter_degrader]
        """
        ...

    def build_chapter_list(
        self, transcript: Transcript, interval_min: int
    ) -> List[Chapter]:
        """[函数名] build_chapter_list

        [职责] 按等距切片构建章节列表
        [参数说明]
          参数1: transcript - Transcript - 必填 - 转写稿
          参数2: interval_min - int - 必填 - 切片间隔
        [返回值]
          类型: List[Chapter]
          描述: 章节列表
        [并发安全] 是
        [幂等性] 是
        [来源标注] [DD-001:MD-007 §ChapterDegrader build_chapter_list 方法]
        """
        ...


class ScreenshotEmbedder:
    """[类名] ScreenshotEmbedder

    [职责] 在 Markdown 中嵌入截图引用 ![](path)
    [关联设计规范] MD-007 §screenshot_embedder（DD-001）

    [属性]
      属性1: base_path - Path - 截图基础路径
      属性2: placeholder - str - 占位图路径

    [方法列表]
      方法1: embed(md, paths) -> str - 主嵌入方法
      方法2: handle_missing(path) -> str - 缺失路径占位图处理

    [异常处理]
      异常1: 路径不存在 → 占位图（不抛错，记录 WARN）

    [来源标注] [DD-001:MD-007 §ScreenshotEmbedder] [DD-001:IC-018] [DD-001:SR-003]
    """

    def __init__(self, base_path: Path, placeholder: str = PLACEHOLDER_SCREENSHOT_PATH) -> None:
        """[函数名] __init__

        [职责] 初始化截图嵌入器
        [参数说明]
          参数1: base_path - Path - 必填 - 截图基础路径
          参数2: placeholder - str - 可选 - 默认 PLACEHOLDER_SCREENSHOT_PATH - 占位图路径
        [返回值] None
        [来源标注] [DD-001:MD-007 §screenshot_embedder]
        """
        ...

    def embed(self, md: str, paths: List[str]) -> str:
        """[函数名] embed

        [职责] 在 Markdown 末尾追加截图引用
        [关联接口契约] IC-018

        [参数说明]
          参数1: md - str - 必填 - Markdown 文本
          参数2: paths - List[str] - 必填 - 截图路径列表

        [返回值]
          类型: str
          描述: 嵌入后的 Markdown

        [错误码]
          错误码1: E_NS_002_SCREENSHOT_MISSING - 截图路径不存在 - 触发条件 path not exists

        [前置条件] paths 非空
        [后置条件] 返回值含 5 个 ![](path) 引用（典型 5 张截图）
        [并发安全] 是
        [幂等性] 是（与 md + paths 一一对应）
        [性能约束] < 50ms
        [来源标注] [DD-001:IC-018] [DD-001:MD-007 §screenshot_embedder] [DD-001:SR-003]
        """
        ...

    def handle_missing(self, path: str) -> str:
        """[函数名] handle_missing

        [职责] 处理缺失的截图路径（返回占位图路径）
        [参数说明]
          参数1: path - str - 必填 - 截图路径
        [返回值]
          类型: str
          描述: 占位图路径或原路径
        [并发安全] 是
        [幂等性] 是
        [来源标注] [DD-001:MD-007 §screenshot_embedder handle_missing 方法]
        """
        ...


class ReferenceGenerator:
    """[类名] ReferenceGenerator

    [职责] 生成 ## 参考来源 章节
    [关联设计规范] MD-007 §reference_generator（DD-001）

    [属性]
      属性1: template - str - Markdown 模板

    [方法列表]
      方法1: generate(meta) -> str - 主生成方法
      方法2: format(meta) -> str - 格式化方法

    [来源标注] [DD-001:MD-007 §ReferenceGenerator] [DD-001:IC-019]
    """

    def __init__(self) -> None:
        """[函数名] __init__

        [职责] 初始化 Markdown 模板
        [参数说明] 无
        [返回值] None
        [来源标注] [DD-M推断:依据 = DD-001:IC-019 §后置条件 references_md 含 ## 参考来源 标题]
        """
        ...

    def generate(self, meta: VideoMeta) -> str:
        """[函数名] generate

        [职责] 生成参考来源 Markdown 章节
        [关联接口契约] IC-019

        [参数说明]
          参数1: meta - VideoMeta - 必填 - 视频元数据

        [返回值]
          类型: str
          描述: 参考来源 Markdown

        [前置条件] meta 非空
        [后置条件] 返回值含 ## 参考来源 标题
        [并发安全] 是
        [幂等性] 是（与 meta 一一对应）
        [性能约束] < 10ms
        [来源标注] [DD-001:IC-019] [DD-001:MD-007 §reference_generator]
        """
        ...

    def format(self, meta: VideoMeta) -> str:
        """[函数名] format

        [职责] 格式化参考来源内容
        [参数说明]
          参数1: meta - VideoMeta - 必填 - 视频元数据
        [返回值]
          类型: str
          描述: 格式化后的 Markdown 行
        [并发安全] 是
        [幂等性] 是
        [来源标注] [DD-001:MD-007 §ReferenceGenerator format 方法]
        """
        ...


class MarkdownAssembler:
    """[类名] MarkdownAssembler

    [职责] 将 front_matter + body + screenshots + references 拼装为完整 Markdown
    [关联设计规范] MD-007 §markdown_assembler（DD-001）

    [属性]
      属性1: template - str - Markdown 总装模板

    [方法列表]
      方法1: assemble(meta, summary, transcript, screenshots) -> str - 主拼装方法
      方法2: render(front_matter, body, references) -> str - 渲染方法

    [状态机]
      INIT → [render front_matter] → FRONT_MATTER_RENDERED
      → [render body] → BODY_RENDERED
      → [embed screenshots] → SCREENSHOTS_EMBEDDED
      → [append references] → REFERENCES_ADDED
      → DONE

    [来源标注] [DD-001:MD-007 §MarkdownAssembler] [DD-001:IC-020] [调研:S-005]
    """

    def __init__(self) -> None:
        """[函数名] __init__

        [职责] 初始化 Markdown 总装模板
        [参数说明] 无
        [返回值] None
        [来源标注] [DD-001:MD-007 §markdown_assembler]
        """
        ...

    def assemble(
        self,
        meta: VideoMeta,
        summary: LLMSummary,
        transcript: Transcript,
        screenshots: List[ScreenshotFrame],
    ) -> str:
        """[函数名] assemble

        [职责] 拼装完整 Markdown
        [关联接口契约] IC-020

        [参数说明]
          参数1: meta - VideoMeta - 必填 - 视频元数据
          参数2: summary - LLMSummary - 必填 - LLM 总结
          参数3: transcript - Transcript - 必填 - 转写稿
          参数4: screenshots - List[ScreenshotFrame] - 必填 - 截图列表

        [返回值]
          类型: str
          描述: 完整 Markdown

        [前置条件] 4 个入参均非空
        [后置条件] 返回值含 YAML front_matter + Markdown body
        [并发安全] 是
        [幂等性] 是（与 4 个入参一一对应）
        [性能约束] < 200ms
        [来源标注] [DD-001:IC-020] [DD-001:MD-007 §markdown_assembler] [调研:S-005]
        """
        ...

    def render(
        self, front_matter: Dict[str, object], body: str, references: str
    ) -> str:
        """[函数名] render

        [职责] 渲染 front_matter + body + references 为 Markdown
        [参数说明]
          参数1: front_matter - Dict[str, object] - 必填 - front_matter
          参数2: body - str - 必填 - Markdown body
          参数3: references - str - 必填 - 参考来源 Markdown
        [返回值]
          类型: str
          描述: 渲染后的 Markdown
        [并发安全] 是
        [幂等性] 是
        [来源标注] [DD-001:MD-007 §MarkdownAssembler render 方法]
        """
        ...


class NotesSchemaOrchestrator:
    """[类名] NotesSchemaOrchestrator

    [职责] 顶层编排器，按状态机调度 6 步拼装
    [关联设计规范] MD-007 §M-007 笔记组装器 状态机（DD-001）

    [属性]
      属性1: parser - FrontMatterParser - YAML 解析器
      属性2: injector - VideoMetaInjector - VideoMeta 注入器
      属性3: degrader - ChapterDegrader - 章节降级器
      属性4: embedder - ScreenshotEmbedder - 截图嵌入器
      属性5: ref_gen - ReferenceGenerator - 参考来源生成器
      属性6: assembler - MarkdownAssembler - Markdown 总装器

    [方法列表]
      方法1: assemble_markdown(meta, summary, screenshots, transcript) -> str - 顶层入口
      方法2: _build_chapter_fallback(transcript) -> List[Chapter] - 钩子方法（EP-002 扩展点）

    [状态机]
      INIT → [parse_front_matter] → PARSED
      → [inject_video_meta] → META_INJECTED
      → [chapter_degrader (if fail)] → CHAPTERS_FALLBACK
      → [embed_screenshots] → SCREENSHOTS_EMBEDDED
      → [generate_references] → REFERENCES_ADDED
      → [assemble_markdown] → MARKDOWN_READY

    [异常处理]
      异常1: 局部失败 → 部分降级 → 主链继续
      异常2: E_LLM_002_CHAPTERS_FALLBACK 触发章节降级

    [来源标注] [DD-001:MD-007 §M-007 笔记组装器] [DD-001:MD-007 §状态机] [DD-001:ADR-008 EP-002]
    """

    def __init__(
        self,
        parser: Optional[FrontMatterParser] = None,
        injector: Optional[VideoMetaInjector] = None,
        degrader: Optional[ChapterDegrader] = None,
        embedder: Optional[ScreenshotEmbedder] = None,
        ref_gen: Optional[ReferenceGenerator] = None,
        assembler: Optional[MarkdownAssembler] = None,
    ) -> None:
        """[函数名] __init__

        [职责] 初始化 6 个子模块实例（支持依赖注入便于测试）
        [参数说明]
          参数1: parser - FrontMatterParser - 可选 - 默认新建
          参数2: injector - VideoMetaInjector - 可选 - 默认新建
          参数3: degrader - ChapterDegrader - 可选 - 默认新建
          参数4: embedder - ScreenshotEmbedder - 可选 - 默认新建
          参数5: ref_gen - ReferenceGenerator - 可选 - 默认新建
          参数6: assembler - MarkdownAssembler - 可选 - 默认新建
        [返回值] None
        [并发安全] 是
        [来源标注] [DD-M推断:依据 = DD-001:CS-001 §类型注解 + 测试可 Mock 依赖注入]
        """
        ...

    def assemble_markdown(
        self,
        meta: VideoMeta,
        summary: LLMSummary,
        screenshots: List[ScreenshotFrame],
        transcript: Transcript,
    ) -> str:
        """[函数名] assemble_markdown

        [职责] 顶层入口：6 步拼装完整 Markdown
        [关联接口契约] IC-020

        [参数说明]
          参数1: meta - VideoMeta - 必填 - 视频元数据
          参数2: summary - LLMSummary - 必填 - LLM 总结
          参数3: screenshots - List[ScreenshotFrame] - 必填 - 截图列表
          参数4: transcript - Transcript - 必填 - 转写稿

        [返回值]
          类型: str
          描述: 完整 Markdown

        [状态机]
          INIT → PARSED → META_INJECTED → CHAPTERS_FALLBACK → SCREENSHOTS_EMBEDDED → REFERENCES_ADDED → MARKDOWN_READY

        [错误码]
          错误码1: E_NS_001_YAML_PARSE_FAIL - YAML 解析失败（降级继续）
          错误码2: E_LLM_002_CHAPTERS_FALLBACK - 章节降级
          错误码3: E_NS_002_SCREENSHOT_MISSING - 截图缺失（占位图）

        [前置条件] 4 个入参均非空
        [后置条件] 返回值含 YAML front_matter + Markdown body + 截图 + 参考来源
        [并发安全] 是
        [幂等性] 是（与 4 个入参一一对应）
        [性能约束] < 200ms
        [来源标注] [DD-001:IC-020] [DD-001:MD-007 §M-007 笔记组装器 §状态机]
        """
        ...

    def _build_chapter_fallback(self, transcript: Transcript) -> List[Chapter]:
        """[函数名] _build_chapter_fallback

        [职责] 章节降级钩子（EP-002 扩展点）
        [关联设计规范] ADR-008 EP-002（V1.2 扩展点）

        [参数说明]
          参数1: transcript - Transcript - 必填 - 转写稿

        [返回值]
          类型: List[Chapter]
          描述: 降级章节列表

        [前置条件] transcript.segments 非空
        [后置条件] chapters 至少 1 个
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 50ms
        [来源标注] [DD-001:IC-021] [DD-001:ADR-008] [CE-008] [DD-M推断:依据 = soul §4.5 钩子方法命名约定]
        """
        ...


# ============================================================
# 模块级函数（接口签名）
# ============================================================


def parse_front_matter(yaml_str: str) -> Dict[str, object]:
    """[函数名] parse_front_matter

    [职责] 模块级入口：解析 YAML front_matter
    [关联接口契约] IC-016（前置步骤）

    [参数说明]
      参数1: yaml_str - str - 必填 - YAML 字符串

    [返回值]
      类型: Dict[str, object]
      描述: 解析后的字典

    [错误码]
      错误码1: E_NS_001_YAML_PARSE_FAIL - YAML 解析失败

    [前置条件] yaml_str 为合法字符串
    [后置条件] 解析失败时返回空 dict
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 10ms
    [来源标注] [DD-001:MD-007 §parse_front_matter] [DD-001:IC-016]
    """
    ...


def inject_video_meta(meta: VideoMeta) -> Dict[str, object]:
    """[函数名] inject_video_meta

    [职责] 模块级入口：注入 VideoMeta 到 front_matter
    [关联接口契约] IC-017

    [参数说明]
      参数1: meta - VideoMeta - 必填 - 视频元数据

    [返回值]
      类型: Dict[str, object]
      描述: 含 video_* 字段的 front_matter

    [前置条件] meta 非空
    [后置条件] 必含 video_title / video_author / video_duration / video_platform
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 10ms
    [来源标注] [DD-001:IC-017] [DD-001:MD-007 §inject_video_meta]
    """
    ...


def degrade_chapters(transcript: Transcript, interval_min: int) -> List[Chapter]:
    """[函数名] degrade_chapters

    [职责] 模块级入口：章节降级（5min 等距切片）
    [关联接口契约] IC-016 + IC-021

    [参数说明]
      参数1: transcript - Transcript - 必填 - 转写稿
      参数2: interval_min - int - 必填 - 等距切片间隔（分钟）

    [返回值]
      类型: List[Chapter]
      描述: 降级章节列表

    [错误码]
      错误码1: E_LLM_002_CHAPTERS_FALLBACK - 章节降级

    [前置条件] transcript.segments 非空
    [后置条件] chapters 至少 1 个
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 100ms
    [来源标注] [DD-001:IC-016] [DD-001:IC-021] [DD-001:MD-007 §degrade_chapters]
    """
    ...


def embed_screenshots(md: str, paths: List[str]) -> str:
    """[函数名] embed_screenshots

    [职责] 模块级入口：嵌入截图引用
    [关联接口契约] IC-018

    [参数说明]
      参数1: md - str - 必填 - Markdown 文本
      参数2: paths - List[str] - 必填 - 截图路径列表

    [返回值]
      类型: str
      描述: 嵌入后的 Markdown

    [错误码]
      错误码1: E_NS_002_SCREENSHOT_MISSING - 截图路径不存在

    [前置条件] paths 非空
    [后置条件] 返回值含 ![](path) 引用
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 50ms
    [来源标注] [DD-001:IC-018] [DD-001:MD-007 §embed_screenshots] [DD-001:SR-003]
    """
    ...


def generate_references(meta: VideoMeta) -> str:
    """[函数名] generate_references

    [职责] 模块级入口：生成参考来源章节
    [关联接口契约] IC-019

    [参数说明]
      参数1: meta - VideoMeta - 必填 - 视频元数据

    [返回值]
      类型: str
      描述: Markdown 参考来源章节

    [前置条件] meta 非空
    [后置条件] 返回值含 ## 参考来源 标题
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 10ms
    [来源标注] [DD-001:IC-019] [DD-001:MD-007 §generate_references]
    """
    ...


def assemble_markdown(
    meta: VideoMeta,
    summary: LLMSummary,
    screenshots: List[ScreenshotFrame],
    transcript: Transcript,
) -> str:
    """[函数名] assemble_markdown

    [职责] 模块级顶层入口：6 步拼装完整 Markdown
    [关联接口契约] IC-020

    [参数说明]
      参数1: meta - VideoMeta - 必填 - 视频元数据
      参数2: summary - LLMSummary - 必填 - LLM 总结
      参数3: screenshots - List[ScreenshotFrame] - 必填 - 截图列表
      参数4: transcript - Transcript - 必填 - 转写稿

    [返回值]
      类型: str
      描述: 完整 Markdown

    [前置条件] 4 个入参均非空
    [后置条件] 返回值含 YAML front_matter + Markdown body + 截图 + 参考来源
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 200ms
    [来源标注] [DD-001:IC-020] [DD-001:MD-007 §assemble_markdown]
    """
    ...


# ============================================================
# 模块导出
# ============================================================

__all__ = [
    "FrontMatterParser",
    "VideoMetaInjector",
    "ChapterDegrader",
    "ScreenshotEmbedder",
    "ReferenceGenerator",
    "MarkdownAssembler",
    "NotesSchemaOrchestrator",
    "parse_front_matter",
    "inject_video_meta",
    "degrade_chapters",
    "embed_screenshots",
    "generate_references",
    "assemble_markdown",
    "DEFAULT_CHAPTER_INTERVAL_MIN",
    "FRONT_MATTER_KEY_PREFIX",
    "PLACEHOLDER_SCREENSHOT_PATH",
    "REFERENCES_SECTION_TITLE",
    "E_LLM_002_CHAPTERS_FALLBACK",
    "E_NS_001_YAML_PARSE_FAIL",
    "E_NS_002_SCREENSHOT_MISSING",
]
