# research-tool

一个 **Python 核心引擎 + 多接口层** 的调研工具：给定主题 → 自动产出知识树 / 调研报告。

实现依据 `../架构设计/` 下 8 份设计文档。五阶段管道，Stage 之间仅通过文件系统通信，可中断、可恢复、可独立调试。

```
topic ─→ Collect ─→ Clean ─→ Extract ─→ Organize ─→ Report ─→ report.md
         raw/       clean/    extracted/  tree/
```

| 阶段 | 职责 | 输出 |
|------|------|------|
| Collect | 搜索（DuckDuckGo / arxiv / Tavily）+ 抓取（Crawl4AI，回退 httpx） | `raw/*.md` + `sources.json` |
| Clean | 去 HTML/导航/广告噪音，定位正文 | `clean/*.md` + `quality.json` |
| Extract | LLM 抽取实体/关系/三元组（可选） | `extracted/*.json` |
| Organize | LLM 构建 4–7 节点知识树（S1–S4） | `tree/00-主表.md` + `N*.md` |
| Report | LLM 合成报告（report/feasibility/review/article） | `report.md` |

## 安装

```bash
cd research-tool
pip install -e .              # 核心（含 httpx 轻量抓取）
pip install -e ".[all]"       # + Crawl4AI + 三个搜索后端
crawl4ai-setup               # 安装 Crawl4AI 用的浏览器（用 [crawl] 时）
pip install openai           # LLM 客户端（DeepSeek/OpenAI 兼容）
```

## 配置

复制 `config.example.yaml` 为 `config.yaml`，至少配置 LLM：

```yaml
llm:
  provider: deepseek
  model: deepseek-chat
  api_key: ${DEEPSEEK_API_KEY}
  base_url: https://api.deepseek.com/v1
```

配置优先级：命令行参数 > 环境变量 > config.yaml > 默认值。
查找顺序：`--config` > `$RESEARCH_CONFIG` > `./config.yaml` > `~/.research/config.yaml`。

```bash
export DEEPSEEK_API_KEY=sk-...
export TAVILY_API_KEY=tvly-...   # 仅用 tavily 搜索时
```

## CLI

```bash
research run "Transformer 架构" -s web -s arxiv      # 一键全流程
research run "Transformer 架构" -s tavily -r 3       # 3 轮多关键词搜索（方法论 1.1）
research collect "Transformer" --dry-run             # 仅看搜索结果
research collect "Transformer" -n 8 -d 2 -r 2        # 阶段1，2 轮搜索
research clean   ./research-output/transformer/raw    # 阶段2
research extract ./research-output/transformer/clean  # 阶段3
research organize ./research-output/transformer/extracted  # 阶段4
research report  ./research-output/transformer/tree   # 阶段5
research status  ./research-output/transformer        # 查看进度
research config                                       # 查看解析后的配置
research run "X" --skip extract --no-resume           # 跳过抽取/不跳过已完成
```

## Python SDK

```python
import asyncio
from research_tool import research

result = asyncio.run(research("Transformer 架构", llm_provider="deepseek"))
print(result.report_result.report_path)
```

单独使用某个 Stage / 自定义管道：

```python
from research_tool import ResearchPipeline, load_config

cfg = load_config("config.yaml", overrides={"topic": "X"})
pipe = ResearchPipeline(cfg)

async for ev in pipe.stream("X"):       # 流式进度
    print(ev.stage, ev.status, ev.message)
```

测试用 `MockLLMClient` 注入，无需 API Key：

```python
from research_tool import MockLLMClient
llm = MockLLMClient(chat_response="...", structured_response=...)
```

## 开发

```bash
pip install -e ".[dev]"
pytest -q
```

## 架构要点（详见 ../架构设计/06-关键设计决策.md）

- **仅文件系统通信**：每个 Stage 输入/输出都是文件，可独立重跑、可检查中间产物。
- **LLM 抽象**：所有 Stage 依赖 `LLMClient`，不直接绑定 SDK；支持 deepseek/openai/ollama/anthropic。
- **Extractor 可选**：`--skip extract` 或 `extractor.enabled: false` 时，Organizer 直接吃 `clean/` 文本。
- **幂等恢复**：已有输出的 Stage 自动跳过（`--no-resume` 关闭）。
