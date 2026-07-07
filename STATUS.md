# research-tool 开发状态

| 功能点 | 状态 | 备注 |
|--------|------|------|
| FP01: 六阶段管道 | ✅ | Collect→Deepen→Clean→Extract→Organize→Report |
| FP02: 12 搜索源 | ✅ | DDG/OpenAlex/Crossref/arXiv/S2/PubMed/Wikipedia/GitHub/GoogleNews/Tavily/Bilibili/X（SearchEngine Literal 含 14 名：scholar→arxiv、x/twitter→x_backend 为别名） |
| FP03: LLM 抽象 | ✅ | 5 provider：DeepSeek/OpenAI/Anthropic/Ollama/MiniMax；from_config 把 openai/deepseek/ollama/minimax 路由到 OpenAILLMClient，anthropic 走 AnthropicLLMClient |
| FP04: CLI 接口 | ✅ | 11 个命令（collect/ingest-pdf/ocr-engines/clean/extract/organize/report/run/status/ui/config） |
| FP05: Python SDK | ✅ | `research()` / `quick_collect()` 便捷函数 |
| FP06: Web UI | ✅ | Gradio 可视化界面 |
| FP07: PDF 摄取 | ✅ | MinerU 集成（+ custom/paddleocr-vl/unlimited-ocr/vision-llm 可插拔引擎） |
| FP08: 反偏差深挖 | ✅ | 实体拆分 + 画像注入 + 缺口检测 + 同名消歧 |
| FP09: 反向传播 | ✅ | 知识树质量评估循环 |
| FP10: 收束节点 v0.1.1 | ✅ | 密钥/日志/异常/死代码/架构/文档全面整改 |
| FP11: 持续优化（2026-07） | ✅ | 包名 src→research_tool；MiniMax 永久视频总结器（ADR 0003，--video-url 默认接入）；DeepseekClient 移除（M-006）；defusedxml 迁移（S314）；resolve_exit_code 404/400 + config 路径修正；移除死依赖 arxiv>=2.1；SSRF 防护（common/url_guard.py）；gitleaks CI + ADR 0002；ruff 全仓清扫 |

## 技术债

| 问题 | 优先级 | 发现节点 |
|------|--------|---------|
| 覆盖率报告（pytest-cov 未安装） | 🟡 中 | v0.1.1 收束 |
| Git commit-msg 钩子（commitlint 配置已就位，pre-commit/CI 强制未落地） | 🟡 中 | v0.1.1 收束 |
| wire resolve_exit_code 到 CLI 退出路径（CLI 现用 typer.Exit(code=1)，resolve_exit_code 仅库内调用） | 🟡 中 | 2026-07 Round 4 |
| deepen.run 函数过长（已 noqa PLR0915，拆分仍 P1） | 🟢 低 | v0.1.1 收束 |
| webui 长函数（run_video_note/run_web/build_ui 已 noqa PLR0915） | 🟢 低 | v0.1.1 收束 |
| SSRF 封禁表可配置（198.18.0.0/15 当前豁免以兼容本机 DNS 代理） | 🟢 低 | 2026-07 SSRF |
| 桥接脚本 --use-minimax-summary 标志冗余（MiniMax 已是核心默认，R8） | 🟢 低 | 2026-07 Round 8 |
