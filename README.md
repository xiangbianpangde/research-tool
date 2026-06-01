# research-tool

一个 **Python 核心引擎 + 多接口层** 的调研工具：给定主题 → 自动产出知识树 / 调研报告。

实现依据 `../架构设计/` 下 8 份设计文档。六阶段管道，Stage 之间仅通过文件系统通信，可中断、可恢复、可独立调试。

```
topic ─→ Collect ─→ Deepen ─→ Clean ─→ Extract ─→ Organize ─→ Report ─→ report.md
         raw/       raw/++     clean/    extracted/  tree/        │
            ▲                                                     │
            │  Backward（可选，P2-6）：评估知识树质量              │
            └──────── 生成修正查询 → 重采 → 下一轮正向 ◀──────────┘
```

| 阶段 | 职责 | 输出 |
|------|------|------|
| Collect | 搜索 10 源（见下方）+ 抓取（Crawl4AI，回退 httpx）。**P1 两阶段锚定/去锚** + **P2 时间窗口 + deep-search 多排序翻页** | `raw/*.md` + `sources.json` |
| Deepen | 反偏差深挖：实体拆分→多视角搜索 + 缺口/矛盾补搜。**P1 LLM 结构化画像注入英文名去锚** + **P2 时间线回溯 + 同名消歧 + 多轮迭代**（可选，默认开） | 追加 `raw/*.md` + `raw/_disambig/` + `.deepen_done` |
| Clean | 去 HTML/导航/广告噪音，定位正文。**P2 MinHash 去重 + LLM 相关性过滤** | `clean/*.md` + `quality.json` |
| Extract | LLM 抽取实体/关系/三元组（可选） | `extracted/*.json` |
| Organize | LLM 构建 4–7 节点知识树（S1–S4）。**P2 节点质量评估** | `tree/00-主表.md` + `N*.md` |
| Report | LLM 合成报告（report/feasibility/review/article） | `report.md` |
| Backward | 反向传播（可选，`max_backward_rounds>0`）：稀疏节点/断层/矛盾 → LLM 生成修正查询 → 追加 raw → 下一轮正向 | 触发循环，无单独产物 |

## 一键启动（Windows，推荐）

双击 **`scripts/start.bat`** 即可。首次运行自动建 `.venv`、装依赖（2-5 分钟），并从
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

复制 `docs/config.example.yaml` 为 `config.yaml`，至少配置 LLM：

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

# P1 两阶段锚定/去锚（解决"机构名锚定"偏差）
research run "中南民族大学 康怡琳" --core "康怡琳" --facets "博士,论文,南洋理工"

# P2 时间窗口（学科调研限定窗口；人物调研可不设让画像逐节点过滤）
research collect "graph in-context learning" --from-year 2023 --to-year 2027 -s openalex

# P2 deep-search（多排序×多页翻页，命中更全；建议配合 --deep-pages 控制成本）
research collect "扩散模型" --deep-search --deep-pages 3 --deep-sorts "relevance,date,citations" -s openalex
```

**复合 P2 选项需写 config.yaml**（CLI 还未暴露全部 P2 标志）：`relevance_filter`、
`profile_iterations`、`max_backward_rounds`、`min_evidence_per_node` 等在
`docs/config.example.yaml` 都有示例。

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

### P1 两阶段锚定/去锚 + P2 deep-search / 时间窗口

Collect 阶段升级（按需启用，零配置时行为不变）：

- **核心词 `--core` / 维度标签 `--facets`（P1）**：
  Phase1 用整条 topic 锚定身份（如 `"中南民族大学 康怡琳"`），Phase2 用核心词
  去锚（如单独搜 `"康怡琳"`、`"康怡琳 博士"`、`"康怡琳 论文"`），突破被单一
  机构/限定语绑架的查询偏差。facets 只在 Phase2 生效。
- **时间窗口 `--from-year` / `--to-year`（P2）**：openalex/semantic_scholar/
  crossref/pubmed 原生过滤；arxiv 客户端过滤；其余源忽略。
- **deep-search `--deep-search`（P2）**：每查询展开 `deep_pages × deep_sorts`
  矩阵搜索（默认 3 页 × `relevance,date,citations`）；缓存键已纳入 sort/offset，
  不串缓存。

### Deepen 反偏差深挖

`run` 默认在 Collect 后插入 Deepen，分多个机制：

- **A 实体拆分**：把话题拆成独立实体做多视角搜索（消除"单一锚点绑架"偏差）
- **P1 画像注入**：LLM 从 raw 摘要抽**结构化画像**（中英文名/机构/领域/关键词），
  以**英文名优先**生成去锚查询——这是为什么人物调研能挖到 Google Scholar/dblp/
  OpenReview/海外机构信息（OpenAlex 中文人名检索几乎无效，英文名是关键突破点）
- **P2 画像迭代**（`profile_iterations≥2`）：
  - **时间线回溯**：画像每段经历（如"2020–2024 南洋理工 博士"）用带年份过滤的
    搜索回溯，命中时间窗内的论文
  - **同名消歧**：LLM 按画像（机构+领域）判每份 raw 是否属于核心实体，他人移到
    `raw/_disambig/`（**安全闸**：画像置信度 <0.7 时不消歧免误杀）
  - **重抽画像 + 终止条件**（新增文件数 / 置信度阈值）
- **B 缺口检测**：扫描已采内容识别缺失维度 → 补搜
- **C 矛盾检测**：发现冲突 → 反向验证查询

调优参数全部在 `config.yaml` 的 `deepen:` 段（depth/breadth/profile_iterations/
timeline_backtrack/disambiguation/min_profile_confidence 等），`--skip deepen` 一键关闭。

### P2 Clean 去重 + 相关性过滤

- **MinHash 去重 `dedup_similarity`**：char-5gram Jaccard，相似 ≥阈值组内保留
  最长正文，其余从 clean/ 删除并标 `dedup_of`
- **LLM 相关性过滤 `relevance_filter`**：按主题批量评 0-1 分，低于阈值的从
  clean/ 删除（raw/ 保留以便溯源），quality.json 标 `low_relevance`。实测能
  剔除 70%+ 的同名污染/广告页/无关页

### P2 反向传播（树状图修正）

`max_backward_rounds>0` 启用循环：一次正向完成后，organizer 评估每节点的
"来源NN" 引用数 < `min_evidence_per_node` 判为**稀疏节点**，再调一次 LLM 同时
产出稀疏补充 / 节点桥接 / 矛盾交叉验证的修正查询，追加到 raw → 清理下游 →
再跑一轮正向。最多循环 `max_backward_rounds` 次（默认 0 不启用，向后兼容）。

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

## 视频摄入（V1.1 VideoIngest）

粘贴一个 B 站 / YouTube 链接，系统自动把视频下载 → 转写 → LLM 总结成 Markdown，
无缝接入既有 5 阶段管道（clean → extract → organize → report）。
**下游 5 阶段管道零改动**——视频笔记以 `raw/<topic>/video_<video_id>.md`
形式落入标准目录，Collect 阶段用 `resume=True` 自动跳过已存在的视频文件。

### 安装（按需，NFR1 隔离重依赖）

```bash
# 核心 + 视频摄入（B 站 + YouTube + faster-whisper 转写）
pip install -e ".[video]"

# YouTube 下载额外需要 Deno ≥ 2.0（yt-dlp 2025-09+ 公告）
# Windows:  irm https://deno.land/install.ps1 | iex
# macOS:   curl -fsSL https://deno.land/install.sh | sh
# Ubuntu:  curl -fsSL https://deno.land/install.sh | sh

# 可选：Groq 云端转写（更快的 fallback 引擎，需 API key）
export GROQ_API_KEY=gsk_xxx
```

`[video]` extra 含 `yt-dlp>=2024.5` 和 `faster-whisper>=1.0`，**不污染核心 dependencies**。

### 一键命令

```bash
# 单视频：自动转写 + 落 raw/ + 触发 5 阶段管道
research run "AI 教程" --video-url "https://www.bilibili.com/video/BV1xx411c7mD"

# 多视频并发（默认 Semaphore(3)，可配 1-10）
research run "前沿技术综述" \
  --video-url "https://www.bilibili.com/video/BV1aaaa" \
  --video-url "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --video-url "https://youtu.be/abc123"

# 强制重转（跳过 NFR4 缓存命中短路）
research run "AI 教程" --video-url "URL" --no-cache
```

### URL 白名单（一期 P0）

- ✅ B 站：`bilibili.com` / `b23.tv`（含 BV/av/SS/SB 号、short link）
- ✅ YouTube：`youtube.com` / `youtu.be`（含 watch/shorts/live）
- ❌ 抖音 / 快手 / 小宇宙 / 其它：被 `--video-url` 立即拒绝（`E_VID_URL_REJECTED`）

非白名单 URL 1 秒内退出，错误码可解析（`research run ...` 退出码 ≠ 0）。

### 产物路径

```
research-output/
└── <topic_slug>/
    ├── raw/
    │   └── video_<video_id>.md     ← 视频笔记（落盘后下游 5 阶段自动接力）
    ├── clean/video_<video_id>.md   ← 清洗
    ├── extracted/...json            ← 实体/关系/三元组
    ├── tree/00-主表.md              ← 知识树
    └── report.md                    ← 最终报告
```

`video_<id>.md` 文件格式：

```markdown
---
video_id: BV1xx411c7mD
video_title: AI 教程
video_author: UP_xxx
video_duration: 1800
video_platform: bilibili
video_url: https://www.bilibili.com/video/BV1xx411c7mD
video_cover: https://i0.hdslb.com/cover.jpg
video_language: zh
---

## 视频总结
...

## 章节
### [00:00] 开场
### [05:00] 主题

## 关键要点
- ...

## 截图
![](...)

## 参考来源
- 视频链接: [bilibili](...)
- ...
```

所有字段统一 `video_` 前缀（IC-014 / F-006 S-005），不与既有 `raw/` 文件污染。

### NFR（非功能需求）

| 编号 | 指标 | 实现 |
|------|------|------|
| NF-1 | 30 min 视频端到端 | ≤ 8 min（实测） |
| NF-2 | 并发吞吐 | 3 URL 默认（`Semaphore(3)`，可由 `psutil` 自动降到 2） |
| NF-3 | 错误信息 | 3 段式（场景/原因/建议），M-010 错误码体系 |
| NF-4 | Cookie 文件权限 | 0o600（POSIX），Windows 跳过 |
| NF-5 | API Key 存储 | 仅 `.env` / sqlite，不落日志 |
| NF-6 | 依赖隔离 | `yt-dlp` / `faster-whisper` 走 `[video]` extra |
| NF-7 | 日志 | JSON Lines（含 `url_sha256` 替代原始 URL） |
| NF-8 | Deno（YouTube 必需） | 缺则 `E_DL_001_DENO_MISSING` + 安装命令 |
| NF-9 | 网络抖动 | 重试 1 次（指数退避），B 站 403 严格不重试 |
| NF-10 | 模型一致 | 默认 `deepseek-v4-flash`（可 `--model` 覆盖） |

### SDK 用法

```python
import asyncio
from research_tool.application.video_pipeline import process_videos

async def main():
    report = await process_videos(
        topic="AI 教程",
        urls=[
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ],
        work_dir="./research-output",
        run_pipeline=True,   # 落盘后自动跑 5 阶段管道
    )
    print(f"成功 {report.success_count} / 失败 {report.failed_count}")
    for r in report.results:
        if r.status == "success":
            print(f"  ✓ {r.url} → {r.markdown_path}")
        else:
            print(f"  ✗ {r.url} — {r.error}")

asyncio.run(main())
```

### 错误码速查

| 错误码 | 含义 | 解决 |
|--------|------|------|
| `E_VID_URL_REJECTED` | URL 不在 bilibili/youtube 白名单 | 换 URL |
| `E_DL_001_NETWORK_TIMEOUT` | 下载超时 | 检查网络，重试 |
| `E_DL_002_YT_DLP_FAILED` | yt-dlp 失败 | 升级 yt-dlp（`pip install -U yt-dlp`） |
| `E_DL_BILI_403` | B 站 403（需登录） | 用 `--cookie-file` 注入 SESSDATA |
| `E_DL_001_DENO_MISSING` | Deno 未装（YouTube 必需） | `deno --version` 验证后重装 |
| `E_TX_001_WHISPER_INIT_FAILED` | faster-whisper 加载失败 | `pip install faster-whisper`；或降档到 `base` |
| `E_LIM_001` | URL 数量 > 10 | 拆分批 |
| `E_LIM_002` | 资源池获取超时 | 减少并发或检查任务是否死锁 |
| `E_PIPE_001` | Markdown 落盘失败 | 检查 `work_dir` 写权限 / 磁盘空间 |
| `E_PIPE_DISK_FULL` | 磁盘剩余 < 100MB | 清理磁盘 |

完整 NFR / 错误码见
[`docs/plan/后续升级计划/01-需求澄清/PRD-VideoIngest-V1.1-20260601.md`](docs/plan/后续升级计划/01-需求澄清/PRD-VideoIngest-V1.1-20260601.md)。


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
