# research-tool

一个 **Python 核心引擎 + 多接口层** 的调研工具：给定主题 → 自动产出知识树 / 调研报告。

实现依据 `../架构设计/` 下 8 份设计文档。五阶段管道，Stage 之间仅通过文件系统通信，可中断、可恢复、可独立调试。

```
topic ─→ Collect ─→ Deepen ─→ Clean ─→ Extract ─→ Organize ─→ Report ─→ report.md
         raw/       raw/++     clean/    extracted/  tree/
```

| 阶段 | 职责 | 输出 |
|------|------|------|
| Collect | 搜索（见下方 7 个源）+ 抓取（Crawl4AI，回退 httpx） | `raw/*.md` + `sources.json` |
| Deepen | 反偏差深挖：实体拆分→多视角搜索 + 缺口/矛盾补搜（可选，默认开） | 追加 `raw/*.md` + `.deepen_done` |
| Clean | 去 HTML/导航/广告噪音，定位正文 | `clean/*.md` + `quality.json` |
| Extract | LLM 抽取实体/关系/三元组（可选） | `extracted/*.json` |
| Organize | LLM 构建 4–7 节点知识树（S1–S4） | `tree/00-主表.md` + `N*.md` |
| Report | LLM 合成报告（report/feasibility/review/article） | `report.md` |

## 一键启动（Windows，推荐）

双击 **`start.bat`** 即可。首次运行自动建 `.venv`、装依赖（2-5 分钟），并从
`.env`（脚本目录 / 上级目录 / 用户目录任一）读取 `deepseek_api_key`、
`tavily_api_key`。之后是菜单：

```
1. 可视化界面  浏览器图形界面（推荐）
2. 网页调研    输入主题 → 自动搜索→清洗→知识树→报告
3. PDF 调研    选本地 PDF 文件夹 → MinerU 解析（可选翻译）→报告
4. 查看进度    某主题做到哪一步
5. 高级命令行  手动敲 research ...
```

### 可视化界面（Gradio）

```bash
pip install -e ".[ui]"   # 或 pip install gradio
research ui              # 浏览器打开 http://127.0.0.1:7861
```

左栏选「网页调研 / PDF 调研」、填主题与选项，右栏实时看进度、读报告、下载知识树文件。

`.env` 示例（放在仓库上级或用户目录）：
```
deepseek_api_key=sk-xxxx
tavily_api_key=tvly-xxxx
```
没有 `.env` 时脚本会提示输入 DeepSeek Key 并保存。PDF 模式默认调用
`C:\Users\<你>\pdf2zh\.venv\Scripts\mineru.exe`，找不到会让你输入路径。

---

## 安装（手动 / 非 Windows）

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
research run "Transformer 架构" -s web -s arxiv      # 一键全流程（默认含 Deepen 深挖）
research run "Transformer 架构" -s tavily -r 3       # 3 轮多关键词搜索（方法论 1.1）
research collect "Transformer" --dry-run             # 仅看搜索结果（失败源会打印警告）
research collect "Transformer" -n 8 -d 2 -r 2        # 阶段1，2 轮搜索
research clean   ./research-output/transformer/raw    # 阶段2
research extract ./research-output/transformer/clean  # 阶段3
research organize ./research-output/transformer/extracted  # 阶段4
research report  ./research-output/transformer/tree   # 阶段5
research status  ./research-output/transformer        # 查看进度
research config                                       # 查看解析后的配置
research run "X" --skip extract --no-resume           # 跳过抽取/不跳过已完成
research run "X" --skip deepen                        # 关闭反偏差深挖（更快/更省）
```

### 搜索源（`-s`，可多选）

| 源 | 名称 | Key | 适用 |
|----|------|:---:|------|
| `web` | DuckDuckGo | 免 | 通用网页 |
| `openalex` | OpenAlex | 免 | **论文首选**：2.5亿+ 全学术，自动混合"经典+最新" |
| `crossref` | Crossref | 免 | 1.5亿+ DOI，跨出版商元数据 |
| `arxiv` | arXiv | 免 | 预印本论文（按相关性，偏经典） |
| `semantic_scholar` | Semantic Scholar | 可选 | 全出版商论文 + 引用数（无 key 易限流） |
| `pubmed` | PubMed | 免 | 生物医学 3700万+ 文献 |
| `wikipedia` | Wikipedia | 免 | 百科背景、人物生平（中英双站点） |
| `github` | GitHub | 可选 | 开源实现、代码、社区活跃度 |
| `google_news` | Google News | 免 | 最新进展 / 新闻 |
| `tavily` | Tavily | 需 | LLM 优化的网页搜索 |

**论文调研推荐 `openalex`**：覆盖最全、限流最宽、且自动一半按相关性 + 一半按发表日期检索，
解决 arxiv/semantic_scholar 默认按相关性排序「搜不到近期论文」的问题。
可选 Key/邮箱在 `config.yaml` 配 `semantic_scholar_api_key` / `github_token` /
`openalex_mailto`（OpenAlex·Crossref 的 polite pool，更稳）提升配额——均不配也能用。

### Deepen 反偏差深挖

`run` 默认在 Collect 后插入 Deepen：先把话题**拆成独立实体**做多视角搜索（消除"单一锚点绑架"偏差，如 `"康怡琳 中南民族大学"` 会独立搜 `"Yilin Kang"`、`"康怡琳 博士"`），再扫描已采内容**补搜缺失维度**、对**矛盾信息**反向验证。调优参数全部在 `config.yaml` 的 `deepen:` 段（depth/breadth/max_entities 等），`--skip deepen` 一键关闭。

## 从 PDF 调研（pdf2zh / MinerU 集成）

把一文件夹学术 PDF 直接综述成知识树 + 报告。PDF 经 MinerU 解析为 Markdown
（保留公式/表格/图引用），可选用 LLM 翻成中文，再走 clean→extract→organize→report。

```bash
pip install -e ".[pdf]"   # 安装 MinerU（重依赖，~7GB 含模型），或复用 pdf2zh 的 .venv

# 仅摄取：PDF 文件夹 → raw/（可选 --translate 译中文）
research ingest-pdf ./papers -T "扩散模型综述" --translate

# 一键：PDF 文件夹 → 中文知识树 + 报告（collect 阶段改为 PDF 摄取）
research run "扩散模型综述" --pdf-dir ./papers --translate --skip extract
```

未把 mineru 装到全局时，用 `--mineru-cmd` 指向 pdf2zh 虚拟环境里的可执行：
`--mineru-cmd "C:\path\to\pdf2zh\.venv\Scripts\mineru.exe"`。

SDK：

```python
from research_tool import ingest_pdfs, PdfIngestConfig, translate_markdown
result = await ingest_pdfs("./papers", "./out/topic",
                           PdfIngestConfig(translate=True), llm)
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
