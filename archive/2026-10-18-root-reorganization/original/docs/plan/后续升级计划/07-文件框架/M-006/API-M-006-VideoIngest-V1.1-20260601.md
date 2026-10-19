# 接口注释清单 — M-006 LLM 客户端（DD-M-006）

> **生成方**：DD-M-006
> **日期**：2026-06-01
> **负责模块**：M-006
> **契约来源**：DD-001 IC-013 / IC-014 / IC-015

---

## API-M006-001  LLM 总结（IC-013）

```
[接口编号]  API-M006-001
[关联契约]  IC-013（来自 DD-001）
[实现文件]  research_tool/llm_client.py → summarize() / DeepseekClient.summarize() / QwenClient.summarize()
[函数签名注释]
  async def summarize(
      transcript: Transcript,        # [转写稿，含 segments]
      style: str = "academic",       # [风格：academic/casual/tutorial]
      video_meta: Optional[VideoMeta] = None,  # [视频元数据，可选]
  ) -> LLMSummary:                   # [LLM 总结，含 model_used/tokens/chapters]

[参数说明]
  参数1: transcript    Transcript   必填  无默认  描述: 转写稿  校验: segments 非空
  参数2: style         str         可选  "academic"  描述: 风格  校验: in {"academic","casual","tutorial"}
  参数3: video_meta    VideoMeta?  可选  None  描述: 视频元数据

[返回值说明]
  类型:  LLMSummary
  描述:  LLM 总结（含 front_matter / body / chapters / input_tokens / output_tokens / model）
  字段约束:  front_matter 键以 video_ 开头

[错误码说明]
  E_LLM_001: 双模型均失败 → 错误码登记 + 中间产物保留
  E_LLM_002_CHAPTERS_FALLBACK: 章节降级（5min 等距切片）

[调用时序]
  CLI → M-006.summarize
    ├─→ PromptBuilder.build_summary_prompt
    ├─→ DeepseekClient.summarize (5xx 3 times)
    ├─→ QwenClient.summarize (fallback, 5xx 2 times)
    ├─→ FrontMatterValidator.validate (重试 1 次)
    ├─→ 章节降级 (if video_chapters 缺失)
    └─→ LLMSummary (return)

[性能约束]  1-5s / 20k input 截断 / 4k output 截断
[并发安全]  是（经 M-012 Semaphore(3)）
[幂等性]  否（LLM 随机性）

[来源标注]  [DD-001:IC-013] [DD-001:MD-006 函数签名1] [DD-001:DP-006]
```

## API-M006-002  字段名前缀校验（IC-014）

```
[接口编号]  API-M006-002
[关联契约]  IC-014（来自 DD-001）
[实现文件]  research_tool/llm_client.py → validate_front_matter() / FrontMatterValidator.validate()
[函数签名注释]
  def validate_front_matter(front_matter: dict) -> dict:
      """
      校验 front_matter 字段名是否以 video_ 开头

      Args:
          front_matter: LLM 输出的 YAML 头部字典

      Returns:
          仅含合法 video_ 前缀键的字典

      Raises:
          无（异常降级为返回 + invalid_keys 列表）
      """

[参数说明]
  参数1: front_matter    dict  必填  无默认  描述: LLM 输出字典  校验: type(dict)

[返回值说明]
  类型:  dict
  描述:  仅含合法键的字典
  特殊值:  若所有键非法，返回 {}

[错误码说明]
  E_LLM_001: 校验失败 + LLM 重试 1 次 + 仍失败 → 保留非法键（不阻塞主链）

[调用时序]
  M-006.summarize → validate_front_matter → (若 invalid) → retry_with_feedback → Deepseek.summarize

[性能约束]  < 10ms
[并发安全]  是
[幂等性]  是

[来源标注]  [DD-001:IC-014] [DD-001:MD-006 子模块4] [DD-001:SR-002] [DD-001:ADR-005]
```

## API-M006-003  RAG 问答（IC-015 / IC-004）

```
[接口编号]  API-M006-003
[关联契约]  IC-015（DD-001）/ IC-004（DD-001）
[实现文件]  research_tool/llm_client.py → rag_query() / RAGQuery.query()
[函数签名注释]
  async def rag_query(
      query: str,                              # [用户问题]
      context: Transcript | LLMSummary,        # [上下文]
  ) -> str:                                    # [LLM 答案]

[参数说明]
  参数1: query      str                       必填  无默认  描述: 用户问题  校验: 1 <= len(query) <= 500
  参数2: context    Transcript | LLMSummary   必填  无默认  描述: 上下文

[返回值说明]
  类型:  str
  描述:  LLM 生成的答案
  特殊值:  不返回空串（5xx 后切 fallback）

[错误码说明]
  E_LLM_001: 双模型均失败

[调用时序]
  CLI --query → M-001.rag_query → M-006.rag_query
    ├─→ PromptBuilder.build_rag_prompt
    ├─→ DeepseekClient.summarize
    └─→ QwenClient.summarize (fallback)

[性能约束]  < 10s
[并发安全]  否（单次调用入口）
[幂等性]  否

[来源标注]  [DD-001:IC-015] [DD-001:IC-004] [DD-001:MD-006 子模块5]
```

## API-M006-004  Deepseek 单次调用（IC-013 内部）

```
[接口编号]  API-M006-004
[关联契约]  IC-013（DD-001）
[实现文件]  research_tool/llm_client.py → call_deepseek() / DeepseekClient.summarize() / _post_chat()
[函数签名注释]
  async def call_deepseek(prompt: str) -> str:

[参数说明]
  参数1: prompt    str  必填  无默认  描述: 完整 prompt

[返回值说明]
  类型:  str
  描述:  Deepseek 原始输出

[错误码说明]
  E_LLM_001: 5xx 3 次后放弃（链上层捕获切 Qwen）

[性能约束]  < 5s
[并发安全]  是
[幂等性]  否

[来源标注]  [DD-001:IC-013] [DD-001:MD-006 函数签名2] [DD-001:TS-007]
```

## API-M006-005  Qwen fallback 单次调用（IC-013 内部）

```
[接口编号]  API-M006-005
[关联契约]  IC-013（DD-001）
[实现文件]  research_tool/llm_client.py → call_qwen() / QwenClient.summarize() / _post_chat()
[函数签名注释]
  async def call_qwen(prompt: str) -> str:

[参数说明]
  参数1: prompt    str  必填  无默认  描述: 完整 prompt

[返回值说明]
  类型:  str
  描述:  Qwen 原始输出

[错误码说明]
  E_LLM_001: 5xx 2 次后放弃（链终端，由 summarize 登记）

[性能约束]  < 8s
[并发安全]  是
[幂等性]  否

[来源标注]  [DD-001:IC-013] [DD-001:MD-006 函数签名3] [DD-001:TS-008]
```

## API-M006-006  文本截断到 20k（IC-013 内部）

```
[接口编号]  API-M006-006
[关联契约]  IC-013（DD-001，PC-004 性能约束）
[实现文件]  research_tool/llm_client.py → truncate_to_20k() / TokenCounter.truncate()
[函数签名注释]
  def truncate_to_20k(text: str) -> str:

[参数说明]
  参数1: text    str  必填  无默认  描述: 原始文本

[返回值说明]
  类型:  str
  描述:  截断到 ≤ 20k tokens 的文本
  特殊值:  必要时追加 "...[truncated]" 标记

[性能约束]  < 10ms
[并发安全]  是
[幂等性]  是

[来源标注]  [DD-001:MD-006 函数签名6] [DD-001:DP-006]
```

## 覆盖汇总

| IC | API 编号 | 函数路径 | 注释完整度 | 状态 |
|----|---------|---------|----------|------|
| IC-013 | API-M006-001/004/005/006 | summarize / call_deepseek / call_qwen / truncate_to_20k | 100% | ✓ |
| IC-014 | API-M006-002 | validate_front_matter | 100% | ✓ |
| IC-015 / IC-004 | API-M006-003 | rag_query | 100% | ✓ |

**6/6 API 注释完整，覆盖 3 个 IC 契约的 100%。**

---

> **本文件结束**。M-006 接口注释清单就绪，交付 DD-S。
