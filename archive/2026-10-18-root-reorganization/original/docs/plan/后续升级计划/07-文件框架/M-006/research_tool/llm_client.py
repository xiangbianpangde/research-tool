# -*- coding: utf-8 -*-
"""
[文件路径] research_tool/llm_client.py
[文件职责]  LLM 客户端：双模型调用 + 字段名校验 + 责任链 fallback
[所属模块]  M-006（LLM 客户端）
[关联设计规范]  FS-006（DD-001）/ MD-006（DD-001）/ IC-013/IC-014/IC-015（DD-001）
[关联技术选型]  TS-007（Deepseek-v4-flash）/ TS-008（Qwen-turbo）/ TS-013（httpx ≥ 0.27）
[关联数据结构]  DE-005（Transcript，from M-005）/ DE-006（LLMSummary，self-owned）
[关联错误码]    E_LLM_001（双模型均失败）/ E_LLM_002_CHAPTERS_FALLBACK（章节降级）

[功能描述]
  功能1: 通过适配器模式封装 Deepseek-v4-flash 主模型与 Qwen-turbo 备用模型
  功能2: 责任链 fallback 流程：Deepseek 5xx 3 次 → 切换 Qwen
  功能3: 字段名前缀校验（video_）含重试 1 次
  功能4: Prompt 3 段式拼装（system / user / assistant anchor）
  功能5: Token 计数与 20k input / 4k output 截断
  功能6: RAG 问答单次调用（IC-004 / IC-015）

[输入输出]
  输入:  Transcript（DE-005）+ style（"academic"/"casual"/"tutorial"）+ 可选 VideoMeta
  输出:  LLMSummary（DE-006）含 model_used / input_tokens / output_tokens / chapters
  旁路:  rag_query(query, context) → str 答案

[依赖关系]
  依赖文件:
    - research_tool/datatypes.py（DE-005/DE-006/DE-007）
    - research_tool/error_handler.py（M-010，register_error）
    - research_tool/structured_logger.py（M-011，emit_log）
  被依赖文件:
    - research_tool/notes_schema.py（M-007，调用 summarize/validate）
    - research_tool/cli.py（M-001，调用 rag_query）
    - research_tool/concurrent_orchestrator.py（M-012，调度 summarize）

[注意事项]
  注意1: API Key 必须从环境变量加载，绝不落盘或入日志（由 M-011 SensitiveFilter 强制）
  注意2: Deepseek 5xx 连续 3 次 → 触发 fallback；429 限流不计入 5xx 但记录为 WARN
  注意3: front_matter 校验失败可重试 1 次；仍失败时整段丢弃不阻塞主链
  注意4: input > 20k tokens 必须截断并打 WARN；output 截断到 4k tokens
  注意5: httpx 客户端使用连接池（limits=httpx.Limits(max_connections=10)）
  注意6: Prompt 模板中严禁插入 Cookie / API Key 等敏感字段
  注意7: 章节降级钩子（IC-021/EP-002）在 V1.1 固定 5min 等距切片
  注意8: 本模块不直接落盘 Markdown，输出 LLMSummary 由 M-007 拼装
  注意9: 线程安全：httpx.AsyncClient 实例应模块级单例；同步 LLM 调用包在 asyncio.to_thread
  注意10: 设计模式：适配器模式（DeepseekClient/QwenClient 共享 LLMClient 协议）+ 责任链模式（主→备）

[代码风格]  遵循 CS-001（Python 3.11+，4 空格缩进，snake_case，Google docstring，强制类型注解）
[创建日期]  2026-06-01
[修改历史]
  2026-06-01: DD-M-006-20260601 - 初版文件框架（仅注释，骨架占位 pass）
[作者]  DD-M-006-20260601
[来源标注]  [DD-001:FS-006] [DD-001:MD-006] [DD-001:IC-013/IC-014/IC-015] [DD-001:DE-005/DE-006]
"""


# =============================================================================
# 标准库导入
# =============================================================================
from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Any, Optional, Union

# =============================================================================
# 第三方导入
# =============================================================================
import httpx  # TS-013

# =============================================================================
# 本地导入（来自同包其他模块，禁止循环依赖）
# =============================================================================
# 注：以下导入在下游开发工程师填充 import 路径时按 FS-006 依赖图校对：
#   - DE-005 Transcript / DE-006 LLMSummary / DE-007 Chapter  →  research_tool.datatypes
#   - M-010 register_error                                  →  research_tool.error_handler
#   - M-011 emit_log                                        →  research_tool.structured_logger
#
# [DD-M推断] 严格遵循 FS-006 的依赖方向（datatypes ← llm_client → error_handler/logger），
#            禁止 llm_client 被 datatypes 反向引用，禁止 llm_client → notes_schema。
# [来源标注]  [DD-001:FS-006 依赖关系图] [DD-001:CS-001 导入规范]


# =============================================================================
# 模块级常量（UPPER_SNAKE_CASE，符合 CS-001）
# =============================================================================
#: Deepseek 主模型标识
DEEPSEEK_MODEL: str = "deepseek-v4-flash"
#: Qwen 备用模型标识
QWEN_MODEL: str = "qwen-turbo"
#: Deepseek base URL
DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
#: Qwen base URL
QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
#: input 截断上限（tokens）
MAX_INPUT_TOKENS: int = 20_000
#: output 截断上限（tokens）
MAX_OUTPUT_TOKENS: int = 4_000
#: Deepseek 连续 5xx 阈值
DEEPSEEK_5XX_RETRY: int = 3
#: front_matter 校验失败重试次数
FRONT_MATTER_RETRY: int = 1
#: 单次 HTTP 调用超时（秒）
HTTP_TIMEOUT_SEC: float = 30.0
#: 连接池上限
HTTP_MAX_CONNECTIONS: int = 10
#: front_matter 字段名前缀（whitelist）
VIDEO_FIELD_PREFIX: str = "video_"
#: 默认 summary 风格
DEFAULT_SUMMARY_STYLE: str = "academic"
#: 合法 style 集合
VALID_STYLES: frozenset[str] = frozenset({"academic", "casual", "tutorial"})
#: 错误码常量
E_LLM_001: str = "E_LLM_001"
E_LLM_002_CHAPTERS_FALLBACK: str = "E_LLM_002_CHAPTERS_FALLBACK"


# =============================================================================
# 类 1: DeepseekClient
# =============================================================================
class DeepseekClient:
    """
    [类名]  DeepseekClient
    [职责]  适配 Deepseek-v4-flash 聊天补全 API
    [关联设计规范]  MD-006 子模块1
    [关联接口契约]  IC-013（LLM 总结）
    [设计模式]  适配器模式（与 QwenClient 共享同一 LLMClient 协议）

    [属性]
      属性1: api_key: str            - Deepseek API Key（从环境变量 DEEPSEEK_API_KEY 加载）
      属性2: base_url: str           - Deepseek base URL（默认 https://api.deepseek.com/v1）
      属性3: model: str              - 模型标识（默认 deepseek-v4-flash）
      属性4: client: httpx.AsyncClient - httpx 异步客户端（模块级单例复用）
      属性5: max_retries: int        - 单调用内 5xx 重试次数（默认 3）

    [方法列表]
      方法1: summarize(prompt, system) -> str  - 发送聊天补全请求，含 5xx 重试
      方法2: _post_chat(messages, temperature) -> dict  - 底层 POST 封装
      方法3: _parse_response(payload) -> tuple[str,int,int]  - 解析 content/input_tokens/output_tokens
      方法4: _handle_5xx(resp) -> None  - 5xx 异常转换（提升到链上级）
      方法5: close() -> None  - 关闭 httpx 客户端

    [状态机]
      IDLE → [summarize] → CALLING
      CALLING → [200] → DONE
      CALLING → [5xx retry<3] → CALLING
      CALLING → [5xx retry==3] → FALLBACK_TRIGGER（由 ChainOfResponsibility 切换）
      CALLING → [4xx/429] → ERROR_RAISED

    [异常处理]
      异常1: httpx.HTTPStatusError（5xx 连续 3 次）- 触发 fallback，不登记 E_LLM_001
      异常2: httpx.TimeoutException - 计入 5xx 重试序列
      异常3: ValueError（响应解析失败）- 抛出由上层 ChainOfResponsibility 捕获

    [并发安全]  httpx.AsyncClient 内部连接池线程安全
    [来源标注]  [DD-001:MD-006 类1] [DD-001:IC-013] [DD-001:TS-007] [DD-001:TS-013]
    """

    api_key: str
    base_url: str
    model: str
    client: httpx.AsyncClient
    max_retries: int

    def __init__(
        self,
        api_key: str,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        max_retries: int = DEEPSEEK_5XX_RETRY,
    ) -> None:
        """
        [函数名]  __init__
        [职责]  初始化 Deepseek 客户端，构造 httpx 异步连接池
        [参数说明]
          参数1: api_key        str   必填  无默认  描述: Deepseek API Key
          参数2: base_url       str   可选  DEEPSEEK_BASE_URL  描述: API 根 URL
          参数3: model          str   可选  DEEPSEEK_MODEL     描述: 模型标识
          参数4: max_retries    int   可选  3                  描述: 5xx 重试次数上限
        [返回值]  None
        [错误码]  -
        [前置条件]  api_key 非空字符串
        [后置条件]  self.client 已创建并配置连接池
        [并发安全]  否（构造期一次性）
        [来源标注]  [DD-001:MD-006 类1]
        """
        # 业务代码占位：构造 self.client = httpx.AsyncClient(limits=..., timeout=...)
        ...

    async def summarize(self, prompt: str, system: str = "") -> str:
        """
        [函数名]  summarize
        [职责]  调用 Deepseek 聊天补全，返回 LLM 文本
        [关联接口契约]  IC-013
        [参数说明]
          参数1: prompt    str  必填  无默认  描述: 用户侧 prompt（已含 3 段式拼装结果）
          参数2: system    str  可选  ""      描述: 系统 prompt（如 "You are a notes assistant"）
        [返回值]
          类型:  str
          描述:  LLM 输出的 Markdown 文本（含 front_matter + body）
          特殊值:  不返回空串；连续 3 次 5xx 时 raise 以触发 fallback
        [错误码]  -
        [前置条件]  self.client 已初始化；api_key 有效
        [后置条件]  调用后 self._post_chat 至少被调用 1 次
        [并发安全]  是（httpx 连接池安全）
        [幂等性]  否（LLM 输出有随机性；不同 temperature 下结果不同）
        [性能约束]  < 5s（P50, 20k input）
        [示例]
          ```
          client = DeepseekClient(api_key="sk-...")
          text = await client.summarize(prompt="...", system="...")
          ```
        [来源标注]  [DD-001:IC-013] [DD-001:MD-006 类1 方法1]
        """
        # 业务代码占位：组装 messages → _post_chat → 返回 content
        ...

    async def _post_chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """
        [函数名]  _post_chat
        [职责]  底层 POST 封装，含 5xx 重试与指数退避
        [参数说明]
          参数1: messages       list[dict]  必填  无默认  描述: 消息序列 [{role, content}]
          参数2: temperature    float      可选  0.3      描述: 采样温度
        [返回值]
          类型:  dict
          描述:  Deepseek API 响应 JSON（choices/usage/model）
        [错误码]  -（5xx 经 _handle_5xx 抛出）
        [前置条件]  messages 非空
        [后置条件]  返回的 dict 必含 "choices"[0]["message"]["content"]
        [并发安全]  是
        [幂等性]  否
        [性能约束]  < 5s
        [来源标注]  [DD-001:MD-006 类1 方法2]
        """
        # 业务代码占位：try POST → except HTTPStatusError → _handle_5xx → retry
        ...

    def _parse_response(
        self, payload: dict[str, Any]
    ) -> tuple[str, int, int]:
        """
        [函数名]  _parse_response
        [职责]  解析 Deepseek 响应，提取 content/input_tokens/output_tokens
        [参数说明]
          参数1: payload    dict  必填  无默认  描述: API 响应 JSON
        [返回值]
          类型:  tuple[str, int, int]
          描述:  (content, input_tokens, output_tokens)
        [错误码]  -
        [前置条件]  payload 含 "choices" 与 "usage"
        [后置条件]  content 长度 ≥ 1
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 1ms
        [来源标注]  [DD-001:MD-006 类1 方法3]
        """
        # 业务代码占位
        ...

    def _handle_5xx(self, resp: httpx.Response) -> None:
        """
        [函数名]  _handle_5xx
        [职责]  5xx 响应处理：超出重试次数时 raise 触发 fallback
        [参数说明]
          参数1: resp    httpx.Response  必填  无默认  描述: 5xx 响应对象
        [返回值]  None（raise 退出）
        [错误码]  -（raise httpx.HTTPStatusError）
        [前置条件]  500 <= resp.status_code < 600
        [后置条件]  raise 异常
        [并发安全]  -
        [幂等性]  -
        [性能约束]  < 1ms
        [来源标注]  [DD-001:MD-006 类1 方法4]
        """
        # 业务代码占位
        ...

    async def close(self) -> None:
        """
        [函数名]  close
        [职责]  关闭 httpx 异步客户端，释放连接池
        [参数说明]  -
        [返回值]  None
        [错误码]  -
        [前置条件]  self.client 已创建
        [后置条件]  连接池关闭
        [并发安全]  否（atexit 阶段调用）
        [幂等性]  是
        [性能约束]  < 100ms
        [来源标注]  [DD-001:MD-006 类1 方法5]
        """
        # 业务代码占位
        ...


# =============================================================================
# 类 2: QwenClient
# =============================================================================
class QwenClient:
    """
    [类名]  QwenClient
    [职责]  适配 Qwen-turbo 聊天补全 API，作为 Deepseek 5xx 3 次后的 fallback
    [关联设计规范]  MD-006 子模块2
    [关联接口契约]  IC-013
    [设计模式]  适配器模式（与 DeepseekClient 同协议不同实现）

    [属性]
      属性1: api_key: str            - 通义千问 API Key（从环境变量 QWEN_API_KEY 加载）
      属性2: base_url: str           - DashScope 兼容模式 base URL
      属性3: model: str              - 模型标识（默认 qwen-turbo）
      属性4: client: httpx.AsyncClient - httpx 异步客户端
      属性5: max_retries: int        - fallback 后单调用内重试次数（默认 2）

    [方法列表]
      方法1: summarize(prompt, system) -> str  - 发送聊天补全请求
      方法2: _post_chat(messages, temperature) -> dict  - 底层 POST 封装
      方法3: _parse_response(payload) -> tuple[str,int,int]  - 解析 content/tokens
      方法4: _handle_5xx(resp) -> None  - 5xx 异常转换
      方法5: close() -> None  - 关闭 httpx 客户端

    [状态机]
      IDLE → [summarize] → CALLING
      CALLING → [200] → DONE
      CALLING → [5xx retry<2] → CALLING
      CALLING → [5xx retry==2] → ERROR_RAISED（链终端，登记 E_LLM_001）
      CALLING → [4xx/429] → ERROR_RAISED

    [异常处理]
      异常1: httpx.HTTPStatusError（5xx 连续 2 次）- 抛出，链上层登记 E_LLM_001
      异常2: httpx.TimeoutException - 计入 5xx 重试序列
      异常3: ValueError（响应解析失败）- 抛出

    [并发安全]  httpx.AsyncClient 内部连接池线程安全
    [来源标注]  [DD-001:MD-006 类2] [DD-001:IC-013] [DD-001:TS-008] [DD-001:TS-013]
    """

    api_key: str
    base_url: str
    model: str
    client: httpx.AsyncClient
    max_retries: int

    def __init__(
        self,
        api_key: str,
        base_url: str = QWEN_BASE_URL,
        model: str = QWEN_MODEL,
        max_retries: int = 2,
    ) -> None:
        """
        [函数名]  __init__
        [职责]  初始化 Qwen fallback 客户端
        [参数说明]
          参数1: api_key        str  必填  无默认       描述: Qwen API Key
          参数2: base_url       str  可选  QWEN_BASE_URL 描述: API 根 URL
          参数3: model          str  可选  QWEN_MODEL    描述: 模型标识
          参数4: max_retries    int  可选  2             描述: fallback 后重试次数
        [返回值]  None
        [错误码]  -
        [前置条件]  api_key 非空
        [后置条件]  self.client 已创建
        [并发安全]  否
        [来源标注]  [DD-001:MD-006 类2]
        """
        # 业务代码占位
        ...

    async def summarize(self, prompt: str, system: str = "") -> str:
        """
        [函数名]  summarize
        [职责]  调用 Qwen 聊天补全（fallback 阶段），返回 LLM 文本
        [关联接口契约]  IC-013
        [参数说明]
          参数1: prompt    str  必填  无默认  描述: 用户侧 prompt
          参数2: system    str  可选  ""      描述: 系统 prompt
        [返回值]
          类型:  str
          描述:  LLM 输出的 Markdown 文本
        [错误码]  -
        [前置条件]  -
        [后置条件]  -
        [并发安全]  是
        [幂等性]  否
        [性能约束]  < 8s（fallback 阶段允许较慢）
        [来源标注]  [DD-001:IC-013] [DD-001:MD-006 类2 方法1]
        """
        # 业务代码占位
        ...

    async def _post_chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """
        [函数名]  _post_chat
        [职责]  底层 POST 封装
        [参数说明]
          参数1: messages       list[dict]  必填  无默认  描述: 消息序列
          参数2: temperature    float      可选  0.3      描述: 采样温度
        [返回值]
          类型:  dict
          描述:  Qwen API 响应 JSON（OpenAI 兼容格式）
        [错误码]  -
        [并发安全]  是
        [幂等性]  否
        [性能约束]  < 8s
        [来源标注]  [DD-001:MD-006 类2 方法2]
        """
        # 业务代码占位
        ...

    def _parse_response(
        self, payload: dict[str, Any]
    ) -> tuple[str, int, int]:
        """
        [函数名]  _parse_response
        [职责]  解析 Qwen 响应
        [参数说明]
          参数1: payload    dict  必填  无默认  描述: API 响应 JSON
        [返回值]
          类型:  tuple[str, int, int]
          描述:  (content, input_tokens, output_tokens)
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 1ms
        [来源标注]  [DD-001:MD-006 类2 方法3]
        """
        # 业务代码占位
        ...

    def _handle_5xx(self, resp: httpx.Response) -> None:
        """
        [函数名]  _handle_5xx
        [职责]  5xx 响应处理（fallback 终端）
        [参数说明]
          参数1: resp    httpx.Response  必填  无默认  描述: 5xx 响应对象
        [返回值]  None（raise 退出）
        [错误码]  -
        [并发安全]  -
        [来源标注]  [DD-001:MD-006 类2 方法4]
        """
        # 业务代码占位
        ...

    async def close(self) -> None:
        """
        [函数名]  close
        [职责]  关闭 httpx 异步客户端
        [并发安全]  否
        [幂等性]  是
        [来源标注]  [DD-001:MD-006 类2 方法5]
        """
        # 业务代码占位
        ...


# =============================================================================
# 类 3: PromptBuilder
# =============================================================================
class PromptBuilder:
    """
    [类名]  PromptBuilder
    [职责]  3 段式 Prompt 拼装（system / user / assistant anchor）
    [关联设计规范]  MD-006 子模块3
    [关联接口契约]  IC-013 / IC-015
    [设计模式]  Builder 模式

    [属性]
      属性1: template_summary: str  - 总结场景 prompt 模板（含 {style} {transcript}）
      属性2: template_rag: str      - RAG 场景 prompt 模板（含 {context} {query}）
      属性3: system_role: str       - 系统角色描述（"You are a note-taking assistant"）

    [方法列表]
      方法1: build_summary_prompt(transcript, style, video_meta) -> tuple[str, str]  - 返回 (user, system)
      方法2: build_rag_prompt(query, context) -> tuple[str, str]                     - 返回 (user, system)
      方法3: _truncate_input(text, max_tokens) -> str                                - token 截断

    [状态机]  N/A（无状态）

    [异常处理]
      异常1: ValueError（style 不在 VALID_STYLES）- 抛出由调用方降级到 default

    [并发安全]  是（无状态）
    [来源标注]  [DD-001:MD-006 类3] [DD-001:IC-013] [DD-001:IC-015]
    """

    template_summary: str
    template_rag: str
    system_role: str

    def __init__(self) -> None:
        """
        [函数名]  __init__
        [职责]  初始化 prompt 模板常量
        [并发安全]  否
        [来源标注]  [DD-001:MD-006 类3]
        """
        # 业务代码占位：加载模板字符串
        ...

    def build_summary_prompt(
        self,
        transcript: "Transcript",  # type: ignore[name-defined]  # noqa: F821
        style: str = DEFAULT_SUMMARY_STYLE,
        video_meta: Optional["VideoMeta"] = None,  # type: ignore[name-defined]  # noqa: F821
    ) -> tuple[str, str]:
        """
        [函数名]  build_summary_prompt
        [职责]  拼装 summary 场景的 (user_prompt, system_prompt)
        [关联接口契约]  IC-013
        [参数说明]
          参数1: transcript    Transcript   必填  无默认  描述: 转写稿（含 segments）
          参数2: style         str         可选  "academic"  描述: 风格（academic/casual/tutorial）
          参数3: video_meta    VideoMeta?  可选  None      描述: 视频元数据
        [返回值]
          类型:  tuple[str, str]
          描述:  (user_prompt, system_prompt)
          特殊值:  user_prompt 必含 "---FRONT_MATTER_START---" 与 "---FRONT_MATTER_END---" 锚点
        [错误码]  -
        [前置条件]  transcript.segments 非空
        [后置条件]  user_prompt 输入部分已通过 _truncate_input 截断到 20k tokens
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 50ms
        [来源标注]  [DD-001:MD-006 类3 方法1] [DD-001:IC-013]
        """
        # 业务代码占位：模板填充 → token 截断
        ...

    def build_rag_prompt(
        self,
        query: str,
        context: Union["Transcript", "LLMSummary"],  # type: ignore[name-defined]  # noqa: F821
    ) -> tuple[str, str]:
        """
        [函数名]  build_rag_prompt
        [职责]  拼装 RAG 场景的 (user_prompt, system_prompt)
        [关联接口契约]  IC-015 / IC-004
        [参数说明]
          参数1: query      str                       必填  无默认  描述: 用户问题
          参数2: context    Transcript | LLMSummary   必填  无默认  描述: 上下文
        [返回值]
          类型:  tuple[str, str]
          描述:  (user_prompt, system_prompt)
        [错误码]  -
        [前置条件]  query 长度 1-500
        [后置条件]  context 已截断到 20k tokens
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 30ms
        [来源标注]  [DD-001:MD-006 类3 方法2] [DD-001:IC-015] [DD-001:IC-004]
        """
        # 业务代码占位
        ...

    def _truncate_input(self, text: str, max_tokens: int = MAX_INPUT_TOKENS) -> str:
        """
        [函数名]  _truncate_input
        [职责]  截断文本到 max_tokens 上限（粗估 1 token ≈ 4 字符）
        [参数说明]
          参数1: text          str  必填  无默认            描述: 原始文本
          参数2: max_tokens    int  可选  MAX_INPUT_TOKENS 描述: 上限
        [返回值]
          类型:  str
          描述:  截断后的文本（必要时追加 "...[truncated]" 标记）
        [错误码]  -
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 10ms（O(n)）
        [来源标注]  [DD-001:MD-006 类3 方法3]
        """
        # 业务代码占位：粗略 token 估算 → 截断
        ...


# =============================================================================
# 类 4: FrontMatterValidator
# =============================================================================
class FrontMatterValidator:
    """
    [类名]  FrontMatterValidator
    [职责]  校验 LLM 输出的 front_matter 字段名是否以 video_ 开头，含重试钩子
    [关联设计规范]  MD-006 子模块4
    [关联接口契约]  IC-014
    [设计模式]  Validator 模式

    [属性]
      属性1: whitelist_prefix: str  - 白名单前缀（默认 "video_"）

    [方法列表]
      方法1: validate(front_matter) -> tuple[dict, list[str]]  - 返回 (validated, invalid_keys)
      方法2: retry_with_feedback(front_matter, llm_client) -> dict  - 失败时回灌 prompt 重新生成
      方法3: _is_valid_key(key) -> bool  - 单键校验

    [状态机]
      INIT → [validate] → ALL_VALID
      INIT → [validate] → HAS_INVALID
      HAS_INVALID → [retry_with_feedback] → REGENERATED
      REGENERATED → [validate] → ALL_VALID | STILL_INVALID（丢弃非法键）

    [异常处理]
      异常1: ValueError（front_matter 非 dict）- 抛出由调用方捕获
      异常2: 校验最终失败 - 不抛错，返回原 dict + invalid_keys 列表

    [并发安全]  是
    [来源标注]  [DD-001:MD-006 类4] [DD-001:IC-014] [DD-001:SR-002] [DD-001:ADR-005]
    """

    whitelist_prefix: str

    def __init__(self, whitelist_prefix: str = VIDEO_FIELD_PREFIX) -> None:
        """
        [函数名]  __init__
        [职责]  初始化字段名前缀白名单
        [参数说明]
          参数1: whitelist_prefix    str  可选  "video_"  描述: 合法的字段名前缀
        [并发安全]  否
        [来源标注]  [DD-001:MD-006 类4]
        """
        # 业务代码占位
        ...

    def validate(
        self, front_matter: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """
        [函数名]  validate
        [职责]  校验 front_matter 字段名前缀
        [关联接口契约]  IC-014
        [参数说明]
          参数1: front_matter    dict  必填  无默认  描述: LLM 输出的 YAML 头部字典
        [返回值]
          类型:  tuple[dict, list[str]]
          描述:  (validated_dict, invalid_keys)
          特殊值:  validated_dict 仅含合法键；invalid_keys 列出被剔除的键
        [错误码]
          错误码1: E_LLM_001  含义: 校验失败 + LLM 重试 1 次 + 仍失败  触发条件: invalid_keys 非空且重试失败
        [前置条件]  front_matter 为 dict
        [后置条件]  validated_dict 所有键以 video_ 开头
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 10ms
        [示例]
          ```
          v = FrontMatterValidator()
          ok, bad = v.validate({"video_title": "...", "wrong_key": "..."})
          # ok == {"video_title": "..."}
          # bad == ["wrong_key"]
          ```
        [来源标注]  [DD-001:IC-014] [DD-001:MD-006 类4 方法1]
        """
        # 业务代码占位：遍历 key → _is_valid_key → 收集 invalid_keys
        ...

    async def retry_with_feedback(
        self,
        front_matter: dict[str, Any],
        llm_client: "DeepseekClient | QwenClient",  # type: ignore[name-defined]  # noqa: F821
    ) -> dict[str, Any]:
        """
        [函数名]  retry_with_feedback
        [职责]  校验失败时回灌 prompt 让 LLM 重新生成（最多 1 次）
        [参数说明]
          参数1: front_matter    dict                          必填  无默认  描述: 失败的字典
          参数2: llm_client      DeepseekClient | QwenClient   必填  无默认  描述: LLM 客户端
        [返回值]
          类型:  dict
          描述:  重新生成的 front_matter（仍可能含非法键，由调用方决定丢弃）
        [错误码]
          错误码1: E_LLM_001  含义: 重试仍失败  触发条件: 重试 1 次后 invalid_keys 仍非空
        [前置条件]  front_matter 非空
        [后置条件]  返回值或 front_matter 至少一个有合法 video_ 字段
        [并发安全]  是
        [幂等性]  否（LLM 重新生成）
        [性能约束]  < 10s（含 1 次 LLM 调用）
        [来源标注]  [DD-001:MD-006 类4 方法2] [DD-001:IC-014]
        """
        # 业务代码占位：构造 feedback prompt → 调 llm_client.summarize → validate
        ...

    def _is_valid_key(self, key: str) -> bool:
        """
        [函数名]  _is_valid_key
        [职责]  单键前缀校验
        [参数说明]
          参数1: key    str  必填  无默认  描述: 字段名
        [返回值]
          类型:  bool
          描述:  True 表示 key 以 whitelist_prefix 开头
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 1μs
        [来源标注]  [DD-001:MD-006 类4 方法3]
        """
        # 业务代码占位
        ...


# =============================================================================
# 类 5: RAGQuery
# =============================================================================
class RAGQuery:
    """
    [类名]  RAGQuery
    [职责]  RAG 问答入口：基于 transcript/summary 回答用户问题
    [关联设计规范]  MD-006 子模块5
    [关联接口契约]  IC-004 / IC-015
    [设计模式]  外观模式（Façade），封装 prompt + 双模型 + 校验

    [属性]
      属性1: llm_client_chain: list  - 责任链：[DeepseekClient, QwenClient]
      属性2: prompt_builder: PromptBuilder  - prompt 拼装器
      属性3: validator: FrontMatterValidator  - 字段校验器（rag 场景不强制使用）

    [方法列表]
      方法1: query(query, context) -> str  - RAG 问答入口
      方法2: format_context(context) -> str  - 上下文格式化

    [状态机]
      INIT → [query] → PROMPT_READY
      PROMPT_READY → [Deepseek 5xx 3 times] → SWITCH_QWEN
      SWITCH_QWEN → [Qwen 5xx 2 times] → ERROR（E_LLM_001）
      任意 OK → [format_answer] → DONE

    [异常处理]
      异常1: E_LLM_001 - 双模型均失败，登记到 M-010
      异常2: E_LLM_002_CHAPTERS_FALLBACK - V1.1 不触发，仅保留钩子

    [并发安全]  是
    [来源标注]  [DD-001:MD-006 类5] [DD-001:IC-015] [DD-001:IC-004]
    """

    llm_client_chain: list
    prompt_builder: PromptBuilder
    validator: FrontMatterValidator

    def __init__(
        self,
        llm_client_chain: list,
        prompt_builder: PromptBuilder,
        validator: FrontMatterValidator,
    ) -> None:
        """
        [函数名]  __init__
        [职责]  注入责任链与依赖
        [参数说明]
          参数1: llm_client_chain    list               必填  无默认  描述: [Deepseek, Qwen]
          参数2: prompt_builder      PromptBuilder     必填  无默认  描述: prompt 拼装
          参数3: validator           FrontMatterValidator  必填  无默认  描述: 字段校验
        [并发安全]  否
        [来源标注]  [DD-001:MD-006 类5]
        """
        # 业务代码占位
        ...

    async def query(
        self,
        query: str,
        context: Union["Transcript", "LLMSummary"],  # type: ignore[name-defined]  # noqa: F821
    ) -> str:
        """
        [函数名]  query
        [职责]  单次 RAG 问答
        [关联接口契约]  IC-015 / IC-004
        [参数说明]
          参数1: query      str                       必填  无默认  描述: 用户问题（1-500 字符）
          参数2: context    Transcript | LLMSummary   必填  无默认  描述: 上下文
        [返回值]
          类型:  str
          描述:  LLM 生成的答案
          特殊值:  不返回空串
        [错误码]
          错误码1: E_LLM_001  含义: 双模型均失败  触发条件: 责任链终端
        [前置条件]  query 非空；context 非空
        [后置条件]  返回值长度 ≥ 1
        [并发安全]  否（单次调用入口）
        [幂等性]
          是否幂等: 否
          幂等键来源: N/A
          幂等有效期: N/A
          重复请求处理: 重新调用
        [性能约束]  < 10s
        [示例]
          ```
          result = await rag.query("主要讲了哪些内容？", context=transcript)
          ```
        [来源标注]  [DD-001:IC-015] [DD-001:IC-004] [DD-001:MD-006 类5 方法1]
        """
        # 业务代码占位：prompt 拼装 → 责任链调用 → 返回 answer
        ...

    def format_context(
        self,
        context: Union["Transcript", "LLMSummary"],  # type: ignore[name-defined]  # noqa: F821
    ) -> str:
        """
        [函数名]  format_context
        [职责]  将 Transcript / LLMSummary 序列化为 prompt 友好的字符串
        [参数说明]
          参数1: context    Transcript | LLMSummary   必填  无默认  描述: 上下文
        [返回值]
          类型:  str
          描述:  序列化后的文本（Transcript 拼 segments；LLMSummary 拼 body）
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 50ms
        [来源标注]  [DD-001:MD-006 类5 方法2]
        """
        # 业务代码占位
        ...


# =============================================================================
# 类 6: TokenCounter
# =============================================================================
class TokenCounter:
    """
    [类名]  TokenCounter
    [职责]  input/output token 计数 + 截断
    [关联设计规范]  MD-006 子模块6
    [关联接口契约]  IC-013（输出 input_tokens/output_tokens）
    [设计模式]  Strategy 模式（粗估 vs tiktoken 精确估算，可切换）

    [属性]
      属性1: encoding: str  - 编码策略（"cl100k_base" / "rough"）
      属性2: max_input: int  - input 上限
      属性3: max_output: int  - output 上限

    [方法列表]
      方法1: count_input(text) -> int  - 估算 input token
      方法2: count_output(text) -> int  - 估算 output token
      方法3: truncate(text, max_tokens) -> str  - 截断到 max_tokens

    [状态机]  N/A

    [异常处理]  N/A（纯函数）

    [并发安全]  是
    [来源标注]  [DD-001:MD-006 类6] [DD-001:IC-013]
    """

    encoding: str
    max_input: int
    max_output: int

    def __init__(
        self,
        encoding: str = "rough",
        max_input: int = MAX_INPUT_TOKENS,
        max_output: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        """
        [函数名]  __init__
        [职责]  初始化计数策略
        [参数说明]
          参数1: encoding      str  可选  "rough"  描述: 计数策略（rough/tiktoken）
          参数2: max_input     int  可选  20000    描述: input 上限
          参数3: max_output    int  可选  4000     描述: output 上限
        [并发安全]  否
        [来源标注]  [DD-001:MD-006 类6]
        """
        # 业务代码占位
        ...

    def count_input(self, text: str) -> int:
        """
        [函数名]  count_input
        [职责]  估算 input token 数
        [参数说明]
          参数1: text    str  必填  无默认  描述: 文本
        [返回值]
          类型:  int
          描述:  估算的 token 数
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 5ms（rough 策略）
        [来源标注]  [DD-001:MD-006 类6 方法1]
        """
        # 业务代码占位：rough 策略 len(text) // 4
        ...

    def count_output(self, text: str) -> int:
        """
        [函数名]  count_output
        [职责]  估算 output token 数
        [参数说明]
          参数1: text    str  必填  无默认  描述: 文本
        [返回值]
          类型:  int
          描述:  估算的 token 数
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 5ms
        [来源标注]  [DD-001:MD-006 类6 方法2]
        """
        # 业务代码占位
        ...

    def truncate(self, text: str, max_tokens: int) -> str:
        """
        [函数名]  truncate
        [职责]  截断文本到 max_tokens
        [参数说明]
          参数1: text          str  必填  无默认  描述: 原始文本
          参数2: max_tokens    int  必填  无默认  描述: 上限
        [返回值]
          类型:  str
          描述:  截断后的文本（必要时追加截断标记）
        [并发安全]  是
        [幂等性]  是
        [性能约束]  < 10ms
        [来源标注]  [DD-001:MD-006 类6 方法3] [DD-001:函数签名 truncate_to_20k]
        """
        # 业务代码占位
        ...


# =============================================================================
# 模块级顶层函数（from MD-006 函数签名）
# =============================================================================

async def summarize(
    transcript: "Transcript",  # type: ignore[name-defined]  # noqa: F821
    style: str = DEFAULT_SUMMARY_STYLE,
    video_meta: Optional["VideoMeta"] = None,  # type: ignore[name-defined]  # noqa: F821
) -> "LLMSummary":  # type: ignore[name-defined]  # noqa: F821
    """
    [函数名]  summarize
    [职责]  M-006 主入口：转写稿 → LLM 总结（责任链 + 校验 + 章节降级）
    [关联接口契约]  IC-013
    [关联设计规范]  MD-006 函数签名1
    [参数说明]
      参数1: transcript    Transcript   必填  无默认            描述: 转写稿
      参数2: style         str         可选  DEFAULT_SUMMARY_STYLE  描述: 总结风格
      参数3: video_meta    VideoMeta?  可选  None               描述: 视频元数据
    [返回值]
      类型:  LLMSummary
      描述:  LLM 总结（含 model_used / input_tokens / output_tokens / chapters）
    [错误码]
      错误码1: E_LLM_001                 含义: 双模型均失败  触发条件: 责任链终端
      错误码2: E_LLM_002_CHAPTERS_FALLBACK 含义: 章节降级  触发条件: LLM 未返回 video_chapters
    [前置条件]  transcript.segments 非空
    [后置条件]  summary.front_matter 所有键以 video_ 开头
    [并发安全]  是（经 M-012 Semaphore(3)）
    [幂等性]
      是否幂等: 否
      幂等键来源: N/A
      幂等有效期: N/A
      重复请求处理: 重新调用
    [性能约束]  < 5s（P50, 20k input）
    [示例]
      ```
      summary = await summarize(transcript, style="academic")
      ```
    [来源标注]  [DD-001:IC-013] [DD-001:MD-006 函数签名1] [DD-001:DP-006]
    """
    # 业务代码占位：
    #   1. PromptBuilder().build_summary_prompt
    #   2. 责任链调用：DeepseekClient → QwenClient fallback
    #   3. FrontMatterValidator().validate
    #   4. 章节降级（E_LLM_002_CHAPTERS_FALLBACK）
    #   5. 构造 LLMSummary 返回
    ...


async def call_deepseek(prompt: str) -> str:
    """
    [函数名]  call_deepseek
    [职责]  Deepseek 单次调用包装（含 5xx 3 次重试）
    [关联接口契约]  IC-013
    [关联设计规范]  MD-006 函数签名2
    [参数说明]
      参数1: prompt    str  必填  无默认  描述: 完整 prompt（含 system）
    [返回值]
      类型:  str
      描述:  Deepseek 原始输出
    [错误码]
      错误码1: E_LLM_001  含义: 5xx 3 次后放弃  触发条件: 触发 fallback
    [前置条件]  DEEPSEEK_API_KEY 环境变量存在
    [后置条件]  -
    [并发安全]  是
    [幂等性]  否
    [性能约束]  < 5s
    [来源标注]  [DD-001:MD-006 函数签名2] [DD-001:TS-007]
    """
    # 业务代码占位：构造 DeepseekClient → summarize
    ...


async def call_qwen(prompt: str) -> str:
    """
    [函数名]  call_qwen
    [职责]  Qwen fallback 单次调用包装
    [关联接口契约]  IC-013
    [关联设计规范]  MD-006 函数签名3
    [参数说明]
      参数1: prompt    str  必填  无默认  描述: 完整 prompt
    [返回值]
      类型:  str
      描述:  Qwen 原始输出
    [错误码]
      错误码1: E_LLM_001  含义: 5xx 2 次后放弃  触发条件: 链终端
    [前置条件]  QWEN_API_KEY 环境变量存在
    [并发安全]  是
    [幂等性]  否
    [性能约束]  < 8s
    [来源标注]  [DD-001:MD-006 函数签名3] [DD-001:TS-008]
    """
    # 业务代码占位
    ...


def validate_front_matter(front_matter: dict[str, Any]) -> dict[str, Any]:
    """
    [函数名]  validate_front_matter
    [职责]  顶层校验入口：剔除非法键并返回 validated dict
    [关联接口契约]  IC-014
    [关联设计规范]  MD-006 函数签名4
    [参数说明]
      参数1: front_matter    dict  必填  无默认  描述: LLM 输出的字典
    [返回值]
      类型:  dict
      描述:  仅含合法 video_ 前缀键的字典
    [错误码]
      错误码1: E_LLM_001  含义: 校验失败 + LLM 重试 1 次 + 仍失败  触发条件: invalid_keys 非空且重试失败
    [前置条件]  front_matter 为 dict
    [后置条件]  返回 dict 所有键以 video_ 开头
    [并发安全]  是
    [幂等性]  是
    [性能约束]  < 10ms
    [来源标注]  [DD-001:IC-014] [DD-001:MD-006 函数签名4]
    """
    # 业务代码占位：构造 FrontMatterValidator → validate → 取 validated
    ...


async def rag_query(
    query: str,
    context: Union["Transcript", "LLMSummary"],  # type: ignore[name-defined]  # noqa: F821
) -> str:
    """
    [函数名]  rag_query
    [职责]  RAG 问答入口（顶层）
    [关联接口契约]  IC-015 / IC-004
    [关联设计规范]  MD-006 函数签名5
    [参数说明]
      参数1: query      str                       必填  无默认  描述: 用户问题（1-500 字符）
      参数2: context    Transcript | LLMSummary   必填  无默认  描述: 上下文
    [返回值]
      类型:  str
      描述:  答案
    [错误码]
      错误码1: E_LLM_001  含义: 双模型均失败  触发条件: 责任链终端
    [前置条件]  query 非空；context 非空
    [后置条件]  返回值长度 ≥ 1
    [并发安全]  否
    [幂等性]  否
    [性能约束]  < 10s
    [来源标注]  [DD-001:IC-015] [DD-001:IC-004] [DD-001:MD-006 函数签名5]
    """
    # 业务代码占位：构造 RAGQuery → query
    ...


def truncate_to_20k(text: str) -> str:
    """
    [函数名]  truncate_to_20k
    [职责]  顶层截断入口：截断到 20k tokens
    [关联接口契约]  IC-013
    [关联设计规范]  MD-006 函数签名6
    [参数说明]
      参数1: text    str  必填  无默认  描述: 原始文本
    [返回值]
      类型:  str
      描述:  截断到 ≤ 20k tokens 的文本
    [并发安全]  是
    [幂等性]  是
    [性能约束]  < 10ms
    [来源标注]  [DD-001:MD-006 函数签名6] [DD-001:DP-006]
    """
    # 业务代码占位：构造 TokenCounter → truncate
    ...


# =============================================================================
# 模块导出（__all__）
# =============================================================================
__all__ = [
    # 类
    "DeepseekClient",
    "QwenClient",
    "PromptBuilder",
    "FrontMatterValidator",
    "RAGQuery",
    "TokenCounter",
    # 顶层函数
    "summarize",
    "call_deepseek",
    "call_qwen",
    "validate_front_matter",
    "rag_query",
    "truncate_to_20k",
    # 常量
    "DEEPSEEK_MODEL",
    "QWEN_MODEL",
    "MAX_INPUT_TOKENS",
    "MAX_OUTPUT_TOKENS",
    "E_LLM_001",
    "E_LLM_002_CHAPTERS_FALLBACK",
]


# =============================================================================
# 骨架自检注释（DD-M 留痕，下游开发工程师可删除）
# =============================================================================
# [DD-M推断:基于 DD-001 MD-006 状态机 + IC-013 时序图 + DP-006 设计模式说明]
# 关键设计决策：
#   1. 适配器模式：DeepseekClient 与 QwenClient 共享 LLMClient 协议（summarize async）
#   2. 责任链模式：summarize() 函数内串联 [Deepseek → Qwen] fallback
#   3. Validator 模式：FrontMatterValidator.validate 失败时通过 retry_with_feedback 回灌
#   4. 3 段式 Prompt：system / user（含 transcript + style）/ assistant anchor
#   5. 模块级单例：建议在 datatypes/__init__.py 中提供 get_llm_client() 工厂
#
# 跨模块调用：
#   - register_error(E_LLM_001, exc, task_id) 来自 M-010
#   - emit_log(level="INFO", module="M-006", ...) 来自 M-011
#   - DE-005 Transcript / DE-006 LLMSummary 来自 datatypes
#
# [来源标注]  [DD-001:MD-006 状态机] [DD-001:IC-013 时序图] [DD-001:DP-006 责任链] [DD-001:SR-002]
