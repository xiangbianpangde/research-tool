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
| FP11: 持续优化（2026-07） | ✅ | 包名 src→research_tool；MiniMax 永久视频总结器（ADR 0003，--video-url 默认接入）；DeepseekClient 移除（M-006）；defusedxml 迁移（S314）；resolve_exit_code 404/400 + config 路径修正；移除死依赖 arxiv>=2.1；SSRF 防护（common/url_guard.py）；gitleaks CI + ADR 0002；ruff 全仓清扫；M-010 错误系统 CLI 接线（format_error+resolve_exit_code→_fail_video_ingest，VideoIngestError 退出码 1→403/401/404/400/500）；注册 E_VID_003_PIPELINE_FAIL（错误码 12→13） |

## 技术债

| 问题 | 优先级 | 发现节点 |
|------|--------|---------|
| 覆盖率报告（pytest-cov 未安装） | 🟡 中 | v0.1.1 收束 |
| Git commit-msg 钩子（commitlint 配置已就位，pre-commit/CI 强制未落地） | 🟡 中 | v0.1.1 收束 |
| 5 个遗留 VideoIngestError 错误码未注册（E_VID_URL_REJECTED/E_LIM_001/E_LIM_002/E_PIPE_001/E_PIPE_DISK_FULL；raise 站点走 _fail_video_ingest 降级 exit 1，不经 3 段式；ingest 模块 register_error 与 raise code 不一致） | 🟡 中 | 2026-07 Round 9 |
| deepen.run 函数过长（已 noqa PLR0915，拆分仍 P1） | 🟢 低 | v0.1.1 收束 |
| webui 长函数（run_video_note/run_web/build_ui 已 noqa PLR0915） | 🟢 低 | v0.1.1 收束 |
| SSRF 封禁表可配置（198.18.0.0/15 当前豁免以兼容本机 DNS 代理） | 🟢 低 | 2026-07 SSRF |
| 桥接脚本 --use-minimax-summary 标志冗余（MiniMax 已是核心默认，R8） | 🟢 低 | 2026-07 Round 8 |
