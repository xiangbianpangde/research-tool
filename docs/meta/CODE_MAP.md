# research-tool 代码入口索引

本版本面向 macOS，以 CLI 为主要产品接口，供 AI Agent 调用；Python SDK 复用同一九阶段引擎。

## 正式入口

| 入口 | 位置 | 用途 |
|---|---|---|
| research run | research_tool/presentation/cli.py | 主题、已有 Markdown、PDF 和视频输入的完整研究 |
| research status | research_tool/presentation/cli.py | 根据 runtime 与摘要校验当前阶段及成果 |
| research config | research_tool/presentation/cli.py | 查看脱敏 JSON 配置 |
| research wiki-stage | research_tool/infrastructure/export/wiki_stage.py | 校验并打包当前成功运行的 final |
| research setup | research_tool/presentation/setup_deployment.py、scripts/setup_interactive.py | CLI 依赖安装、凭据配置与环境检查 |
| Python SDK | research_tool/__init__.py | research()、ResearchPipeline.run/stream() |

WebUI、research ui 命令及 Gradio 安装项已移除。

## 引擎

| 模块（相对 research_tool/） | 职责 |
|---|---|
| application/pipeline.py | 唯一九阶段调度、恢复、取消、预算和摘要 |
| nine_loop/runtime_state.py | macOS/POSIX fcntl 文件锁、原子写和 CAS |
| nine_loop/evidence.py | 来源正文哈希、偏移及逐字引用核验 |
| nine_loop/knowledge_min.py | 确定性事实/来源/主题证据图 |
| nine_loop/merge_min.py | 有摘要前置条件的无损增量合并 |
| nine_loop/research.py | 检视、缺口解决记录、按优先级与finding类型公平分配补搜查询、质量门控、报告支持核验 |
| infrastructure/stages/collector.py、fetcher.py | 搜索与真实正文抓取 |
| infrastructure/stages/research_strategies.py | Collect 内部的实体拆分、画像与查询策略 |
| infrastructure/stages/cleaner.py、extractor.py | 保留科学比较符/表格边界的清洗、去重、原文片段ID选择式抽取、严格跨度校验与块级诊断 |
| infrastructure/llm/openai_client.py、domain/errors.py | 结构化结束状态/JSON/schema校验与安全LLMFailureDiagnostic |
| infrastructure/retrieval/manifest.py、client.py | 运行检索清单、能力握手、成文前evidence公开CLI与审校采用查询 |
| infrastructure/retrieval/snapshot.py、review.py | 已提交stage/全量raw-clean证据快照与run/report/evidence哈希绑定的独立审校记录 |
| nine_loop/grounding.py、grounded_synthesis.py | 全文依赖精确入账、中文原子论断数值/单位/效应硬检查、全部来源正反证核验及逐项审计 |
| infrastructure/ingest/、application/video_pipeline.py | PDF/视频输入适配 |
| domain/models.py、config.py | 固定九段配置与字段校验 |

正式阶段：collect、clean、extract、knowledge、inspect、targeted、merge、qgate、report。初次知识构建后先 inspect/qgate；CONTINUE 才 targeted/merge 并重检；默认strict仅满足原缺口阈值才报告。显式reporter.evidence_policy=layered在最后补搜轮后可生成证据与局限分层报告，保留strict_verdict/finding/coverage，不把生成成功当科学问题清零。

## AI 读取的产物

主题目录下，runtime-state.json 记录输入指纹、run_id、阶段提交和摘要；run-summary.json 记录本次 verdict、pipeline_complete、output_dir 及 final 文件哈希。只有成功且校验通过的当前运行可交付。

阶段 envelope 位于 artifacts/<run_id>/；首次及补搜资料位于 runs/<run_id>/initial/ 和 targeted-N/；最终 report.md、sources.json、tree/ 位于 runs/<run_id>/final/。目录存在或历史报告存在不能替代当前运行成功。

结构化失败诊断在 `run-summary.json` 的 `stage_metrics[].diagnostic`，同轮 `diagnostic_path` 指向 `runs/<run_id>/failure-diagnostic.json`。只含结束原因/长度哈希/token/JSON位置/schema字段及来源块/选择条目标识，不含响应正文、推理正文、headers或密钥。canonical `run-record.json` 严格字段保持不变；失败诊断sidecar不作为成功成果或医学证据。

分层报告 `final/evidence-audit.json`保留selected/unselected原始claim IDs、节点内容家族覆盖、未解决finding、显式known_limitations及“不等于独立研究”的边界；与全部final文件共同受summary哈希校验。核心选材有maxItems和最多一次纠正，模型短编号映回ledger ID；邻近原文与限制仅作反向审核，不把选材遗漏解释成论文未报告。content_filter在抽取/阶段即停不重试。`run-record.json`字段不扩展，summary只附report_contract/strict_evidence_complete/unresolved_finding_count。

默认model_support仍使用section_facts，不把其称为全文语义保证。显式source_grounded已接snapshot/full-source读取、现有merge/inspect/qgate内部提交、中文原子正反证与独立review；source_exact仅窄原文模式。生产者/消费者字段与final集合互操作由真实九段测试验证。P3真实医学终验状态从STATUS读取，不能由代码或离线绿灯推定。

历史来源和边界见 docs/plan/NINE-STAGE-BASELINE.json；当前进度及可复现实验从 STATUS.md 进入。
