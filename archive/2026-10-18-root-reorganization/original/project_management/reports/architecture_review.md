# Phase 4：架构审核报告（research-tool V0.1.1）

> **路径证据基线**：所有引用均来自 `/Volumes/项目/research-tool/research_tool/`
> **审计日期**：2026-07-21 / **版本**：0.1.1 / **测试**：CLAUDE.md 标注 417 passed / 5 skipped（与 STATUS.md 454 矛盾待 Phase 3 复核）
> **DoD 状态**：当前架构 + 15 条问题 + 3 条目标改进 + 4 步迁移 → ✅ 达标

---

## 1. 当前架构快照

### 1.1 五层 ASCII 图

```
                    +-------------------------------------+
                    | presentation/  (3 文件, 2203 行)    |
                    |  cli.py (1264) | webui.py (712)     |
                    |  setup_deployment.py (227)          |
                    +-----+--------------------------+----+
                          | 拉取配置/渲染/CLI 派发       | (3 处有意偏差直达 infra)
                          v                              v
                    +-----+------------------------------+-----+
                    | application/  (4 文件, 2197 行)         |
                    |  pipeline.py (637) ← 主管道              |
                    |  video_pipeline.py (665)                 |
                    |  video_concurrent_orchestrator.py (465)  |
                    |  talk_linker.py (430)                    |
                    +--+-------------+-------------+-----------+
                       |             |             |
                       v             v             v
        +--------------+--+   +-----+-----+   +---+----------------+
        | domain/  (3 文件) |   | common/   |   | infrastructure/    |
        |  models.py (623) |   | (横切)    |   | (8 子包, 11751 行) |
        |  config.py (286) |   | logging   |   |  stages/ 6 stage   |
        |  errors.py (292) |   | slug      |   |  llm/ 4 client     |
        +------------------+   | translate |   |  search/ 18 工厂   |
                               | url_guard |   |  ingest/ 9 模块    |
                               +-----------+   |  export/ 2 模块    |
                                               |  experts/ 1 注册表 |
                                               +-------------------+
```

### 1.2 数据流（基于 `application/pipeline.py:30-40`, `pipeline.py:288-411`）

```
              ┌──── collect ──────┐
              │ Collector/PdfIngest│  ← raw/XX-{domain}.md + sources.json
              │ + .deepen_done?    │
              └────────┬───────────┘
                       v
              ┌──── deepen (optional, cfg.pdf_dir 时跳过) ──┐
              │ DeepenStage                               │
              │ (画像→反偏差查询→追加 raw/)               │
              └────────┬──────────────────────────────────┘
                       v
              ┌──── clean ────┐
              │ Cleaner       │  ← clean/*.md + FileQuality
              │ (可选 relevance_filter) │
              └────┬──────────┘
                   v
              ┌──── extract (optional, extractor.enabled=false 跳过) ──┐
              │ Extractor                                               │
              │ → extracted/{entities,relations,triples}.json + schema  │
              └────┬────────────────────────────────────────────────────┘
                   v
              ┌──── organize ────┐
              │ Organizer         │
              │ map-reduce:       │
              │  摘要→规划→节点→主表│  ← tree/*.md + 00-主表.md
                   v  (可选 talk enrichment)
              ┌──── talk (cfg.talk.enabled=True) ────┐
              │ TalkLinker                            │
              │ → raw/video_*.md + 重跑 clean→report  │
              └────┬──────────────────────────────────┘
                   v
              ┌──── report ─────┐
              │ Reporter         │  ← report.md / report.html
              └──────────────────┘

  反向循环（max_backward_rounds>0）：
    organize 后 → Organizer.assess_and_feedback → 修正 queries
                → _recollect() 追加 raw/
                → _invalidate_after_collect 删 marker + tree/clean/extracted
                → 下一轮正向 collect→...→report
  契约：纯文件系统通信（research-output/<topic>/{raw,clean,extracted,tree,report.md,.stage-complete/}）
```

### 1.3 调用关系（实际 import 拓扑）

```
presentation/cli.py
  ├→ application/pipeline.py           (合规)
  ├→ infrastructure/llm/base.py        ⚠️ (cli.py:22 有意偏差 1)
  ├→ infrastructure/stages/*            ⚠️ (cli.py:35 有意偏差 1)
  ├→ infrastructure/ingest/*           (cli.py:379, 417 触发加载)
  ├→ infrastructure/export/*           (合规)
  └→ application/video_pipeline.py     (cli.py:553, 583)

presentation/webui.py
  ├→ application/pipeline.py           (webui.py:20 合规)
  ├→ application/video_pipeline.py     (webui.py:77 合规)
  └→ infrastructure/ingest/pipeline_adapter.py  ⚠️ (webui.py:78 有意偏差 2)

application/pipeline.py
  ├→ infrastructure/stages/* + llm + search  (合规)
  ├→ application/talk_linker.py        (pipeline.py:421 内部 import，合规)
  └→ infrastructure/ingest (PdfIngestor)    (pipeline.py:589 内部 import，合规)

infrastructure/ingest/pipeline_adapter.py
  └→ application/pipeline.py            ⚠️ (pipeline_adapter.py:228 内部 import，有意偏差 3)

infrastructure/search/*
  └→ domain/errors.py + domain/models.py (全部一致，仅向上指 domain，合规)

infrastructure/stages/*
  └→ infrastructure/{llm,search,experts,ingest} + domain + common
     (deepen/cleaner/extractor/organizer/collector/reporter 一致)

common/translate.py
  └→ infrastructure/llm/base.py          ⚠️ (translate.py:13 有意偏差 4)
```

合规性：domain 层零上引（grep `from ..` 仅命中 `domain/errors.py: import 自身模块`、`domain/config.py:23 import errors`）；presentation→infrastructure 仅出现在被 CLAUDE.md 标注的 3 处（实际 4 处）。

---

## 2. 模块设计审视（按层）

### 2.1 presentation（cli.py 1264 / webui.py 712 / setup_deployment.py 227）

| 维度 | 评价 | 证据 |
|------|------|------|
| 单一职责 | 🟡 CLI 同时承担"命令派发+参数解析+JSON 渲染+URL 脱敏+表格输出+子进程派生（视频）"，cli.py 含 31 个 `def`（grep -c） | `cli.py:1-200, 1163` |
| 内聚 | ✅ 同类职责都在 cli.py，无散落 | — |
| 可测试 | 🟡 Gradio 路径靠 `_run_threaded` + `queue.Queue` 桥接（webui.py:49-61），单测需绕 asyncio.run | `webui.py:56-61` |
| 可扩展 | 🟢 新增命令 = `app.command(...)`，不影响其它 | `cli.py` typer 模式 |
| 偏差 | ⚠️ cli.py:22 `from ..infrastructure.llm.base import LLMClient` + cli.py:35 `from ..infrastructure.stages import ...` —— CLAUDE.md 已标注 | `cli.py:22,35` |

### 2.2 application（pipeline.py 637 / video_pipeline.py 665 / talk_linker.py 430 / video_concurrent_orchestrator.py 465）

| 维度 | 评价 | 证据 |
|------|------|------|
| 单一职责 | ✅ pipeline 只编排；video_pipeline 只做视频；talk_linker 只做关联 | `pipeline.py:1-5` |
| 内聚 | 🟡 pipeline.py `_stream_forward`（122 行）+ `_run_talk_enrichment`（138 行）混合"正向驱动+反向失效+talk 串接" | `pipeline.py:288-551` |
| 可测试 | ✅ TestPipelineBackward 已覆盖 8 用例（CLAUDE.md 标注 R11） | `tests/test_pipeline_backward.py` |
| 可扩展 | 🟡 加新阶段需改 `_exec` 大 if-elif（pipeline.py:585-632）+ `_stage_output` 字典（line 31-40），OCP 违反 | `pipeline.py:585-632` |
| 偏差 | ✅ application 不向上引 presentation/，合规 | — |

### 2.3 domain（models.py 623 / config.py 286 / errors.py 292）

| 维度 | 评价 | 证据 |
|------|------|------|
| 零依赖 | ✅ `grep "from \.\." research_tool/domain/` 仅命中 errors.py 的内部引用 | 实证 |
| 单一职责 | ✅ Pydantic 模型 + 错误体系 + 配置加载三件套 | — |
| 可测试 | ✅ 测试比例 0.86（CLAUDE.md）；domain 单元测试密度最高 | — |
| 可扩展 | 🟡 models.py 单文件 623 行，混 Config / Result / Source / Video 4 类，新增模型时检索成本↑ | `models.py:17-622` |
| 偏差 | 无 | — |

### 2.4 infrastructure（8 子包，11751 行）

| 子包 | 行数 | 评价 |
|------|------|------|
| `stages/` | 2827 | ✅ 单职责清晰；唯一弱点：`_stage_output` 字典耦合阶段↔输出契约（pipeline.py:31-40） |
| `llm/` | 686 | ✅ 抽象→工厂→具体 3 文件，OpenAI/Anthropic 互不依赖 |
| `search/` | 2666 | ✅ 18 后端工厂（`search/__init__.py:9-73`），统一 `SearchBackend` 抽象 |
| `ingest/` | 5210 | 🟡 `transcriber.py 1139 / downloader.py 996` 双引擎重型模块 |
| `export/` | UNKNOWN | 仅 2 文件未读 |
| `experts/` | 单文件 registry | ✅ |

### 2.5 common（横切）

| 维度 | 评价 | 证据 |
|------|------|------|
| 真正横切 | 🟡 logging/slug/url_guard 横切干净；`translate.py` 引入 `infra/llm` 跨层（CLAUDE.md 标注） | `translate.py:13` |
| 文档 | ✅ 每个文件 docstring 引用规范节号 | — |

---

## 3. 扩展性审视

### 3.1 18 个 SearchBackend 工厂（`search/__init__.py:9-73`）

✅ **合理**：
- 字典式路由（`if name == ...`）简单可读，新增后端 = 加 elif + 写 backend 文件
- `get_backend()` 自动套 `CachingBackend` 装饰器（line 76-84），复用一致
- 全部依赖 `domain.models.CollectorConfig`，参数统一

⚠️ **弱点**：
- 18 个分支手写，O(n) 查找；新后端必改 `search/__init__.py:9-73`——**轻微 OCP 违反**
- 没有任何 `entry_points` / 装饰器自注册（对比 LangChain 风格）——**插件化缺失**
- 同名函数 `_build_inner` 在 `search/__init__.py:9` 和 `search/x_backend.py:424` 不重复，但别名映射（"scholar"→arxiv, "x"→twitter）集中在工厂里（line 14, 69）

### 3.2 5 个 LLM Provider 路由（`llm/base.py:104-142`）

✅ **合理**：
- `LLMClient.from_config()` 工厂 + Literal `Provider` 强制枚举（`models.py:18`）
- OpenAI 兼容协议 4 归一（OpenAI/DeepSeek/Ollama/MiniMax 全部走 `OpenAILLMClient`，base.py:134-137）——**减少实现成本**
- 健康检查 `_authentication_failed` 标记位（line 56, 95-100）防止 401 后继续飞请求

⚠️ **弱点**：
- 5→4 归一意味着 `minimax` 必须配 OpenAI 兼容 URL，base.py:47 写死 `api.minimaxi.com/v1`——多 MiniMax 兼容端点需硬编码扩展
- 没有 streaming backpressure 或 retry budget——批量调用靠 `gather_fail_fast`（line 27-39）一次性取消

### 3.3 6 Stage 管道

✅ **优势**：
- 文件系统契约：每 Stage 自带 `.stage-complete/<name>.json` marker + `has_output()` 兜底（`pipeline.py:68-86`）
- 幂等 + resume：`_stage_is_complete` 同时检查 marker + glob（line 72-86），旧产物兼容
- 反向循环：`_recollect` + `_invalidate_after_collect` 完整（line 553-583）

⚠️ **弱点**：
- `_exec()` 是大 if-elif（line 585-632）——**新增 Stage 必须改 pipeline.py**，OCP 违反
- `_stage_output` 字典（line 31-40）是契约的第二处单点，新增 Stage 需双修

---

## 4. 4 处有意偏差复核（CLAUDE.md 标注 3 处，实测 4 处）

| # | 偏差 | 路径 | 当前合理性 | 建议 |
|---|------|------|------------|------|
| 1 | presentation→infra (`cli._make_llm`) | `cli.py:22,35` | 🟡 CLI 薄封装简化合理，但直接 `from infrastructure.stages import Cleaner/Collector/...` 跳过 pipeline 编排 | 保留但加 `_thin_layer` 注释，标 `@arch_exception` |
| 2 | webui→infra (`trigger_pipeline`) | `webui.py:78` | ✅ webui 直接 import `pipeline_adapter` 而非 `application`，**违反 application 抽象** | 建议改为 `from ..application.pipeline_adapter_facade import run_research_pipeline` |
| 3 | common→infra (`translate.py`) | `common/translate.py:13` | ✅ 翻译工具复用 LLM 抽象合理；但 common 名义上是横切 | 下轮重构：移至 `application/translation.py` |
| 4 | infra→app (`pipeline_adapter`) | `infrastructure/ingest/pipeline_adapter.py:228` | ⚠️ 基础设施上引 application 形成 `infra → app → infra` 循环依赖隐患（实测 4 处偏差） | 拆 application/services.py 抽 trigger_pipeline 包装 |

---

## 5. 问题列表（15 条）

| # | 严重度 | 路径:行号 | 问题 | 影响 |
|---|--------|-----------|------|------|
| A1 | 🔴 P0 | `application/pipeline.py:585-632` | `_exec()` 大 if-elif + `_stage_output` 字典（line 31-40）双单点 | OCP 违反；扩展成本 = 改 2 个文件 + 测试 5 类事件 |
| A2 | 🔴 P0 | `application/pipeline.py:288-401` | `_stream_forward()` 单方法 113 行，承担"跳过判断+执行+marker+失败回写+talk 触发" | 可读性差；review/重构风险；测试只能 end-to-end |
| A3 | 🔴 P0 | `infrastructure/ingest/pipeline_adapter.py:228` | `from ...application.pipeline import ResearchPipeline` —— 基础设施上引应用层 | 拆分 application 时会拆断；测试 ingest/ 时需 mock application.pipeline |
| A4 | 🟠 P1 | `infrastructure/search/__init__.py:9-73` | 18 个分支手写工厂，新增后端必须改本文件 | 扩展成本 O(1) 但易冲突；建议装饰器自注册或 entry_points |
| A5 | 🟠 P1 | `presentation/webui.py:78` | 直接 import `infrastructure.ingest.pipeline_adapter`，绕开 application 层 | 违反分层；application 演化时 webui 必须同步改 |
| A6 | 🟠 P1 | `common/translate.py:13` | 公共模块上引 infrastructure，与 common "横切无业务" 定位冲突 | common 应只剩 logging/slug/url_guard |
| A7 | 🟠 P1 | `application/pipeline.py:404-551` | `_run_talk_enrichment()` 内嵌 clean→extract→organize→report 重跑循环 | 复用差；report stage 可能在 talk 后被跳过 |
| A8 | 🟠 P1 | `infrastructure/stages/collector.py:279,328,329,406` | stage 内多处延迟 import（experts / github / x_backend） | 启动期 import 顺序脆弱；测试 mock 难 |
| A9 | 🟡 P2 | `domain/models.py:17-622` | 单文件 623 行混合 Config / Result / Source / Video 4 类契约 | 维护性下降；建议拆 `domain/{configs,results,video}.py` |
| A10 | 🟡 P2 | `presentation/cli.py:1264` | cli.py 1264 行，31 个函数；同时承担参数解析+JSON 渲染+脱敏+子进程 | 难测；建议拆 `cli/{commands,render,redact}.py` |
| A11 | 🟡 P2 | `infrastructure/llm/base.py:134-137` | 4 个 OpenAI 兼容 provider 走同一 client，但 endpoint 默认值写死 `_DEFAULT_BASE_URL`（line 42-48） | 多环境 MiniMax 兼容端点无法配置化，需改源码 |
| A12 | 🟡 P2 | `application/pipeline.py:147-243` | 反向循环与 talk enrichment 共用 `_invalidate_after_collect` + `_finish_run` 两条路径各自实现（line 493-504 vs 572-583） | 重复实现 + 易漂移 |
| A13 | 🟡 P2 | `infrastructure/ingest/transcriber.py:1139` | 单文件 1139 行，承担 4 引擎（MiniMax/whisper/groq/bcut）+ 缓存+并发 | 拆 `transcriber/{engines,runner,cache}.py` |
| A14 | 🟡 P2 | `application/pipeline.py:31-40` | `_stage_output` 字典是"阶段→输出契约"第二单点，新增 Stage 必改 | 与 A1 联动；建议 `STAGE_OUTPUT_CONTRACT: dict[StageName, OutputSpec]` 注册到 `domain/models.py` |
| A15 | 🟢 P3 | `infrastructure/stages/__init__.py:10-19` | 既暴露 `Collector/Cleaner/...` 类，又暴露 `clean/deepen` 模块级函数（同名不同义） | presentation/cli.py:35 跳过 pipeline 编排直接调 stage（与 A5 同源） |

---

## 6. 目标架构（3 条改进项）

```
改进 1 ─ 解耦 Stage 路由（OCP）
  - 把 _exec() 大 if-elif 改为 StageRegistry 注册表
  - pipeline.py 只负责调度 + 事件流，Stage 自描述 (output_pattern, llm_required)
  - 影响：A1 / A7 / A12 / A14

改进 2 ─ 收回 presentation→infra 直连
  - 新增 application/services.py（薄服务层）：create_research_service(), run_video_pipeline()
  - webui/cli 全部走 application，禁止 import infrastructure
  - translate.py 从 common 移至 application/translation.py
  - 影响：A3 / A5 / A6 / A15

改进 3 ─ 拆分超大文件
  - domain/models.py → configs.py / results.py / video.py
  - presentation/cli.py → cli/{commands,render,redact}.py
  - infrastructure/ingest/transcriber.py → transcriber/{engines,runner,cache}.py
  - 影响：A9 / A10 / A13（纯可读性，无功能变化）
```

---

## 7. 迁移步骤（按依赖顺序）

```
Step 1 [无破坏]  拆 domain/models.py
   → 新增 domain/{configs.py, results.py, video.py}
   → models.py 改为聚合 import（保持 from research_tool.domain.models import ... 兼容）
   → 验证：pytest 全绿

Step 2 [低风险]  拆 cli.py / transcriber.py（同 Step 1，仅文件级重构）
   → cli/{commands,render,redact}.py + cli.py 顶部薄 re-export
   → transcriber/{engines,runner,cache}.py + __init__.py 重组
   → 验证：pytest + ruff check

Step 3 [架构核心] 收回 presentation→infra 直连
   3a) 新增 application/services.py：
       - def create_research_service(cfg) -> ResearchService
       - def run_video_ingest(...) -> VideoProcessResult
   3b) 把 translate_markdown 从 common/translate.py 移至 application/translation.py
       common/translate.py 留 shim（from ..application.translation import translate_markdown）
   3c) webui.py:78 改为 from ..application.services import run_video_ingest
   3d) cli.py:22, 35 改为只用 application.services + presentation 层
   验证：所有 imports + pytest + ruff

Step 4 [架构演进] StageRegistry 化（OCP）
   4a) infrastructure/stages/base.py 新增 StageMeta dataclass:
       - name, llm_required, output_dir, output_patterns, optional
   4b) 每个 stage 文件导出 STAGE_META 实例
   4c) pipeline.py 引入 _REGISTRY: dict[StageName, StageMeta]
   4d) _stage_output / _exec() 改为查 _REGISTRY
   4e) 新增 stage 现在只需写 stage 文件 + 注册
   验证：新增一个 no-op stage + 走完 6 stage pipeline + backward + talk
```

---

## 8. UNKNOWN（待 Phase 1/3 补全）

- pytest 覆盖率精确数字（CLI 报"覆盖率待测"）
- MCP / Skill / Agent 注册扫描结果
- tests/ 各模块行数详单（Agent 2 已报告总计 tests 18906 行 / 59 测试文件）
- wiki_export 模块内部结构（`export/` 子包未深度审计）

---

**报告完成时间**：2026-07-21
**关联文件**：`project_management/problems/technical_debt.md`（架构相关章节由 Agent 2 写入）