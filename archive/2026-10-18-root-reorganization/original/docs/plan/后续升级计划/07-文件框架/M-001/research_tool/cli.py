# -*- coding: utf-8 -*-
"""
[文件路径] research_tool/cli.py
[文件职责]  CLI 入口、参数解析、平台识别、配置加载、并发调度、RAG 入口
[所属模块]  M-001（cli_bindings，CLI 绑定与编排器）
[关联设计规范]  FS-001（DD-001）/ MD-001（DD-001）/ IC-001~IC-005（DD-001）
[关联技术选型]  TS-001（Python ≥ 3.11）/ TS-014（asyncio）/ TS-015（subprocess）
[关联数据结构]  DE-001（CLIArgs）/ DE-002（VideoURL）/ DE-006（LLMSummary）/ DE-009（Result）
[关联错误码]    E_DL_001（非法 URL）/ E_DL_001_DENO_MISSING（Deno 缺失）/ E_DL_LOCAL_001（本地路径不存在）/ E_LIM_001（URL>10）

[功能描述]
  功能1: argparse 解析 8 个白名单参数（--urls/--video-file/--topic/--style/--cookie-file/--query/--chapter-interval-min/--help）
  功能2: URL → Platform 枚举识别（BILIBILI / YOUTUBE / LOCAL / UNKNOWN）
  功能3: 环境变量 + LLMConfig 强制覆盖加载（API Key 强制 env）
  功能4: URL 列表分发到 M-012 并发编排器（Semaphore(3)）
  功能5: 单次 RAG 问答入口（封装 M-006 summarize_rag）
  功能6: 异常 → M-010 登记 → 退出码仲裁 → 顶层打印 + sys.exit

[输入输出]
  输入:  sys.argv（用户命令行参数，列表）
  输出:  sys.exit code（0 成功 / 1 通用 / 2 校验失败 / 3 依赖缺失 / 4 部分失败）
  旁路:  Markdown 落盘至 raw/<topic>/<video-id>.md（由 M-007/M-008 完成）

[依赖关系]
  依赖文件:
    - research_tool/datatypes.py（DE-001/DE-002/DE-006/DE-009）
    - research_tool/preflight.py（M-002，check_all）
    - research_tool/concurrent_orchestrator.py（M-012，gather_tasks）
    - research_tool/error_handler.py（M-010，register_error / resolve_exit_code）
    - research_tool/structured_logger.py（M-011，emit_log）
    - research_tool/llm_client.py（M-006，summarize_rag，仅 RAG 路径）
  被依赖文件:
    - research/__init__.py（research 入口脚本 from research_tool.cli import main）
    - 无业务模块反向依赖 M-001（CLI 是顶层协调器）

[注意事项]
  注意1: CLIArgParser 仅接受 8 个白名单参数，未知参数触发 argparse SystemExit（退出码 2）
  注意2: URL 数量上限 MAX_URL_COUNT=10，超出时拒绝执行（错误码 E_LIM_001）
  注意3: API Key 强制从环境变量加载，绝不落盘/入日志（由 M-011 SensitiveFilter 强制）
  注意4: argv 日志仅记录 sha256（argv_sha256），不记录明文（IC-001 日志策略）
  注意5: 设计模式 = 协调器模式（CLI 调度 M-002/010/011/012）+ 门面模式（CLIArgs / Result 对外）
  注意6: 无状态 CLI 入口：每次启动独立进程，无内部状态机（MD-001 明确 N/A）
  注意7: --query 与 --urls 互斥：仅 RAG 模式时使用 --query，处理模式使用 --urls
  注意8: --video-file 解析后注入 platform=LOCAL 变体 VideoURL（IC-005）
  注意9: 异常流程：异常 → M-010 register_error → resolve_exit_code → 顶层打印 → sys.exit
  注意10: argv sha256 用于审计追踪（不记录明文参数；PII 保护）

[代码风格]  遵循 CS-001（Python 3.11+，4 空格缩进，snake_case，Google docstring，强制类型注解）
[创建日期]  2026-06-01
[修改历史]
  2026-06-01: DD-M-001-20260601 - 初版文件框架（仅注释，骨架占位 pass）
[作者]  DD-M-001-20260601
[来源标注]  [DD-001:FS-001] [DD-001:MD-001] [DD-001:IC-001/IC-002/IC-003/IC-004/IC-005] [DD-001:DE-001/DE-002/DE-009]
"""


# =============================================================================
# 标准库导入
# =============================================================================
from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Optional

# =============================================================================
# 第三方导入
# =============================================================================
# [DD-M推断] M-001 仅依赖标准库；不引入第三方依赖以保持 CLI 入口极简
# [来源标注]  [DD-001:CS-001 导入规范]


# =============================================================================
# 本地导入（来自同包其他模块，禁止循环依赖）
# =============================================================================
# 注：以下导入在下游开发工程师填充 import 路径时按 FS-001 依赖图校对：
#   - DE-001 CLIArgs / DE-002 VideoURL / DE-006 LLMSummary / DE-009 Result
#                                                       →  research_tool.datatypes
#   - M-002 check_all / PreflightReport                 →  research_tool.preflight
#   - M-010 register_error / resolve_exit_code         →  research_tool.error_handler
#   - M-011 emit_log                                    →  research_tool.structured_logger
#   - M-012 gather_tasks                                →  research_tool.concurrent_orchestrator
#   - M-006 summarize_rag（仅 RAG 模式）               →  research_tool.llm_client
#
# [DD-M推断] 严格遵循 FS-001 依赖方向：cli.py → (M-002/M-010/M-011/M-012/M-006)/datatypes，
#            禁止 datatypes 反向引用 cli；禁止 M-002~M-012 反向 import cli（M-001 是顶层入口）。
# [来源标注]  [DD-001:FS-001 依赖关系图] [DD-001:CS-001 导入规范]


# =============================================================================
# 模块级常量（UPPER_SNAKE_CASE，符合 CS-001）
# =============================================================================
#: CLI 接受的 8 个白名单参数
ALLOWED_ARGS: tuple[str, ...] = (
    "--urls",
    "--video-file",
    "--topic",
    "--style",
    "--cookie-file",
    "--query",
    "--chapter-interval-min",
    "--help",
)
#: URL 数量上限（IC-001 前置条件：≤ 10）
MAX_URL_COUNT: int = 10
#: 默认主题分类（IC-001 出参 topic 默认 "general"）
DEFAULT_TOPIC: str = "general"
#: 默认章节等距切片间隔（分钟，对齐 IC-016/IC-021）
DEFAULT_CHAPTER_INTERVAL_MIN: int = 5
#: 默认并发数（与 M-012 一致，可由 M-012 资源探测降级到 2）
DEFAULT_CONCURRENCY: int = 3
#: Platform 枚举字面量
PLATFORM_BILIBILI: str = "bilibili"
PLATFORM_YOUTUBE: str = "youtube"
PLATFORM_LOCAL: str = "local"
PLATFORM_UNKNOWN: str = "unknown"
#: 平台识别正则模式键
_RE_YOUTUBE: str = r"(?:youtube\.com|youtu\.be)"
_RE_BILIBILI: str = r"bilibili\.com"
#: 退出码映射
EXIT_OK: int = 0
EXIT_GENERAL: int = 1
EXIT_VALIDATION: int = 2
EXIT_DEP_MISSING: int = 3
EXIT_PARTIAL_FAIL: int = 4
#: argv sha256 截断长度（日志用 16 字符）
ARGV_SHA256_PREFIX: int = 16
#: 环境变量名（API Key / 模型名）
ENV_DEEPSEEK_API_KEY: str = "DEEPSEEK_API_KEY"
ENV_QWEN_API_KEY: str = "QWEN_API_KEY"
ENV_LLM_MODEL: str = "LLM_MODEL"
ENV_LLM_BASE_URL: str = "LLM_BASE_URL"


# =============================================================================
# 子模块 1：arg_parser（IC-001 CLI 入参解析）
# =============================================================================
class CLIArgParser:
    """
    [类名] CLIArgParser
    [职责]  argparse 白名单 8 个参数解析与校验
    [关联设计规范] MD-001（DD-001）/ IC-001（DD-001）

    [属性]
      属性1: allowed_args tuple[str, ...] 8 个白名单参数名
      属性2: parser argparse.ArgumentParser argparse 解析器实例

    [方法列表]
      方法1: parse(argv) -> CLIArgs 解析 argv 并返回结构化 CLIArgs
      方法2: validate_url(url) -> bool 校验单个 URL 是否合法
      方法3: validate_path(path) -> bool 校验本地文件路径是否存在

    [状态机] N/A（一次性 CLI 入口，无内部状态）

    [异常处理]
      异常1: argparse.SystemExit - 未知参数或 --help 时由 argparse 触发
      异常2: E_DL_001 - validate_url 拒绝非法 URL
      异常3: E_DL_LOCAL_001 - validate_path 拒绝不存在的文件

    [来源标注] [DD-001:MD-001] [DD-001:IC-001]
    """

    def __init__(self) -> None:
        """
        [函数名] __init__
        [职责] 初始化 CLIArgParser，构建 argparse 子系统
        [参数说明]
          参数1: self CLIArgParser 必填 无默认 实例自身
        [返回值] None
        [来源标注] [DD-001:IC-001] [DD-M推断:MD-001 类设计]
        """
        # TODO(DD-M-001-20260601): 初始化 self.allowed_args = ALLOWED_ARGS
        # TODO(DD-M-001-20260601): 构建 self.parser = argparse.ArgumentParser(prog="research", ...)
        # TODO(DD-M-001-20260601): 注册 8 个白名单参数
        pass

    def parse(self, argv: list[str]) -> "CLIArgs":
        """
        [函数名] parse
        [职责] 解析 argv，校验后返回 CLIArgs
        [关联接口契约] IC-001（DD-001）
        [参数说明]
          参数1: argv list[str] 必填 无默认 sys.argv 列表，长度 1-100
        [返回值]
          类型: CLIArgs
          描述: 解析后的结构化参数对象（DE-001）
          特殊值: 无
        [错误码]
          错误码1: E_DL_001 - 非法 URL（argparse 拒绝 + 打印帮助）
          错误码2: E_DL_LOCAL_001 - 本地文件不存在
          错误码3: E_LIM_001 - URL 数量超过 10
        [前置条件] argv 非空；URL 数量 ≤ 10
        [后置条件] 解析后的 CLIArgs.parsed_urls 全部为有效 VideoURL
        [并发安全] 否（一次性 CLI 入口）
        [幂等性] 是（幂等键 = URL 列表）
        [性能约束] < 100ms
        [示例]
          ```
          parser = CLIArgParser()
          cli_args = parser.parse(["--urls", "https://www.bilibili.com/video/BV1xx", "--topic", "ai"])
          ```
        [来源标注] [DD-001:IC-001] [DD-001:DE-001]
        """
        # TODO(DD-M-001-20260601): 校验 argv 非空
        # TODO(DD-M-001-20260601): 校验白名单（拒绝未在 ALLOWED_ARGS 的参数）
        # TODO(DD-M-001-20260601): 调用 self.parser.parse_args(argv)
        # TODO(DD-M-001-20260601): 校验 URL 数量 ≤ MAX_URL_COUNT
        # TODO(DD-M-001-20260601): 调用 validate_url 对每个 URL 校验
        # TODO(DD-M-001-20260601): 若 --video-file 存在则调用 validate_path
        # TODO(DD-M-001-20260601): 构造并返回 CLIArgs 对象
        raise NotImplementedError("DD-S 阶段由结构设计师实现 parse 骨架")

    def validate_url(self, url: str) -> bool:
        """
        [函数名] validate_url
        [职责] 校验单个 URL 字符串是否合法（http/https + 含域名）
        [关联接口契约] IC-002（DD-001 隐含前置校验）
        [参数说明]
          参数1: url str 必填 无默认 URL 字符串，长度 1-2048
        [返回值]
          类型: bool
          描述: True=合法，False=非法
          特殊值: 空字符串返回 False
        [错误码] E_DL_001 - 非法 URL
        [前置条件] url 为字符串
        [后置条件] 返回布尔判定
        [并发安全] 是（无副作用）
        [幂等性] 是
        [性能约束] < 1ms
        [来源标注] [DD-001:IC-002] [DD-M推断:MD-001 子模块]
        """
        # TODO(DD-M-001-20260601): 正则匹配 ^(https?)://... 或本地路径
        # TODO(DD-M-001-20260601): 返回布尔
        raise NotImplementedError("DD-S 阶段由结构设计师实现 validate_url 骨架")

    def validate_path(self, path: str) -> bool:
        """
        [函数名] validate_path
        [职责] 校验本地文件路径是否存在且可读
        [关联接口契约] IC-005（DD-001）
        [参数说明]
          参数1: path str 必填 无默认 绝对路径
        [返回值]
          类型: bool
          描述: True=存在且可读，False=不存在或不可读
        [错误码] E_DL_LOCAL_001 - 文件不存在或不可读
        [前置条件] path 为绝对路径字符串
        [后置条件] 校验后无副作用
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 1s
        [来源标注] [DD-001:IC-005]
        """
        # TODO(DD-M-001-20260601): 调用 Path(path).exists() / is_file()
        # TODO(DD-M-001-20260601): 校验后缀 in {.mp4, .webm, .mkv}
        raise NotImplementedError("DD-S 阶段由结构设计师实现 validate_path 骨架")


# =============================================================================
# 子模块 2：platform_resolver（IC-002 平台识别）
# =============================================================================
class PlatformResolver:
    """
    [类名] PlatformResolver
    [职责] URL → Platform 枚举识别（BILIBILI/YOUTUBE/LOCAL/UNKNOWN）
    [关联设计规范] MD-001（DD-001）/ IC-002（DD-001）

    [属性]
      属性1: regex_patterns dict[str, str] 平台 → 正则模式

    [方法列表]
      方法1: resolve(url) -> Platform 识别 URL 平台
      方法2: get_enum(platform_str) -> Platform 字面量 → 枚举

    [状态机] N/A

    [异常处理]
      异常1: 无 - 未知 URL 返回 Platform.UNKNOWN

    [来源标注] [DD-001:MD-001] [DD-001:IC-002]
    """

    def __init__(self) -> None:
        """
        [函数名] __init__
        [职责] 初始化正则模式字典
        [来源标注] [DD-M推断:MD-001 子模块]
        """
        # TODO(DD-M-001-20260601): 初始化 self.regex_patterns = {PLATFORM_YOUTUBE: _RE_YOUTUBE, ...}
        pass

    def resolve(self, url: str) -> str:
        """
        [函数名] resolve
        [职责] 根据 URL 形态识别平台
        [关联接口契约] IC-002（DD-001）
        [参数说明]
          参数1: url str 必填 无默认 视频 URL 或本地路径，长度 1-2048
        [返回值]
          类型: str（Platform 字面量）
          描述: PLATFORM_YOUTUBE / PLATFORM_BILIBILI / PLATFORM_LOCAL / PLATFORM_UNKNOWN
          特殊值: 未知 URL 返回 PLATFORM_UNKNOWN
        [错误码] -（无错误码，unknown 返回 UNKNOWN）
        [前置条件] url 非空字符串
        [后置条件] 返回值必为 4 个 Platform 字面量之一
        [并发安全] 是（无副作用）
        [幂等性] 是（幂等键 = url）
        [性能约束] < 10ms
        [来源标注] [DD-001:IC-002]
        """
        # TODO(DD-M-001-20260601): 若 url 以 '/' 开头或含盘符（Windows）→ LOCAL
        # TODO(DD-M-001-20260601): 正则匹配 youtube → YOUTUBE；bilibili → BILIBILI
        # TODO(DD-M-001-20260601): 未匹配 → UNKNOWN
        raise NotImplementedError("DD-S 阶段由结构设计师实现 resolve 骨架")

    def get_enum(self, platform_str: str) -> str:
        """
        [函数名] get_enum
        [职责] 字面量 → 规范化枚举（大小写不敏感）
        [参数说明]
          参数1: platform_str str 必填 无默认 平台字面量
        [返回值]
          类型: str
          描述: 规范化后的 Platform 字面量
        [错误码] -（未识别返回 UNKNOWN）
        [来源标注] [DD-M推断:MD-001 类设计]
        """
        # TODO(DD-M-001-20260601): platform_str.lower() 映射
        raise NotImplementedError("DD-S 阶段由结构设计师实现 get_enum 骨架")


# =============================================================================
# 子模块 3：config_loader（环境变量 + LLMConfig 强制覆盖）
# =============================================================================
class LLMConfigLoader:
    """
    [类名] LLMConfigLoader
    [职责] 从环境变量加载 LLM 配置，强制覆盖文件配置
    [关联设计规范] MD-001（DD-001）

    [属性]
      属性1: api_keys dict[str, str] 模型 → API Key
      属性2: model_names dict[str, str] 角色（primary/fallback）→ 模型名

    [方法列表]
      方法1: load_from_env() -> dict[str, str] 从环境变量加载
      方法2: override(config: dict) -> dict 强制覆盖文件配置

    [状态机] N/A

    [异常处理]
      异常1: E_LLM_001 - 双模型 Key 均缺失（错误码登记，不阻塞 CLI 启动）

    [来源标注] [DD-001:MD-001]
    """

    def __init__(self) -> None:
        """
        [函数名] __init__
        [职责] 初始化配置加载器
        [来源标注] [DD-M推断:MD-001 子模块]
        """
        # TODO(DD-M-001-20260601): 初始化 self.api_keys = {} / self.model_names = {}
        pass

    def load_from_env(self) -> dict[str, str]:
        """
        [函数名] load_from_env
        [职责] 从环境变量读取 LLM API Key / 模型名 / base_url
        [参数说明] 无
        [返回值]
          类型: dict[str, str]
          描述: 配置字典，含 api_key_deepseek / api_key_qwen / model / base_url
        [错误码] E_LLM_001 - 双模型 Key 均缺失时登记（仅警告，不抛错）
        [前置条件] 环境变量已设置
        [后置条件] 返回字典
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 5ms
        [来源标注] [DD-001:MD-001 子模块3] [DD-M推断:API Key 强制 env]
        """
        # TODO(DD-M-001-20260601): 读取 os.getenv(ENV_DEEPSEEK_API_KEY) 等
        # TODO(DD-M-001-20260601): 若 DEEPSEEK_API_KEY 与 QWEN_API_KEY 均缺失 → 登记 E_LLM_001
        raise NotImplementedError("DD-S 阶段由结构设计师实现 load_from_env 骨架")

    def override(self, config: dict) -> dict:
        """
        [函数名] override
        [职责] 强制用环境变量覆盖文件配置（避免文件配置被恶意篡改）
        [参数说明]
          参数1: config dict 必填 无默认 文件配置字典
        [返回值]
          类型: dict
          描述: 覆盖后的配置字典
        [错误码] -
        [前置条件] config 为 dict
        [后置条件] 环境变量优先级最高
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 5ms
        [来源标注] [DD-M推断:MD-001 子模块3]
        """
        # TODO(DD-M-001-20260601): env_config = self.load_from_env()
        # TODO(DD-M-001-20260601): merged = {**config, **env_config}
        raise NotImplementedError("DD-S 阶段由结构设计师实现 override 骨架")


# =============================================================================
# 子模块 4：dispatcher（IC-003 并发调度）
# =============================================================================
class Dispatcher:
    """
    [类名] Dispatcher
    [职责] URL 列表分发到 M-012 并发编排器，收集结果
    [关联设计规范] MD-001（DD-001）/ IC-003（DD-001）

    [属性]
      属性1: orchestrator Callable 异步任务编排器（M-012 gather_tasks）

    [方法列表]
      方法1: dispatch(urls, task_func) -> list[Result] 异步分发
      方法2: collect_results(raw_results) -> list[Result] 结果归一化

    [状态机] N/A（无状态包装层）

    [异常处理]
      异常1: E_LIM_001 - URL > 10
      异常2: 任务异常 - 经 M-012 隔离后由 collect_results 标记为 failed

    [来源标注] [DD-001:MD-001] [DD-001:IC-003]
    """

    def __init__(self) -> None:
        """
        [函数名] __init__
        [职责] 初始化 Dispatcher
        [来源标注] [DD-M推断:MD-001 子模块]
        """
        # TODO(DD-M-001-20260601): self.orchestrator = None（运行时注入 M-012 gather_tasks）
        pass

    async def dispatch(
        self,
        urls: list["VideoURL"],
        task_func: Callable,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> list["Result"]:
        """
        [函数名] dispatch
        [职责] 异步分发 URL 列表到 M-012 并发编排器
        [关联接口契约] IC-003（DD-001）
        [参数说明]
          参数1: urls list[VideoURL] 必填 无默认 URL 列表，长度 1-10
          参数2: task_func Callable 必填 无默认 异步任务函数
          参数3: concurrency int 可选 默认 DEFAULT_CONCURRENCY=3 并发上限
        [返回值]
          类型: list[Result]
          描述: 任务结果列表（DE-009），长度 == len(urls)
        [错误码]
          错误码1: E_LIM_001 - URL 数量超过 10
          错误码2: 任务异常 - 隔离（return_exceptions=True）
        [前置条件] urls 非空；M-012 已初始化
        [后置条件] results 长度 == urls 长度
        [并发安全] 是（Semaphore(3) 保护）
        [幂等性] 否（任务副作用不可重放）
        [性能约束] 启动 < 50ms / 总耗时 = 最慢任务 + 排队时间
        [示例]
          ```
          dispatcher = Dispatcher()
          results = await dispatcher.dispatch(urls, task_func, concurrency=3)
          ```
        [来源标注] [DD-001:IC-003] [DD-001:DE-009]
        """
        # TODO(DD-M-001-20260601): 校验 len(urls) <= MAX_URL_COUNT
        # TODO(DD-M-001-20260601): 注入 self.orchestrator = M-012.gather_tasks
        # TODO(DD-M-001-20260601): 调用 await self.orchestrator(urls, task_func, concurrency=concurrency)
        raise NotImplementedError("DD-S 阶段由结构设计师实现 dispatch 骨架")

    def collect_results(self, raw_results: list) -> list["Result"]:
        """
        [函数名] collect_results
        [职责] 将 M-012 原始结果归一化为 DE-009 Result 列表
        [参数说明]
          参数1: raw_results list 必填 无默认 M-012 gather_tasks 原始结果
        [返回值]
          类型: list[Result]
          描述: 归一化后的 Result 列表（含 status: success/failed）
        [错误码] -（内部异常隔离）
        [前置条件] raw_results 长度 == urls 长度
        [后置条件] 每个 Result 包含 task_id / status / output / error
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 10ms
        [来源标注] [DD-M推断:MD-001 子模块4]
        """
        # TODO(DD-M-001-20260601): 遍历 raw_results，构造 Result
        # TODO(DD-M-001-20260601): 若元素为 Exception 实例 → status=failed, error=...
        raise NotImplementedError("DD-S 阶段由结构设计师实现 collect_results 骨架")


# =============================================================================
# 子模块 5：rag_entry（IC-004 RAG 问答入口）
# =============================================================================
class RAGEntry:
    """
    [类名] RAGEntry
    [职责] 单次 RAG 问答入口（封装 M-006 summarize_rag）
    [关联设计规范] MD-001（DD-001）/ IC-004（DD-001）

    [属性]
      属性1: transcript_repo Optional[Callable] 转写稿仓库（注入 M-005）
      属性2: llm_client Optional[Callable] LLM 客户端（注入 M-006 summarize_rag）

    [方法列表]
      方法1: query(query, context) -> str 单次 RAG 问答
      方法2: format_answer(answer, tokens_used) -> str 格式化输出

    [状态机] N/A

    [异常处理]
      异常1: E_LLM_001 - LLM 双模型均失败

    [来源标注] [DD-001:MD-001] [DD-001:IC-004]
    """

    def __init__(self) -> None:
        """
        [函数名] __init__
        [职责] 初始化 RAGEntry（依赖注入）
        [来源标注] [DD-M推断:MD-001 子模块]
        """
        # TODO(DD-M-001-20260601): self.transcript_repo = None / self.llm_client = None
        pass

    def query(self, query: str, context: object) -> str:
        """
        [函数名] query
        [职责] 基于 context（Transcript | LLMSummary）回答用户问题
        [关联接口契约] IC-004（DD-001）/ IC-015（DD-001）
        [参数说明]
          参数1: query str 必填 无默认 用户问题，长度 1-500
          参数2: context object 必填 无默认 上下文（Transcript 或 LLMSummary）
        [返回值]
          类型: str
          描述: LLM 答案（长度 ≥ 1）
        [错误码] E_LLM_001 - LLM 双模型均失败
        [前置条件] context 非空
        [后置条件] answer 长度 ≥ 1
        [并发安全] 否（单次调用）
        [幂等性] 否（LLM 输出有随机性）
        [性能约束] < 10s
        [来源标注] [DD-001:IC-004] [DD-001:IC-015]
        """
        # TODO(DD-M-001-20260601): 校验 context 非空
        # TODO(DD-M-001-20260601): 调用 M-006 summarize_rag(query, context)
        raise NotImplementedError("DD-S 阶段由结构设计师实现 query 骨架")

    def format_answer(self, answer: str, tokens_used: int, model: str) -> str:
        """
        [函数名] format_answer
        [职责] 格式化 RAG 答案（含 tokens_used / model 展示）
        [参数说明]
          参数1: answer str 必填 无默认 LLM 答案
          参数2: tokens_used int 必填 无默认 token 消耗
          参数3: model str 必填 无默认 使用的模型
        [返回值]
          类型: str
          描述: 格式化后的输出（含元信息）
        [错误码] -
        [前置条件] answer 非空
        [后置条件] 输出包含 answer + tokens_used + model
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 5ms
        [来源标注] [DD-M推断:MD-001 类方法]
        """
        # TODO(DD-M-001-20260601): 拼接 answer + tokens_used + model
        raise NotImplementedError("DD-S 阶段由结构设计师实现 format_answer 骨架")


# =============================================================================
# 模块级函数（5 个函数签名）
# =============================================================================
def parse_argv(argv: list[str]) -> "CLIArgs":
    """
    [函数名] parse_argv
    [职责] 解析用户 argv，校验后返回 CLIArgs
    [关联接口契约] IC-001（DD-001）
    [参数说明]
      参数1: argv list[str] 必填 无默认 sys.argv 列表，长度 1-100
    [返回值]
      类型: CLIArgs
      描述: 解析后的结构化参数对象
    [错误码] E_DL_001 / E_DL_LOCAL_001 / E_LIM_001
    [前置条件] argv 非空
    [后置条件] CLIArgs.parsed_urls 全部为有效 VideoURL
    [并发安全] 否（一次性 CLI 入口）
    [幂等性] 是
    [性能约束] < 100ms
    [示例]
      ```
      cli_args = parse_argv(["--urls", "https://www.bilibili.com/video/BV1xx"])
      ```
    [来源标注] [DD-001:MD-001] [DD-001:IC-001]
    """
    # TODO(DD-M-001-20260601): 构造 CLIArgParser 实例并调用 parse(argv)
    raise NotImplementedError("DD-S 阶段由结构设计师实现 parse_argv 骨架")


def resolve_platform(url: str) -> str:
    """
    [函数名] resolve_platform
    [职责] URL → Platform 枚举识别
    [关联接口契约] IC-002（DD-001）
    [参数说明]
      参数1: url str 必填 无默认 视频 URL 或本地路径
    [返回值]
      类型: str（Platform 字面量）
      描述: PLATFORM_YOUTUBE / PLATFORM_BILIBILI / PLATFORM_LOCAL / PLATFORM_UNKNOWN
    [错误码] -（无错误码）
    [前置条件] url 非空
    [后置条件] 返回值必为 4 个 Platform 字面量之一
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 10ms
    [来源标注] [DD-001:MD-001] [DD-001:IC-002]
    """
    # TODO(DD-M-001-20260601): 构造 PlatformResolver 实例并调用 resolve(url)
    raise NotImplementedError("DD-S 阶段由结构设计师实现 resolve_platform 骨架")


def load_llm_config() -> dict[str, str]:
    """
    [函数名] load_llm_config
    [职责] 从环境变量加载 LLM 配置
    [参数说明] 无
    [返回值]
      类型: dict[str, str]
      描述: 配置字典
    [错误码] E_LLM_001 - 双模型 Key 均缺失时登记
    [前置条件] 环境变量
    [后置条件] 返回 dict
    [并发安全] 是
    [幂等性] 是
    [性能约束] < 5ms
    [来源标注] [DD-001:MD-001] [DD-M推断:API Key 强制 env]
    """
    # TODO(DD-M-001-20260601): 构造 LLMConfigLoader 实例并调用 load_from_env()
    raise NotImplementedError("DD-S 阶段由结构设计师实现 load_llm_config 骨架")


async def dispatch_tasks(
    urls: list["VideoURL"],
    task_func: Callable,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list["Result"]:
    """
    [函数名] dispatch_tasks
    [职责] 异步分发 URL 列表到 M-012 并发编排器
    [关联接口契约] IC-003（DD-001）/ IC-029（DD-001）
    [参数说明]
      参数1: urls list[VideoURL] 必填 无默认 URL 列表，长度 1-10
      参数2: task_func Callable 必填 无默认 异步任务函数
      参数3: concurrency int 可选 默认 DEFAULT_CONCURRENCY=3
    [返回值]
      类型: list[Result]
      描述: 任务结果列表
    [错误码] E_LIM_001
    [前置条件] urls 非空
    [后置条件] results 长度 == urls 长度
    [并发安全] 是
    [幂等性] 否
    [性能约束] 启动 < 50ms
    [来源标注] [DD-001:MD-001] [DD-001:IC-003] [DD-001:IC-029]
    """
    # TODO(DD-M-001-20260601): 构造 Dispatcher 实例并调用 await dispatcher.dispatch(urls, task_func, concurrency)
    raise NotImplementedError("DD-S 阶段由结构设计师实现 dispatch_tasks 骨架")


def rag_query(query: str, context: object) -> str:
    """
    [函数名] rag_query
    [职责] 单次 RAG 问答入口
    [关联接口契约] IC-004（DD-001）/ IC-015（DD-001）
    [参数说明]
      参数1: query str 必填 无默认 用户问题，长度 1-500
      参数2: context object 必填 无默认 上下文
    [返回值]
      类型: str
      描述: LLM 答案
    [错误码] E_LLM_001
    [前置条件] context 非空
    [后置条件] answer 长度 ≥ 1
    [并发安全] 否
    [幂等性] 否
    [性能约束] < 10s
    [来源标注] [DD-001:MD-001] [DD-001:IC-004]
    """
    # TODO(DD-M-001-20260601): 构造 RAGEntry 实例并调用 query(query, context)
    raise NotImplementedError("DD-S 阶段由结构设计师实现 rag_query 骨架")


# =============================================================================
# CLI 顶层入口
# =============================================================================
def main(argv: Optional[list[str]] = None) -> int:
    """
    [函数名] main
    [职责] CLI 顶层入口：解析 argv → preflight → dispatch / rag → 退出码仲裁
    [关联设计规范] MD-001（DD-001）/ DP-001（DD-001）
    [参数说明]
      参数1: argv Optional[list[str]] 可选 None 默认值 None sys.argv 列表（None 时取 sys.argv[1:]）
    [返回值]
      类型: int
      描述: 退出码（0 成功 / 1 通用 / 2 校验失败 / 3 依赖缺失 / 4 部分失败）
    [错误码]
      错误码1: E_DL_001 - 非法 URL
      错误码2: E_DL_001_DENO_MISSING - Deno 缺失（preflight）
      错误码3: E_DL_LOCAL_001 - 本地文件不存在
      错误码4: E_LIM_001 - URL > 10
      错误码5: E_LLM_001 - LLM 双模型均失败（RAG 模式）
    [前置条件] argv 非空或 sys.argv 可用
    [后置条件] sys.exit(exit_code) 已调用
    [并发安全] 否（顶层入口）
    [幂等性] 否（任务副作用）
    [性能约束] 无 SLA（顶层入口）
    [异常处理] 顶层 try-except：异常 → M-010 register_error → resolve_exit_code → 顶层打印 → sys.exit
    [示例]
      ```
      $ research --urls https://www.bilibili.com/video/BV1xx --topic ai
      $ research --query "什么是 LRU?" --video-file notes/abc.md
      ```
    [来源标注] [DD-001:MD-001] [DD-001:DP-001]
    """
    # TODO(DD-M-001-20260601): try-except 包裹
    # TODO(DD-M-001-20260601): argv = argv or sys.argv[1:]
    # TODO(DD-M-001-20260601): cli_args = parse_argv(argv)
    # TODO(DD-M-001-20260601): M-011 emit_log argv_sha256
    # TODO(DD-M-001-20260601): M-002 check_all() → PreflightReport
    # TODO(DD-M-001-20260601): 若 report.is_blocking → EXIT_DEP_MISSING
    # TODO(DD-M-001-20260601): 若 --query 模式 → rag_query() → print
    # TODO(DD-M-001-20260601): 否则 → dispatch_tasks() → collect_results
    # TODO(DD-M-001-20260601): exit_code = M-010 resolve_exit_code(records)
    # TODO(DD-M-001-20260601): return exit_code
    raise NotImplementedError("DD-S 阶段由结构设计师实现 main 骨架")


# =============================================================================
# 入口检查
# =============================================================================
if __name__ == "__main__":
    # TODO(DD-M-001-20260601): sys.exit(main())
    raise NotImplementedError("DD-S 阶段由结构设计师实现 __main__ 入口")
