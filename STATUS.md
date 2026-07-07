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
| FP09: 反向传播 | ✅ | 知识树质量评估循环；已测试（R11，test_pipeline_backward.py，8 用例：_invalidate_after_collect + _recollect + 5 stream 控制流 + .deepen_done 契约） |
| FP10: 收束节点 v0.1.1 | ✅ | 密钥/日志/异常/死代码/架构/文档全面整改 |
| FP11: 持续优化（2026-07） | ✅ | 包名 src→research_tool；MiniMax 永久视频总结器（ADR 0003，--video-url 默认接入）；DeepseekClient 移除（M-006）；defusedxml 迁移（S314）；resolve_exit_code 404/400 + config 路径修正；移除死依赖 arxiv>=2.1；SSRF 防护（common/url_guard.py）；gitleaks CI + ADR 0002；ruff 全仓清扫；M-010 错误系统 CLI 接线（format_error+resolve_exit_code→_fail_video_ingest，VideoIngestError 退出码 1→403/401/404/400/500）；注册 E_VID_003_PIPELINE_FAIL（错误码 12→13）；M-010 遗留错误码注册（13 个：E_VID_URL_REJECTED/E_DL_002_VERSION_TOO_OLD/E_DL_BILI_403/E_DL_LOCAL_001/002/E_TR_001/E_TR_003/E_TR_004/E_PIPE_001/E_PIPE_DISK_FULL/E_PIPE_CONFIG_MISMATCH/E_LIM_001/E_LIM_002，错误码 13→26）+ register/raise 不一致对齐（14 处；4 处 raise 语义校正：yt-dlp 未安装 500→403、Cookie 缺失 400→401）；backward-loop 覆盖（R11，test_pipeline_backward.py，8 用例：_invalidate_after_collect + _recollect + 5 stream 控制流 + .deepen_done 契约，原零覆盖） |

## 技术债

| 问题 | 优先级 | 发现节点 |
|------|--------|---------|
| 覆盖率报告（pytest-cov 未安装） | 🟡 中 | v0.1.1 收束 |
| Git commit-msg 钩子（commitlint 配置已就位，pre-commit/CI 强制未落地） | 🟡 中 | v0.1.1 收束 |
| ✅ VideoIngest 错误码字典与 raise 站点脱节 — R10 已解决（13 个遗留错误码注册 + 14 处 register/raise 不一致对齐；AST 审计测试确认所有 raise 站点 lookup_code 成功，M-010 不再装饰性） | ✅ 已解决 | 2026-07 Round 10 |
| 4 个死错误码常量（E_VID_PIPELINE_FAIL/E_LLM_002_CHAPTERS_FALLBACK/E_NS_001_YAML_PARSE_FAIL/E_NS_002_SCREENSHOT_MISSING：定义+导出但从不 raise，R10 审计发现；可安全删除或注册） | 🟢 低 | 2026-07 Round 10 |
| deepen.run 函数过长（已 noqa PLR0915，拆分仍 P1） | 🟢 低 | v0.1.1 收束 |
| webui 长函数（run_video_note/run_web/build_ui 已 noqa PLR0915） | 🟢 低 | v0.1.1 收束 |
| SSRF 封禁表可配置（198.18.0.0/15 当前豁免以兼容本机 DNS 代理） | 🟢 低 | 2026-07 SSRF |
| 桥接脚本 --use-minimax-summary 标志冗余（MiniMax 已是核心默认，R8） | 🟢 低 | 2026-07 Round 8 |
| vestigial register_error 覆盖保留：让 _fail_video_ingest 用 ingest 模块 ErrorRecord 覆盖（scene/cause/suggestion）而非默认值——涉及状态存储、并发线程安全、状态清理，独立设计轮非收尾；完成 M-010 线（R9 接线→R10 注册→此轮保留覆盖） | 🟡 中 | 2026-07 Round 12 |
| deliverable.md + deliverable-track-*.md 含重命名前 src/ 路径（跟踪→更新；未跟踪→gitignore） | 🟢 低 | 2026-07 Round 12 |
| P3-1：扩展 01-架构受批偏差列表（cli.py:33、webui.py:78 的 presentation→infrastructure 导入） | 🟢 低 | 2026-07 Round 12 |
| P3-2：ocr_cmd `--` 分隔符（文件名参数注入，极低风险） | 🟢 低 | 2026-07 Round 12 |
