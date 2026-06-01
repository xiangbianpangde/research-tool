# research-tool 开发状态

| 功能点 | 状态 | 备注 |
|--------|------|------|
| FP01: 六阶段管道 | ✅ | Collect→Deepen→Clean→Extract→Organize→Report |
| FP02: 10 搜索源 | ✅ | DDG/OpenAlex/Crossref/arXiv/S2/PubMed/Wikipedia/GitHub/GoogleNews/Tavily |
| FP03: LLM 抽象 | ✅ | DeepSeek/OpenAI/Anthropic/Ollama |
| FP04: CLI 接口 | ✅ | 7 个命令（collect/ingest-pdf/clean/extract/organize/report/run/status/ui/config） |
| FP05: Python SDK | ✅ | `research()` / `quick_collect()` 便捷函数 |
| FP06: Web UI | ✅ | Gradio 可视化界面 |
| FP07: PDF 摄取 | ✅ | MinerU 集成 |
| FP08: 反偏差深挖 | ✅ | 实体拆分 + 画像注入 + 缺口检测 + 同名消歧 |
| FP09: 反向传播 | ✅ | 知识树质量评估循环 |
| FP10: 收束节点 v0.1.1 | ✅ | 密钥/日志/异常/死代码/架构/文档全面整改 |

## 技术债

| 问题 | 优先级 | 发现节点 |
|------|--------|---------|
| 覆盖率报告 | 🟡 中 | v0.1.1 收束 |
| Git commit-msg 钩子 | 🟡 中 | v0.1.1 收束 |
| deepen.run 函数过长 (52 行) | 🟢 低 | v0.1.1 收束 |
| webui.run_web 函数过长 (94 行) | 🟢 低 | v0.1.1 收束 |
