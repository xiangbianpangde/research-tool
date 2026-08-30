# research-tool

一个 **Python 核心引擎 + 多接口层** 的调研工具：给定主题 → 自动产出知识树 / 调研报告。

## 🆕 v1.0.0 — 九段管线成为默认路径

自 `v1.0.0-nine-loop-default` 起，**九段管线（nine-loop）成为默认执行路径**（渐进重构程序 RT-RF-2026 的交付，全程独立审核可审计）：

```
①collect → ②clean → ③extract → ④knowledge → ⑤inspect → ⑥targeted → ⑦merge → ⑧gate → ⑨report
```

- **确定性核心（Rust）**：来源身份（canonical URL / stable ID / content hash）由 `rt-identity` 子进程承载（pinned SHA），快 10.8×、省 8.3× 内存
- **模型链（R4.1）**：paratera `DeepSeek-V4-Flash-0731`（primary）→ `DeepSeek-V4-Flash` → `MiniMax-M3`；key 仅环境变量（`PARATERA_API_KEY`）
- **kill-switch 回退**：配置 `nine_loop.enabled: false` 即回退 legacy 六段（逐字节等同，演练验证）；回滚点 tag `pre-p7-default-switch`
- **质量对比**：与 Onyx stock Deep Research 配对确认 41 对任务统计持平（NOT_SURPASSED，CI 含 0 双向），交付完成率 100% vs 80.5%
- 详见 `docs/reports/`（P7/P8 收束报告）与 nine_loop/ 包内九段实现

---

实现依据 `../架构设计/` 下 8 份设计文档。legacy 六阶段管道（kill-switch 回退路径），Stage 之间仅通过文件系统通信，可中断、可恢复、可独立调试。

```
topic ─→ Collect ─→ Deepen ─→ Clean ─→ Extract ─→ Organize ─→ Report ─→ report.md
         raw/       raw/++     clean/    extracted/  tree/        │
            ▲                                                     │
            │  Backward（可选，P2-6）：评估知识树质量              │
            └──────── 生成修正查询 → 重采 → 下一轮正向 ◀──────────┘
```

| 阶段 | 职责 | 输出 |
|------|------|------|
| Collect | 搜索 15 个后端（另有 2 个别名）+ 抓取（Crawl4AI，回退 httpx）。**P1 两阶段锚定/去锚** + **P2 时间窗口 + deep-search 多排序翻页** | `raw/*.md` + `sources.json` |
| Deepen | 反偏差深挖：实体拆分→多视角搜索 + 缺口/矛盾补搜。**P1 LLM 结构化画像注入英文名去锚** + **P2 时间线回溯 + 同名消歧 + 多轮迭代**（可选，默认开） | 追加 `raw/*.md` + `raw/_disambig/` + `.deepen_done` |
| Clean | 去 HTML/导航/广告噪音，定位正文。**P2 MinHash 去重 + LLM 相关性过滤** | `clean/*.md` + `quality.json` |
| Extract | LLM 抽取实体/关系/三元组（可选） | `extracted/*.json` |
| Organize | LLM 构建 4–7 节点知识树（S1–S4）。**P2 节点质量评估** | `tree/00-主表.md` + `N*.md` |
| Report | LLM 合成报告（report/feasibility/review/article） | `report.md` |
| Backward | 反向传播（可选，`max_backward_rounds>0`）：稀疏节点/断层/矛盾 → LLM 生成修正查询 → 追加 raw → 下一轮正向 | 触发循环，无单独产物 |

## 交互式部署（推荐）

交互式部署是这条项目的**正式安装与验收入口**，不是附带脚本。Windows 可双击
`scripts/start.bat`；macOS / Linux 用 `scripts/setup.sh` 或 `research setup`。
它们最终都进入同一个向导：`scripts/setup_interactive.py`（打包进 wheel 后也可从
`research setup` 调用）。

### 你实际会跑到什么

```text
入口
  Windows: scripts/start.bat
  macOS/Linux: bash scripts/setup.sh
  任意平台: research setup
        │
        ▼
scripts/setup_interactive.py          # 交互向导：档位 / 密钥 / 验收
        │
        ├─ research_tool/presentation/setup_deployment.py
        │     · 三档能力契约（minimal / recommended / full）
        │     · 生成确定性安装计划 build_deployment_plan()
        │     · fail-fast 执行 execute_deployment()
        │
        ├─ 写入项目根 .env（0600，原子替换；wheel 默认 ~/.research/.env）
        ├─ preflight 验收：模块 import + 系统工具 + LLM/MiniMax PONG
        └─ 成功后写入无密钥回执 .research-deployment.json
```

设计原则只有一条：**安装计划与验收清单来自同一份档位能力表**。不能出现
“pip 装成功了，但 full 档要求的 ffmpeg 没过，却仍显示部署完成”。

### 快速开始

```bash
# 源码目录
cd research-tool
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[llm,search]"       # 先有 research CLI

# 进入交互式部署（首次默认完整档 full）
research setup
# 或
bash scripts/setup.sh
# Windows 也可：
#   scripts\setup.bat
#   双击 scripts\start.bat（部署后继续进菜单）
```

向导主菜单：

```text
1) 完整部署（装依赖 + 全部密钥向导）
2) 更新密钥（逐步问 LLM / GitHub / Tavily / 代理 / 视频 / X …）
3) 只更新 GITHUB_TOKEN
4) 只更新 TAVILY_API_KEY
5) 自选要更新的密钥（多选编号）
```

### 档位与能力

| 档位 | 名称 | Python extras | 验收能力 |
|------|------|---------------|----------|
| `minimal` | 最小可跑 | `llm,search` | `core` `llm` `search` |
| `recommended` | 推荐 | `llm,search,ui,crawl` | 上表 + `ui` `crawl` `crawl_browser` |
| `full` | 完整（首次默认） | `llm,search,ui,video,pdf,crawl` | 上表 + `video` `pdf` `ffmpeg` `deno` `opencli` |

含义简述：

- `core`：能 `import research_tool`
- `llm`：对应 provider 的 key + SDK；验收时还会做一次真实 PONG
- `search`：`ddgs` / `tavily` 可用
- `ui`：Gradio Web UI
- `crawl` + `crawl_browser`：Crawl4AI 包 + 浏览器资产（`crawl4ai-setup`）
- `video`：`yt_dlp` / `faster_whisper` + **独立的** `MINIMAX_API_KEY`
- `pdf`：MinerU
- `ffmpeg` / `deno` / `opencli`：系统工具，分别要求 ffmpeg ≥ 6、Deno ≥ 2、`opencli doctor` 通过

### 完整部署时向导怎么走

1. **选档位**  
   有历史回执时默认上次档位；首次默认 `3) 完整`。
2. **创建 / 复用 `.venv` 并安装依赖**  
   源码模式：`pip install -e ".[<extras>]"`  
   wheel 模式：`pip install research-tool[<extras>]`  
   随后 `pip check`；若档位含 crawl，再跑 `crawl4ai-setup`。  
   任一步非 0 立即失败，不会假装成功。
3. **配置 LLM**  
   选择 DeepSeek / OpenAI 兼容 / Anthropic / MiniMax 后，向导一次写入完整身份：
   `LLM_PROVIDER` + `LLM_MODEL` + `LLM_BASE_URL` + 对应 key。  
   不会只改 key 而留下旧 provider/endpoint 混用。
4. **按档位询问可选密钥**  
   GitHub / Tavily / S2 / OpenAlex 邮箱 / 代理 / MiniMax 视频 / Groq / X cookie 等。  
   密钥用 `getpass` 隐藏输入；代理允许写空以清空失效的 `HTTPS_PROXY`。
5. **原子写 `.env`**  
   保留注释与未知键；拒绝含换行/NUL 的注入值；POSIX 下权限 `0600`。
6. **preflight 验收**  
   检查 import、密钥是否存在、本地代理是否真在监听，以及档位全部能力。  
   含 `llm` 时做 LLM PONG；含 `video` 时**单独**做 MiniMax PONG（与主管道 LLM 隔离）。
7. **写回执**  
   仅当验收通过才写 `.research-deployment.json`：
   `profile` / `capabilities` / `python` / `verified`。  
   **回执不含任何密钥。**

Windows `start.bat` 每次启动都会先 `research setup --check-only`；回执缺失或验收失败会重新进入向导。

### 常用命令

```bash
research setup                         # 交互菜单；完整部署默认 full
research setup --profile full          # 跳过档位提问，直接完整档
research setup --profile recommended
research setup --profile minimal
research setup --secrets-only          # 只更新密钥，不重装依赖
research setup --github-only           # 只改 GITHUB_TOKEN
research setup --tavily-only           # 只改 TAVILY_API_KEY
research setup --check-only            # 按回执档位复验，不安装、不提问
research setup --check-only --profile full

# 未装 CLI 时也可直接：
python scripts/setup_interactive.py
bash scripts/setup.sh --check-only
```

约束：

- 需要 Python **3.11+**
- `--profile` 不能与 `--secrets-only` / `--github-only` / `--tavily-only` 组合
- 密钥只应出现在本地 `.env` / 终端隐藏输入中，**不要粘贴到聊天、README、issue**

### 产物与位置

| 产物 | 源码 checkout | wheel / 非源码 |
|------|---------------|----------------|
| 密钥 | 项目根 `.env` | `~/.research/.env` 或 `$RESEARCH_HOME/.env` |
| 部署回执 | 项目根 `.research-deployment.json` | 同上目录 |
| 虚拟环境 | 项目根 `.venv` | 复用当前解释器 |

`.env` 模板见 [`.env.example`](.env.example)。业务配置仍用 `config.yaml`，通过
`${ENV}` 引用环境变量；一般不必手改密钥字段。

### 可视化界面（Gradio）

部署 `recommended` / `full`（含 `ui`）后：

```bash
research ui              # 浏览器打开 http://127.0.0.1:7861
```

左栏选「网页调研 / PDF 调研」、填主题与选项，右栏实时看进度、读报告、下载知识树文件。

### 手动安装（调试 / 跳过向导时）

```bash
cd research-tool
python -m venv .venv
source .venv/bin/activate
pip install -e ".[llm,search]"     # 最小可跑
# 或
pip install -e ".[all]"            # 全部 Python 能力
crawl4ai-setup                     # 使用 [crawl] 时安装浏览器
# full 档系统工具仍需本机具备：ffmpeg、Deno、OpenCLI
research setup --check-only        # 建议仍用同一验收逻辑核对
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
research run "Transformer 架构" --mode full -s tavily # 强制六阶段；当前受限网络推荐 Tavily
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

# 新论文：官方源强制先抓，成功后才用 OpenAlex/Crossref/arXiv 扩展
research run "论文完整标题" \
  --official-url "https://作者或实验室的官方论文页" \
  --official-url "https://官方项目页或全文"

# P1 两阶段锚定/去锚（解决"机构名锚定"偏差）
research run "中南民族大学 康怡琳" --core "康怡琳" --facets "博士,论文,南洋理工"

# P2 时间窗口（学科调研限定窗口；人物调研可不设让画像逐节点过滤）
research collect "graph in-context learning" --from-year 2023 --to-year 2027 -s openalex

# P2 deep-search（多排序×多页翻页，命中更全；建议配合 --deep-pages 控制成本）
research collect "扩散模型" --deep-search --deep-pages 3 --deep-sorts "relevance,date,citations" -s openalex
```

### 两种推荐版本：brief / full

`brief` 与 `full` 是明确的产物契约。`brief` 只跑 Collect → Clean → Report，报告直接
基于 clean 资料生成，减少 LLM 调用；`full` 强制六阶段，不能与 `--skip` 同用，且
Extractor 任一块失败都会触发阶段重试，不会把空抽取误记为完成。

| 版本 | 默认行为 | 典型用途 |
|------|----------|----------|
| `brief` | `raw/` + `clean/` + 简略 `report.md`；2 次 LLM 阶段尝试 | 多主题快速摸底 |
| `full` | 六阶段完整产物；3 次 LLM 阶段尝试 + 原子完成标记 | 正式调研与可恢复交付 |

原有 `fast / standard / deep` 继续兼容，表示搜索成本档位；新任务建议直接选择
`brief / full`，避免“fast 到底是少搜索还是少产物”的歧义。

```bash
research run "主题" --mode brief -s tavily
research run "主题" --mode full -s tavily

# 兼容旧的深搜档位
research run "主题" --mode deep -r 3 --deep-pages 2 --skip extract
```

每次运行都会在主题目录生成 `run-summary.json`，记录总耗时、各阶段耗时、是否启用
resume/搜索缓存、`pipeline_complete`、完成与跳过阶段及来源审计。`extract/organize/report`
只有成功写入 `.stage-complete/<stage>.json` 后，续跑才会跳过；部分文件不再等于完成。

### 新论文官方源优先流程

新论文不应先用聚合索引猜原文。先定位作者、实验室、项目页或论文官方全文，
再通过可重复的 `--official-url` 传入。工具按以下硬顺序执行：

1. 校验并抓取所有官方 URL；任一失败则终止，不启动旁支搜索。
2. 自动补齐 OpenAlex、Crossref、arXiv，扩展引用与相关工作。
3. 每个实际执行的 LLM 阶段前运行短 PONG 健康检查。
4. 任一请求返回 HTTP 401，立即熔断客户端并终止后续 LLM 阶段。

`--extra-url` 仍是普通优先种子，不具备“全部成功否则中止”的强制语义；
新论文原文请使用 `--official-url`。

`--relevance-filter`、`--profile-iterations`、`--max-backward-rounds` 已有 CLI 参数；
`min_evidence_per_node` 等低频参数可写入 `config.yaml`。完整示例见
`docs/config.example.yaml`。

### 搜索源（`-s`，可多选）

| 源 | 名称 | Key | 适用 |
|----|------|:---:|------|
| `web` | DuckDuckGo | 免 | 通用网页 |
| `openalex` | OpenAlex | 免 | **论文首选**：2.5亿+ 全学术，自动混合"经典+最新" |
| `crossref` | Crossref | 免 | 1.5亿+ DOI，跨出版商元数据 |
| `cvpr` | CVPR / DBLP | 免 | CVPR 论文及官方开放页面 |
| `arxiv` | arXiv | 免 | 预印本论文（按相关性，偏经典） |
| `semantic_scholar` | Semantic Scholar | 可选 | 全出版商论文 + 引用数（无 key 易限流） |
| `pubmed` | PubMed | 免 | 生物医学 3700万+ 文献 |
| `wikipedia` | Wikipedia | 免 | 百科背景、人物生平（中英双站点） |
| `github` | GitHub | 可选 | 开源实现、代码、社区活跃度 |
| `google_news` | Google News | 免 | 最新进展 / 新闻 |
| `tavily` | Tavily | 需 | LLM 优化的网页搜索 |
| `opencli` | OpenCLI + Chrome | 需浏览器 | CAPTCHA/403 时的交互式浏览器兜底；默认 Google Scholar adapter |
| `bilibili` | Bilibili | 免 | B 站视频搜索 |
| `youtube` | YouTube | 免 | 公开视频与会议演讲发现 |
| `x` / `twitter` | X/Twitter | 需登录态 | 通过 `twitter-cli` 或 OpenCLI 读取公开推文搜索 |

当前网络环境下，无人值守默认路径应使用 `HTTPS_PROXY= -s tavily`。`web/openalex/
wikipedia` 若持续 CAPTCHA/403/429，不应通过无限重试拖慢任务。

OpenCLI 复用已登录 Chrome，能够通过浏览器 DOM/网络请求绕过一部分无头反爬；它要求
Node.js 20+、Browser Bridge 扩展及 `opencli doctor` 通过，因此只作为可选 fallback，
不替代 Tavily 的批量主路径。安装与验证：

```bash
npm install -g @jackwener/opencli
opencli doctor
research run "主题" --mode full -s tavily -s opencli
```

默认执行 `opencli google-scholar search`；可在 `collector.opencli_site` 改成已安装且
支持 `search` 的 adapter。不要把浏览器 cookie 写入仓库。详见
[OpenCLI 官方仓库](https://github.com/jackwener/opencli)与
[Browser Bridge 文档](https://opencli.info/docs/guide/browser-bridge.html)。

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
  clean/ 删除（raw/ 保留以便溯源），quality.json 标 `low_relevance`。实际剔除比例
  取决于主题、阈值和搜索源，应以 `quality.json` 为准。

### P2 反向传播（树状图修正）

`max_backward_rounds>0` 启用循环：一次正向完成后，organizer 评估每节点的
"来源NN" 引用数 < `min_evidence_per_node` 判为**稀疏节点**，再调一次 LLM 同时
产出稀疏补充 / 节点桥接 / 矛盾交叉验证的修正查询，追加到 raw → 清理下游 →
再跑一轮正向。最多循环 `max_backward_rounds` 次（默认 0 不启用，向后兼容）。

### 代理诊断

`collector.proxy`（或 `HTTPS_PROXY` fallback）只控制搜索与网页抓取；LLM 客户端当前
显式直连，不应把它描述成“所有请求都走代理”。运行前会对显式代理做一次脱敏 TCP
预检：端口不可达时立即失败，不会静默改成直连。

```bash
research config                       # 查看脱敏后的生效配置
HTTPS_PROXY= research run "主题" --mode full -s tavily  # 当前环境的稳定全量路径
```

长期直连请在配置中设置 `collector.proxy: null`，并删除或清空 `.env` 中失效的
`HTTPS_PROXY`。错误信息只显示协议、主机和端口，不输出用户名、密码或查询参数。

### 公平比较性能

不要把暖缓存、续跑的 `fast` 与冷启动的完整调研直接比较。可复现基准至少应固定
commit、主题、LLM/provider、网络地区、搜索源、`-n/-r` 和调研档位，并满足：

1. `pipeline.resume: false`，每次使用独立输出目录；
2. `collector.search_cache: false`，避免一天缓存影响结果；
3. 每个档位至少运行 3 次，报告中位数；
4. 同时记录 `run-summary.json` 的阶段耗时，以及 `source-audit.json` 的
   attempted/hits/filtered/deduplicated/fetch_failed/retained。

`-n` 是“每个搜索源 × 每个查询”的上限，不是最终来源数；结果还会去重并受
`collector.max_total_results` 限制。

## 从 PDF 调研（pdf2zh / MinerU 集成）

把一文件夹学术 PDF 直接综述成知识树 + 报告。PDF 经 MinerU 解析为 Markdown
（保留公式/表格/图引用），可选用 LLM 翻成中文，再走 clean→extract→organize→report。

```bash
pip install -e ".[pdf]"   # 安装 MinerU（重依赖，~7GB 含模型），或复用 pdf2zh 的 .venv

# 仅摄取：PDF 文件夹 → raw/（可选 --translate 译中文）
research ingest-pdf ./papers -T "扩散模型综述" --translate

# 扫描当前可用 OCR 引擎
research ocr-engines

# 切换 OCR 引擎：MinerU / custom / PaddleOCR-VL / Unlimited-OCR / vision-llm
research ingest-pdf ./papers -T "扩散模型综述" --ocr-engine mineru
research ingest-pdf ./papers -T "表格密集文档" --ocr-engine custom --ocr-cmd "my-ocr {pdf} {out}"
research ingest-pdf ./papers -T "视觉模型 OCR" --ocr-engine paddleocr-vl ^
  --ocr-cmd "my-paddleocr-vl --model {model} --input {pdf} --output {out}" ^
  --ocr-model-path "C:\Users\yhn\.cache\research-tool\models\PaddlePaddle__PaddleOCR-VL-1.6"

# 一键：PDF 文件夹 → 中文知识树 + 报告（collect 阶段改为 PDF 摄取）
research run "扩散模型综述" --pdf-dir ./papers --translate --skip extract
```

默认仍使用 MinerU。未把 mineru 装到全局时，用 `--mineru-cmd` 指向 pdf2zh 虚拟环境里的可执行：
`--mineru-cmd "C:\path\to\pdf2zh\.venv\Scripts\mineru.exe"`。

OCR 引擎说明：

- `mineru`：内置 MinerU CLI 调用，保持旧行为。
- `custom`：外部命令包装，命令可用 `{pdf}`、`{out}`、`{lang}`、`{model}` 占位；命令可直接 stdout 输出 Markdown，或在 `{out}` 下写 `.md/.txt`。
- `paddleocr-vl` / `unlimited-ocr`：本项目不强制安装 Paddle/Torch 重依赖，通过 `--ocr-cmd` + `--ocr-model-path` 接入本地模型包装脚本。
- `vision-llm`：为图片理解型 OCR 预留同样的命令包装入口。

已下载模型默认缓存位置：

- `C:\Users\yhn\.cache\research-tool\models\PaddlePaddle__PaddleOCR-VL-1.6`
- `C:\Users\yhn\.cache\research-tool\models\baidu__Unlimited-OCR`

### X / Twitter 渠道

默认使用 OpenCLI 复用浏览器登录态，不需要手动复制 X cookie：

```bash
opencli doctor
research collect "multimodal medical AI" -s x -n 3 --dry-run
research run "multimodal medical AI" -s x --x-backend opencli
```

OpenCLI 浏览器桥接扩展（cookie 登录态由此自动读取，**不要**手抄到 `.env`）：

```bash
# macOS 一键：CLI + 下载扩展目录
bash scripts/install_opencli_macos.sh
# 扩展目录示例：
#   ~/.cache/research-tool/opencli/opencli-extension-v1.0.22
```

```text
# Windows 示例（版本号以 releases 为准）
C:\Users\yhn\.cache\research-tool\opencli\opencli-extension-v1.0.20
```

首次使用：Chrome 打开 `chrome://extensions` → 启用 Developer mode →
`Load unpacked` 选扩展目录 → 同一浏览器登录 `x.com` → `opencli doctor`。

如果要退回 `twitter-cli`，可在配置中指定：

```yaml
collector:
  x_backend: twitter-cli
```

命令行也可直接指定：

```bash
research collect "multimodal medical AI" -s x --x-backend twitter-cli --x-cmd twitter
```

`twitter-cli` 的认证优先级是：先读环境变量 `TWITTER_AUTH_TOKEN` / `TWITTER_CT0`，
再尝试从本机浏览器自动提取 cookie。推荐先试自动提取：

```powershell
$env:TWITTER_BROWSER="chrome"          # 可选：chrome / edge / firefox / brave / arc
$env:TWITTER_CHROME_PROFILE="Default"  # 可选：也可能是 "Profile 1"
twitter status
```

如果自动提取失败，可以手动从浏览器获取 cookie：

1. 在 Chrome/Edge 登录 `https://x.com`。
2. 按 `F12` 打开 DevTools，进入 `Application` → `Cookies` → `https://x.com`。
3. 找到 `auth_token` 和 `ct0` 两行，复制它们的 `Value`。
4. 在 PowerShell 当前会话中设置：

```powershell
$env:TWITTER_AUTH_TOKEN="复制到的 auth_token"
$env:TWITTER_CT0="复制到的 ct0"
twitter status
```

需要长期保存到当前 Windows 用户环境变量时：

```powershell
[Environment]::SetEnvironmentVariable("TWITTER_AUTH_TOKEN", "复制到的 auth_token", "User")
[Environment]::SetEnvironmentVariable("TWITTER_CT0", "复制到的 ct0", "User")
```

设置后重新打开终端再运行 `twitter status`。Cookie 等同于登录凭证，不要提交到
`config.yaml`、README、issue、聊天记录或任何远程仓库；失效时重新登录 X 后再取一次。

SDK：
```python
from research_tool import ingest_pdfs, PdfIngestConfig, translate_markdown
result = await ingest_pdfs("./papers", "./out/topic",
                           PdfIngestConfig(translate=True), llm)
```

## 视频摄入（V1.1 VideoIngest）

粘贴一个 B 站 / YouTube 链接，系统自动把视频下载 → 转写 → LLM 总结成 Markdown，
无缝接入既有 4 个下游阶段（clean → extract → organize → report）。
视频笔记以 `raw/<topic>/video_<video_id>.md`
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

# MiniMax：--video-url 默认 LLM 总结器（缺 key 启动期报错；SDK 可注入 summarizer_fn 替代）
export MINIMAX_API_KEY=<your_key>
```

`[video]` extra 含 `yt-dlp>=2024.5` 和 `faster-whisper>=1.0`，**不污染核心 dependencies**。

### 一键命令

```bash
# 单视频：自动转写 + 落 raw/ + 触发 4 个下游阶段
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
    │   └── video_<video_id>.md     ← 视频笔记（落盘后下游 4 阶段自动接力）
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
| NF-1 | 视频端到端耗时 | 取决于视频长度、转写引擎、硬件和网络；以结构化日志为准 |
| NF-2 | 并发吞吐 | 3 URL 默认（`Semaphore(3)`，可由 `psutil` 自动降到 2） |
| NF-3 | 错误信息 | 3 段式（场景/原因/建议），M-010 错误码体系 |
| NF-4 | Cookie 文件权限 | 0o600（POSIX），Windows 跳过 |
| NF-5 | API Key 存储 | 仅 `.env` / sqlite，不落日志 |
| NF-6 | 依赖隔离 | `yt-dlp` / `faster-whisper` 走 `[video]` extra |
| NF-7 | 日志 | JSON Lines（含 `url_sha256` 替代原始 URL） |
| NF-8 | Deno（YouTube 必需） | 缺则 `E_DL_001_DENO_MISSING` + 安装命令 |
| NF-9 | 网络抖动 | 重试 1 次（指数退避），B 站 403 严格不重试 |
| NF-10 | 模型边界 | 视频转写/总结默认 MiniMax-M3；主管道 LLM 由 `llm` 配置决定 |

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
        run_pipeline=True,   # 落盘后自动跑 clean→extract→organize→report
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
- **LLM 抽象**：所有 Stage 依赖 `LLMClient`，不直接绑定 SDK；支持 deepseek/openai/ollama/anthropic/minimax。
- **Extractor 可选**：`--skip extract` 或 `extractor.enabled: false` 时，Organizer 直接吃 `clean/` 文本。
- **幂等恢复**：已有输出的 Stage 自动跳过（`--no-resume` 关闭）。
