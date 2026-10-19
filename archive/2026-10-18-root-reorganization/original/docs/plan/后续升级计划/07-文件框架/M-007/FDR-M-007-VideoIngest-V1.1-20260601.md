# 框架决策记录（FDR）— M-007 笔记组装器（DD-M-007）

> **生成方**：DD-M-007
> **日期**：2026-06-01
> **负责模块**：M-007
> **决策数**：6 个重大框架决策

---

## FDR-007-001 新增顶层编排器 NotesSchemaOrchestrator

```
[决策编号] FDR-007-001
[决策标题] 新增顶层编排器 NotesSchemaOrchestrator 封装 6 步拼装
[决策状态] 已接受
[决策内容] 在 6 个子模块之上新增 NotesSchemaOrchestrator 类，封装状态机调度逻辑
[决策理由]
  - DD-001:MD-007 定义了 6 个子模块但未明确顶层编排入口
  - 模板方法模式需要 final method 持有骨架
  - 6 步状态机（INIT → PARSED → META_INJECTED → ... → MARKDOWN_READY）需统一调度
  - 便于依赖注入测试（构造器接受 6 个子模块作为可选参数）
[拒绝的替代方案]
  方案 A: 6 个模块级函数直接串行调用 → 拒绝理由：缺少统一入口，状态机分散
  方案 B: 在 cli.py (M-001) 中直接调用 → 拒绝理由：违反单一职责原则
[影响范围]
  - research_tool/notes_schema.py（新增 NotesSchemaOrchestrator 类）
  - research_tool/tests/test_notes_schema.py（新增 TestNotesSchemaOrchestrator 测试类）
[相关FDR] FDR-007-002
[来源标注] [DD-M推断:依据 = DD-001:MD-007 §设计模式 管道-过滤器 + 模板方法]
```

## FDR-007-002 钩子方法 _build_chapter_fallback 命名约定

```
[决策编号] FDR-007-002
[决策标题] 钩子方法命名为 _build_chapter_fallback 并显式声明 EP-002
[决策状态] 已接受
[决策内容] 将章节降级钩子命名为 _build_chapter_fallback（带 _ 前缀），在类注释中显式标注 "EP-002 扩展点"
[决策理由]
  - DD-001:IC-021 + DD-001:ADR-008 EP-002 是 V1.2 扩展点
  - V1.1 固定 5min 等距切片；V1.2 可替换为聚类/语义切片
  - 钩子命名带 _ 前缀表示子类可覆盖
  - 显式标注 EP-002 避免后续重构遗忘
[拒绝的替代方案]
  方案 A: 直接暴露 public method _build_chapter_fallback → 拒绝理由：与状态机状态不匹配
  方案 B: 使用 abc.ABCMeta abstract method → 拒绝理由：V1.1 固定实现，V1.2 才有子类
[影响范围]
  - research_tool/notes_schema.py（NotesSchemaOrchestrator._build_chapter_fallback 方法）
  - soul §4.6 钩子方法命名约定
[相关FDR] FDR-007-001
[来源标注] [DD-001:IC-021] [DD-001:ADR-008 EP-002] [DD-M推断:依据 = soul §4.5 钩子方法命名约定]
```

## FDR-007-003 错误码命名空间隔离（E_NS_xxx vs E_LLM_xxx）

```
[决策编号] FDR-007-003
[决策标题] M-007 错误码使用 E_NS_ 前缀避免与上游冲突
[决策状态] 已接受
[决策内容] M-007 自身错误码使用 E_NS_001_YAML_PARSE_FAIL / E_NS_002_SCREENSHOT_MISSING；保留 E_LLM_002_CHAPTERS_FALLBACK 来自上游契约
[决策理由]
  - DD-001:E_LLM_002_CHAPTERS_FALLBACK 来自 IC-016 必须保留
  - YAML 解析失败 + 截图缺失是 M-007 内部错误，需独立命名空间
  - E_NS_ 前缀（Notes Schema）避免与 M-001~M-012 其他错误码冲突
[拒绝的替代方案]
  方案 A: 全部使用 E_LLM_ 前缀 → 拒绝理由：错误归属不清
  方案 B: 全部使用 E_M007_ 前缀 → 拒绝理由：与现有命名规范 E_<CATEGORY>_<NUMBER> 不一致
[影响范围]
  - research_tool/notes_schema.py（E_NS_001 / E_NS_002 常量）
  - research_tool/tests/test_notes_schema.py（错误码相关测试）
[相关FDR] -
[来源标注] [DD-001:E_LLM_002_CHAPTERS_FALLBACK] [DD-M推断:依据 = DD-001:CS-001 §错误码常量 E_<CATEGORY>_<NUMBER>_<DETAIL>]
```

## FDR-007-004 依赖方向约束（M-007 不导入 M-005/006/009）

```
[决策编号] FDR-007-004
[决策标题] M-007 不导入 M-005/006/009，仅通过参数注入数据
[决策状态] 已接受
[决策内容] notes_schema.py 仅依赖 datatypes.py + error_handler.py + structured_logger.py，不导入 M-005（transcriber）/ M-006（llm_client）/ M-009（ffmpeg_wrapper）
[决策理由]
  - 避免反向依赖：M-007 是 M-005/006/009 的下游消费者
  - 数据通过参数注入：Transcript / LLMSummary / ScreenshotFrame
  - 便于单元测试：Mock 3 个数据对象即可
  - DAG 检测通过，无循环依赖
[拒绝的替代方案]
  方案 A: 直接调用 M-005.transcribe() / M-006.summarize() / M-009.capture_screenshots() → 拒绝理由：反向依赖
  方案 B: 通过 callback 注入 → 拒绝理由：增加复杂度，无明显收益
[影响范围]
  - research_tool/notes_schema.py（import 列表）
  - DD-001:FS-007 §文件依赖关系图
[相关FDR] -
[来源标注] [DD-001:FS-007 §文件依赖关系图 notes_schema.py 行] [soul §五 R26 禁止循环依赖]
```

## FDR-007-005 6 步拼装的并行 vs 串行选择

```
[决策编号] FDR-007-005
[决策标题] 6 步拼装采用串行（依赖关系决定）
[决策状态] 已接受
[决策内容] 6 步拼装采用串行执行（INIT → PARSED → ... → MARKDOWN_READY）
[决策理由]
  - 数据依赖：每步输入依赖上一步输出（front_matter → 注入 → 章节 → 截图 → 参考 → 总装）
  - 串行符合管道-过滤器模式语义
  - 总耗时 < 200ms（DD-001:IC-020 性能约束），并行优化收益有限
  - 状态机明确 7 个状态转移，串行易于追踪
[拒绝的替代方案]
  方案 A: 章节降级与截图嵌入并行 → 拒绝理由：两者都依赖 META_INJECTED 结果，存在数据竞争
  方案 B: 参考来源与 Markdown 总装并行 → 拒绝理由：总装需要参考来源作为输入
[影响范围]
  - NotesSchemaOrchestrator.assemble_markdown() 实现顺序
  - 性能测试用例（test_orchestrator_state_progression）
[相关FDR] -
[来源标注] [DD-001:IC-020 性能约束 < 200ms] [DD-001:MD-007 §状态机]
```

## FDR-007-006 测试用例数 = 16（核心 8 + 边界 4 + 异常 4）

```
[决策编号] FDR-007-006
[决策标题] 测试用例数严格按 DD-001:MD-007 §测试策略 = 16
[决策状态] 已接受
[决策内容] 单元测试用例数 = 16（核心 8 + 边界 4 + 异常 4）
[决策理由]
  - DD-001:MD-007 §测试策略明确指定 16 个用例
  - 覆盖率目标：行 ≥ 85% / 分支 ≥ 75%
  - 6 个子模块各 2-3 用例 + 顶层编排 4 个端到端用例
[拒绝的替代方案]
  方案 A: 简化为 8 个用例 → 拒绝理由：未达 6 子模块覆盖要求
  方案 B: 扩展到 24 个用例 → 拒绝理由：超出 DD-001 规范，增加维护成本
[影响范围]
  - research_tool/tests/test_notes_schema.py（16 个 test_* 函数）
[相关FDR] -
[来源标注] [DD-001:MD-007 §测试策略 测试用例数 核心 8 + 边界 4 + 异常 4 = 16]
```

---

## FDR 状态汇总

| FDR 编号 | 状态 | 类别 |
|---------|------|------|
| FDR-007-001 | 已接受 | 架构 |
| FDR-007-002 | 已接受 | 命名约定 |
| FDR-007-003 | 已接受 | 错误码 |
| FDR-007-004 | 已接受 | 依赖关系 |
| FDR-007-005 | 已接受 | 并发模型 |
| FDR-007-006 | 已接受 | 测试策略 |

**6 个 FDR 全部已接受，状态正常**。

[来源标注] [soul §3.7 框架决策记录模板] [soul §4.13 FDR 机制]

---

> **本文件结束**。M-007 框架决策记录 6 条 100% 覆盖。
