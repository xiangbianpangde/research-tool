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
| nine_loop/research.py | 检视、缺口解决记录、质量门控、报告支持核验 |
| infrastructure/stages/collector.py、fetcher.py | 搜索与真实正文抓取 |
| infrastructure/stages/research_strategies.py | Collect 内部的实体拆分、画像与查询策略 |
| infrastructure/stages/cleaner.py、extractor.py | 正文清洗、去重、相关性过滤及事实抽取 |
| infrastructure/ingest/、application/video_pipeline.py | PDF/视频输入适配 |
| domain/models.py、config.py | 固定九段配置与字段校验 |

正式阶段：collect、clean、extract、knowledge、inspect、targeted、merge、qgate、report。初次知识构建后先 inspect/qgate；CONTINUE 才 targeted/merge 并重检；STOP_SUCCESS 才生成报告。

## AI 读取的产物

主题目录下，runtime-state.json 记录输入指纹、run_id、阶段提交和摘要；run-summary.json 记录本次 verdict、pipeline_complete、output_dir 及 final 文件哈希。只有成功且校验通过的当前运行可交付。

阶段 envelope 位于 artifacts/<run_id>/；首次及补搜资料位于 runs/<run_id>/initial/ 和 targeted-N/；最终 report.md、sources.json、tree/ 位于 runs/<run_id>/final/。目录存在或历史报告存在不能替代当前运行成功。

历史来源和边界见 docs/plan/NINE-STAGE-BASELINE.json；当前进度及可复现实验从 STATUS.md 进入。
