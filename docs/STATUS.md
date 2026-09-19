# research-tool 开发状态

| 功能点 | 状态 | 备注 |
|--------|------|------|
| FP01: 六阶段管道 | ✅ | Collect→Deepen→Clean→Extract→Organize→Report；`brief/full` 产物契约 + stage completion marker + LLM 阶段有界重试 |
| FP02: 搜索后端 | ✅ | SearchEngine 注册含 opencli；常用 web/openalex/crossref/arxiv/s2/pubmed/wikipedia/github/news/tavily/bilibili/youtube/x + opencli 浏览器兜底 |
| FP03: LLM 抽象 | ✅ | 5 provider：DeepSeek/OpenAI/Anthropic/Ollama/MiniMax；可配置 connect/read/request 超时与 sdk_max_retries |
| FP04: CLI 接口 | ✅ | 主命令含 collect/ingest-pdf/ocr-engines/clean/extract/organize/report/run/status/ui/config/setup/wiki-stage/publish-wiki |
| FP05: Python SDK | ✅ | `research()` / `quick_collect()` 便捷函数 |
| FP06: Web UI | ✅ | Gradio 可视化界面 |
| FP07: PDF 摄取 | ✅ | MinerU 集成（+ custom/paddleocr-vl/unlimited-ocr/vision-llm 可插拔引擎） |
| FP08: 反偏差深挖 | ✅ | 实体拆分 + 画像注入 + 缺口检测 + 同名消歧 |
| FP09: 反向传播 | ✅ | 知识树质量评估循环；已测试（R11，test_pipeline_backward.py） |
| FP10: Research→Wiki P1 | ✅ | `wiki-stage` 内容寻址不可变包；禁止 legacy `publish-wiki` 写 active Vault |
| FP11: 交互式部署 | ✅ | `research setup` + 档位契约/preflight/回执；详见 README |
| FP12: 收束与持续优化 | ✅ | 包名 research_tool、MiniMax 视频默认、M-010 错误码、SSRF、gitleaks、ruff 清扫 |

## 测试权威值

| 指标 | 值 | 备注 |
|------|----|------|
| pytest collect | **840** | 以 `pytest --collect-only` 为准（2026-07-28 复验） |
| 推荐模式 | `brief` / `full` | 兼容 `fast` / `standard` / `deep` |

## 技术债

| 问题 | 优先级 | 发现节点 |
|------|--------|---------|
| 覆盖率报告（pytest-cov 未默认启用） | 🟡 中 | v0.1.1 收束 / R13 |
| Git commit-msg 钩子（commitlint 配置已就位，pre-commit/CI 强制未落地） | 🟡 中 | v0.1.1 收束 |
| ✅ VideoIngest 错误码字典与 raise 站点脱节 — R10 已解决 | ✅ 已解决 | 2026-07 Round 10 |
| 4 个死错误码常量（定义+导出但从不 raise） | 🟢 低 | 2026-07 Round 10 |
| deepen.run 函数过长（已 noqa PLR0915，拆分仍 P1） | 🟢 低 | v0.1.1 收束 |
| webui 长函数（run_video_note/run_web/build_ui 已 noqa PLR0915） | 🟢 低 | v0.1.1 收束 |
| SSRF 封禁表可配置（198.18.0.0/15 当前豁免以兼容本机 DNS 代理） | 🟢 低 | 2026-07 SSRF |
| 桥接脚本 --use-minimax-summary 标志冗余 | 🟢 低 | 2026-07 Round 8 |
| vestigial register_error 覆盖保留（M-010 状态覆盖设计未做） | 🟡 中 | 2026-07 Round 12 |
| deliverable.md + deliverable-track-*.md 含重命名前 src/ 路径 | 🟢 低 | 2026-07 Round 12 |
| P3-1：presentation→infrastructure 导入偏差列表未扩 | 🟢 低 | 2026-07 Round 12 |
| P3-2：ocr_cmd `--` 分隔符（文件名参数注入，极低风险） | 🟢 低 | 2026-07 Round 12 |
| TD-03 `cli.py` 历史硬编码本机路径（wiki-stage 默认已改为相对路径） | 🟡 中 | 2026-07-21 R13 |
| TD-04 多处 `except ... pass` 静默吞错 | 🟡 中 | 2026-07-21 R13 |
| 缺 pytest CI workflow | 🟡 中 | 2026-07-21 R13 |
| Star/文档增长（badge、截图、英文 Quick Start）仍空 | 🟡 中 | 2026-07-21 R13 |
| 2026-07 全项目审核（Round 13 audit） | ✅ 已完成 | 2026-07-21 |
