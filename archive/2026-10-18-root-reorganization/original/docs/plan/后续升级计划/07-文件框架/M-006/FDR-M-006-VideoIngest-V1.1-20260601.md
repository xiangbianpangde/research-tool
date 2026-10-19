# 框架决策记录（FDR）— M-006 LLM 客户端（DD-M-006）

> **生成方**：DD-M-006
> **日期**：2026-06-01
> **决策数**：6（FDR-001 ~ FDR-006）

---

## FDR-001  单文件 vs 多文件拆分

```
[决策编号]  FDR-001
[决策标题]  M-006 采用单文件 llm_client.py 而非按子模块拆 6 文件
[决策状态]  已接受
[决策内容]  将 6 个类（DeepseekClient/QwenClient/PromptBuilder/FrontMatterValidator/RAGQuery/TokenCounter）全部放入 research_tool/llm_client.py，遵循 [DD-001:FS-006] 规定。
[决策理由]
  1. DD-001 FS-006 明确指定单文件路径 research_tool/llm_client.py
  2. 单文件函数总数约 28（含方法），在 soul 4.2 单文件函数数 ≤ 20 的边界上方
     → [DD-M推断] 接受略超上限以保持与上游 FS 对齐；若下游反馈文件过大再拆
  3. 6 类职责高度耦合（DeepseekClient 与 QwenClient 共享协议，RAGQuery 组合 4 个类）
     → 拆分为 6 文件会导致大量跨文件 import，反而降低可读性
  4. M-006 复杂度为"中等"（6 类 6 函数），与单文件策略匹配

[拒绝的替代方案]
  方案B: 拆分为 deepseek_client.py / qwen_client.py / prompt_builder.py / front_matter_validator.py / rag_query.py / token_counter.py + 入口 llm_client.py
  拒绝理由: 与 [DD-001:FS-006] 冲突；过度拆分增加 import 复杂度（评估维度得分 8.0 vs 9.2）

[影响范围]  research_tool/llm_client.py（含 6 类 + 6 顶层函数 + 14 常量）
[相关FDR]  FDR-002 / FDR-003
[来源标注]  [DD-001:FS-006 单文件规范] [DD-M推断:基于 soul 4.11 多方案对比]
```

## FDR-002  适配器模式 + 责任链模式

```
[决策编号]  FDR-002
[决策标题]  适配器模式（DeepseekClient/QwenClient 共享协议）+ 责任链模式（主→备 fallback）
[决策状态]  已接受
[决策内容]  DeepseekClient 与 QwenClient 实现相同的 LLMClient 协议（async def summarize(prompt, system) -> str）；summarize() 顶层函数内部串联两个客户端实现责任链 fallback。
[决策理由]
  1. [DD-001:DP-006] 明确指定两种模式
  2. 适配器模式的好处：新增模型（如 GPT-4）只需实现同一协议，零侵入
  3. 责任链模式的好处：fallback 逻辑与正常调用解耦
  4. 与 [DD-001:IC-013 时序图] 完全对齐：Deepseek 5xx 3 times → Qwen

[拒绝的替代方案]
  方案B: 用 if/else 分支判断（简单但不易扩展）
  拒绝理由: 不符合 DP-006 设计模式要求；扩展性差

[影响范围]  6 个类 + 6 个顶层函数的协作关系
[相关FDR]  FDR-001
[来源标注]  [DD-001:DP-006] [DD-001:MD-006 状态机]
```

## FDR-003  Token 计数策略（rough vs tiktoken）

```
[决策编号]  FDR-003
[决策标题]  默认采用 rough 估算（1 token ≈ 4 字符），可切换 tiktoken
[决策状态]  已接受
[决策内容]  TokenCounter.encoding 默认 "rough"，但保留切换到 "tiktoken" 的接口。
[决策理由]
  1. V1.1 性能约束 [DD-001:PC-004] 5s 响应，rough 估算 < 1ms
  2. rough 精度误差约 ±10%，对 20k 截断的边界判定可接受
  3. tiktoken 是第三方依赖，V1.1 暂不引入
  4. 通过 Strategy 模式保留升级路径

[拒绝的替代方案]
  方案B: 默认 tiktoken
  拒绝理由: 增加依赖；性能开销约 50ms（与 PC-004 冲突）

[影响范围]  TokenCounter 类；summarize() / rag_query() 的截断行为
[相关FDR]  -
[来源标注]  [DD-M推断:基于 DD-001 PC-004 性能约束] [DD-001:CS-001 依赖最小化]
```

## FDR-004  front_matter 校验失败重试策略

```
[决策编号]  FDR-004
[决策标题]  校验失败时重试 LLM 1 次，仍失败时丢弃非法键不阻塞主链
[决策状态]  已接受
[决策内容]  FrontMatterValidator.retry_with_feedback 最多重试 1 次；若仍含非法键，剔除非法键返回 validated 字典，主链继续。
[决策理由]
  1. [DD-001:IC-014] 规定校验失败可重试 1 次
  2. 不阻塞主链：[DD-001:SR-002] 要求 partial degradation
  3. 避免无限重试：[DD-001:ADR-005] 防止 LLM 循环

[拒绝的替代方案]
  方案B: 校验失败抛 E_LLM_001
  拒绝理由: 阻塞主链；违反 SR-002 partial degradation 原则

[影响范围]  FrontMatterValidator.retry_with_feedback / validate_front_matter
[相关FDR]  -
[来源标注]  [DD-001:IC-014] [DD-001:SR-002] [DD-001:ADR-005]
```

## FDR-005  Prompt 3 段式拼装（system / user / assistant anchor）

```
[决策编号]  FDR-005
[决策标题]  Prompt 拆为 system + user（3 段：transcript / style / front_matter 锚点）+ assistant anchor
[决策状态]  已接受
[决策内容]  PromptBuilder.build_summary_prompt 返回 (user_prompt, system_prompt) 二元组；user_prompt 内嵌 3 段（数据 + 风格指令 + front_matter 锚点），assistant 锚点提示 LLM 输出格式。
[决策理由]
  1. [DD-001:MD-006 子模块3] 明确 3 段式
  2. front_matter 锚点（---FRONT_MATTER_START--- / ---END---）让 validator 解析更鲁棒
  3. system 分离有助于 temperature 控制（system 较低，user 较高）

[拒绝的替代方案]
  方案B: 单段 prompt（无 system/assistant 分离）
  拒绝理由: 与 MD-006 子模块3 规范不符；解析易出错

[影响范围]  PromptBuilder.build_summary_prompt / build_rag_prompt
[相关FDR]  -
[来源标注]  [DD-001:MD-006 子模块3] [DD-001:IC-013]
```

## FDR-006  httpx 异步客户端模块级单例

```
[决策编号]  FDR-006
[决策标题]  DeepseekClient / QwenClient 内部持有 httpx.AsyncClient，构造时建连池
[决策状态]  已接受
[决策内容]  每个 LLM 客户端类在 __init__ 中创建 httpx.AsyncClient(limits=httpx.Limits(max_connections=10), timeout=30)；通过 close() 显式释放。
[决策理由]
  1. [DD-001:TS-013] 指定 httpx ≥ 0.27，AsyncClient 支持连接池
  2. 模块级单例可避免每次调用建连（节省 100-300ms）
  3. M-012 Semaphore(3) 已限制并发，连接池上限 10 留有余量

[拒绝的替代方案]
  方案B: 每次调用新建 client
  拒绝理由: 性能差（重复 TCP/TLS 握手）

[影响范围]  DeepseekClient / QwenClient 的连接管理
[相关FDR]  -
[来源标注]  [DD-001:TS-013] [DD-M推断:基于 M-012 Semaphore(3) 约束]
```

## 决策汇总

| FDR | 标题 | 状态 | 影响范围 |
|-----|------|------|---------|
| FDR-001 | 单文件 vs 多文件 | 已接受 | llm_client.py |
| FDR-002 | 适配器+责任链 | 已接受 | 6 类协作 |
| FDR-003 | Token 计数策略 | 已接受 | TokenCounter |
| FDR-004 | 校验失败重试 | 已接受 | FrontMatterValidator |
| FDR-005 | 3 段式 Prompt | 已接受 | PromptBuilder |
| FDR-006 | httpx 单例 | 已接受 | 2 个 Client |

---

> **本文件结束**。
