# research-tool

<p align="center">
  <strong>智能深度调研工具与知识引擎</strong><br>
  给定任意主题、论文、本地文档或视频 → 自动执行多源检索、反偏差深挖、结构化提炼与闭环质检 → 产出高可信知识树与专业调研报告。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Pipeline-v1.0.0%20Nine--Loop%20Default-blueviolet" alt="Nine-Loop Pipeline">
  <img src="https://img.shields.io/badge/Tests-850+%20Passed-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/Rust%20Core-rt--identity-DEA584?logo=rust&logoColor=white" alt="Rust Core">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

---

## 📑 目录

- [🌟 核心亮点](#-核心亮点)
- [🏗️ 架构与管线流程](#️-架构与管线流程)
  - [v1.0.0 九段闭环管线（默认路径）](#v100-九段闭环管线默认路径)
  - [确定性 Rust 身份核心与模型降级链](#确定性-rust-身份核心与模型降级链)
  - [Legacy 六阶段管线（Kill-Switch 回退）](#legacy-六阶段管线kill-switch-回退)
- [🚀 快速开始与部署](#-快速开始与部署)
  - [交互式部署向导（推荐）](#交互式部署向导推荐)
  - [部署档位说明](#部署档位说明)
  - [手动安装（开发者）](#手动安装开发者)
- [⚙️ 配置指南](#️-配置指南)
  - [环境变量与密钥管理](#环境变量与密钥管理)
  - [YAML 核心配置](#yaml-核心配置)
- [🖥️ 使用指南](#️-使用指南)
  - [1. CLI 命令行界面](#1-cli-命令行界面)
  - [2. Gradio 可视化界面](#2-gradio-可视化界面)
  - [3. Python 异步 SDK](#3-python-异步-sdk)
- [🔍 深度特性与方法论](#-深度特性与方法论)
  - [15+ 搜索后端与学术优先机制](#15-搜索后端与学术优先机制)
  - [② Clean 清洗工程与 NearDup 近似去重](#-clean-清洗工程与-neardup-近似去重)
  - [两阶段锚定 / 去锚（突破检索偏差）](#两阶段锚定--去锚突破检索偏差)
  - [Deepen 反偏差深挖与时间线回溯](#deepen-反偏差深挖与时间线回溯)
  - [多模态 PDF 综述（MinerU / OCR / 自动翻译）](#多模态-pdf-综述mineru--ocr--自动翻译)
  - [视频结构化研读（VideoIngest V1.1）](#视频结构化研读videoingest-v11)
  - [知识库与 Wiki 导出（wiki-stage）](#知识库与-wiki-导出wiki-stage)
- [🛡️ 可靠性与安全工程](#️-可靠性与安全工程)
- [📋 错误码与排障速查](#-错误码与排障速查)
- [🛠️ 开发与贡献](#️-开发与贡献)

---

## 🌟 核心亮点

- 🔄 **九段闭环调研管线（Nine-Loop）**：从搜集、清洗、抽取、知识整合、检视、定向补搜、合并、质检闸门到报告合成，全流程自主闭环迭代。
- 🦀 **确定性 Rust 身份核心（`rt-identity`）**：URL 规范化、稳定 Hash 与内容去重下沉至 Rust 子进程，性能提升 **10.8×**，内存消耗降低 **8.3×**。
- 🌐 **15+ 多源学术与全网搜索**：原生集成 OpenAlex（2.5亿+学术文献）、Crossref、arXiv（HTML5 全文 + PDF 回退）、Semantic Scholar、PubMed、DuckDuckGo、Tavily、Google News、GitHub、Wikipedia、X/Twitter、Bilibili、YouTube 及 OpenCLI 浏览器兜底。
- 🧠 **反偏差深挖（Deepen）与反向自愈**：通过实体拆分、画像注入（英文名去锚）、时间线回溯、同名消歧突破单一锚点偏差；支持按知识树稀疏度自动触发反向补充检索。
- 📄 **多模态文献综述（PDF Ingest）**：集成 MinerU 与可插拔 OCR 引擎（PaddleOCR-VL / Unlimited-OCR / Vision-LLM），公式/图表/引用完整保留并支持全篇中文翻译。
- 🎬 **多模态视频研读（VideoIngest V1.1）**：B 站 / YouTube 视频自动下载、`faster-whisper` / Groq 语音转写、MiniMax / LLM 章节提炼，无缝汇入下游知识树。
- 📦 **不可变知识包与 Wiki 发布**：提供内容寻址不可变研究包（`wiki-stage`）与 Obsidian Staging Vault 对接机制。
- 🛡️ **生产级安全性与可恢复性**：全阶段基于文件系统通信，断点幂等秒级恢复（`--resume`）；内置严格的 SSRF 防护与脱敏日志体系。

---

## 🏗️ 架构与管线流程

### v1.0.0 九段闭环管线（默认路径）

自 `v1.0.0` 起，**九段闭环管线（Nine-Loop）成为默认执行路径**（渐进重构程序 RT-RF-2026 交付，经类生产回滚与全链路 Canary 验证，与顶级 Deep Research 系统配对评测交付完成率达 100% vs 80.5%）。

![research-tool 九段闭环](collect/architecture/system/nine-stage-loop.png)

九段闭环执行流如下：
1. **① 收集 (Collect)**：多源学术与全网检索、arXiv HTML5 全文解析、PDF/视频摄取与原始资产（`raw/_originals/`）归档。
2. **② 清洗 (Clean)**：`raw/` 保持只读；去除 HTML/广告噪声并定位正文；MinHash（char-5gram Jaccard 0.85）近似去重；按文件记录清洗指标（`clean/quality.json`）；支持增量 delta 清洗。
3. **③ 事实抽取 (Extract)**：确定性规则与 LLM 并发抽取实体、关系与三元组，Evidence Span 边界强锚定。
4. **④ 知识网络 (Knowledge)**：实体/关系拓扑构建、Same-Bytes 等价边计算与 Family 节点聚合。
5. **⑤ 缺口/矛盾 (Inspect)**：深度反思检视，规则引擎扫描证据空白、时间线断层与事实冲突。
6. **⑥ 针对性补搜 (Targeted)**：针对检视发现的缺口与矛盾生成精准补搜请求（零外网安全断言，仅处理新增定向资料）。
7. **⑦ 增量合并 (Merge)**：CAS (Write-if-match) 幂等合并、带出处新事实融合与同名消歧。
8. **⑧ 质量与预算门 (QGate)**：确定性决策树仲裁（质量达标或预算耗尽：否 → 回 ⑤ 循环补搜，是 → 进入 ⑨）。
9. **⑨ 核验式报告 (Report)**：严格引用断言（Citation Coverage 1.0），未引用事实自动丢弃，多风格专业组装。

| 阶段 | 核心职责 | 产出物 |
|---|---|---|
| **① Collect** | 15+ 搜索源聚合、arXiv HTML5 全文解析、PDF/视频多模态摄取、原始资产归档 | `raw/*.md` + `raw/_originals/` + `sources.json` |
| **② Clean** | 去除 HTML 结构噪声与广告，MinHash 相似度去重（Jaccard），LLM 语义相关性评分过滤 | `clean/*.md` + `quality.json` |
| **③ Extract** | 并发提取实体（NER）、关系与三元组（Triples），动态 Schema 归纳 | `extracted/*.json` |
| **④ Knowledge** | 聚合多源证据，构建多层级网络与知识骨架 | `knowledge/*.json` |
| **⑤ Inspect** | 深度反思：检视证据空白点、时间线断层与事实冲突 | `inspect/gap_analysis.json` |
| **⑥ Targeted** | 针对 Inspect 发现的缺口与矛盾，生成高精度靶向查询并补采 | `targeted/*.md` |
| **⑦ Merge** | 实体对齐、同名消歧与增量知识图谱融合 | `tree/00-主表.md` + `tree/N*.md` |
| **⑧ Gate** | 节点置信度裁决，核查最小证据支撑数（`min_evidence_per_node`） | `gate/verification.json` |
| **⑨ Report** | 多风格专业报告合成（综述报告 / 可行性分析 / 述评 / 长文） | `report.md` + `run-summary.json` |

### 确定性 Rust 身份核心与模型降级链

- **Rust 身份核心（`rt-identity`）**：负责 Canonical URL 解析、Stable ID 生成与 Content Hash 校验，以子进程形式提供高并发确定性支撑。
- **弹性模型链**：默认支持 Primary → Fallback 故障转移降级链（如 `DeepSeek-V4-Flash` → `MiniMax-M3`），保障长时间运行任务的高韧性。

### Legacy 六阶段管线（Kill-Switch 回退）

系统保留了经充分验证的 Legacy 六阶段管线作为安全回退路径。当配置 `nine_loop.enabled: false` 时，管线将无缝回退至六阶段执行（逐字节等同）：

```
Topic ─→ [Collect] ─→ [Deepen] ─→ [Clean] ─→ [Extract] ─→ [Organize] ─→ [Report] ─→ report.md
           raw/        raw/++      clean/     extracted/     tree/          │
             ▲                                                              │
             │  Backward Propagation（反向传播循环，可选）                  │
             └────────── 评估知识树质量 → 生成修正查询 → 重采 ◀─────────────┘
```

---

## 🚀 快速开始与部署

### 交互式部署向导（推荐）

项目提供统一的跨平台交互式部署向导，涵盖依赖安装、密钥配置与端到端 Preflight 验收：

```bash
# 1. 克隆仓库并进入目录
git clone https://github.com/xiangbianpangde/research-tool.git
cd research-tool

# 2. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. 安装基础 CLI 入口
pip install -e ".[llm,search]"

# 4. 启动交互式部署向导
research setup
# 或通过脚本启动：
# macOS / Linux: bash scripts/setup.sh
# Windows: scripts\start.bat
```

向导将自动引导完成：
1. **选择部署档位**（`minimal` / `recommended` / `full`）。
2. **自动化依赖安装与校验**（执行 `pip check` 及浏览器组件安装）。
3. **安全配置密钥**（使用隐藏输入，自动写入 `0600` 权限的 `.env` 文件）。
4. **Preflight 端到端自检**（LLM PONG 探测、搜索连通性、本地代理及音视频工具验证）。
5. **签发无密钥部署回执**（`.research-deployment.json`）。

### 部署档位说明

| 档位 | 目标场景 | 安装 Extras | 包含能力 |
|---|---|---|---|
| `minimal` | 轻量快速验证 | `[llm,search]` | CLI 核心、LLM 客户端（OpenAI/DeepSeek/Anthropic/MiniMax/Ollama）、DuckDuckGo & Tavily 搜索 |
| `recommended` | 推荐完整体验 | `[llm,search,ui,crawl]` | `minimal` 全部能力 + Gradio Web UI + Crawl4AI 深度网页抓取与浏览器渲染 |
| `full` | 全功能全模态 | `[all]` | `recommended` 全部能力 + MinerU PDF 摄取 + VideoIngest（yt-dlp、faster-whisper） + OCR 体系（需系统具备 ffmpeg、Deno） |

### 常用向导命令

```bash
research setup                         # 进入交互式主菜单
research setup --profile full          # 直接按完整档安装并配置
research setup --profile recommended   # 按推荐档安装
research setup --secrets-only          # 仅更新 API Key 与代理，不重装依赖
research setup --github-only           # 仅更新 GITHUB_TOKEN
research setup --tavily-only           # 仅更新 TAVILY_API_KEY
research setup --check-only            # 仅执行 Preflight 验收自检
```

### 手动安装（开发者）

```bash
pip install -e ".[all]"         # 安装全部 Python 依赖
crawl4ai-setup                  # 初始化 Crawl4AI 浏览器内核（若包含 crawl）
research setup --check-only     # 运行预检确认环境
```

---

## ⚙️ 配置指南

系统遵循清晰的配置优先级：**CLI 命令行参数 > 环境变量（`.env`） > `config.yaml` > 默认值**。

### 环境变量与密钥管理

复制根目录模板生成本地配置文件：

```bash
cp .env.example .env
```

核心环境变量说明：

```bash
# === 主 LLM 客户端 ===
LLM_PROVIDER=deepseek                    # deepseek | openai | anthropic | ollama | minimax
LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=sk-xxxx
LLM_BASE_URL=https://api.deepseek.com/v1

# === 搜索增强密钥（可选但推荐） ===
TAVILY_API_KEY=tvly-xxxx                 # Tavily 搜索 API（高准确率推荐）
GITHUB_TOKEN=ghp_xxxx                    # GitHub 检索（Classic PAT，免特殊 scope）
S2_API_KEY=xxxx                          # Semantic Scholar 学术检索配额提升
OPENALEX_MAILTO=your@email.com           # OpenAlex/Crossref Polite Pool 邮箱

# === 视频摄入（VideoIngest） ===
MINIMAX_API_KEY=xxxx                     # 视频总结默认引擎（与主 LLM 独立隔离）
GROQ_API_KEY=gsk_xxxx                    # 可选：Groq 云端高速 Whisper 转写

# === 网络代理（仅控制搜索与网页抓取） ===
# 国内环境访问海外搜索源建议配置；海外/直连环境请保持留空
HTTPS_PROXY=http://127.0.0.1:7890
```

### YAML 核心配置

复制 `docs/config.example.yaml` 为 `config.yaml`，可通过 `${VAR}` 语法无缝引用环境变量：

```yaml
# config.yaml 核心片段
llm:
  provider: deepseek
  model: deepseek-chat
  api_key: ${DEEPSEEK_API_KEY}
  temperature: 0.3
  request_timeout_sec: 180

collector:
  search_engines:
    - tavily
    - openalex
    - arxiv
  max_results_per_engine: 8
  depth: 2
  save_originals: true          # 归档原始 HTML / PDF 字节至 raw/_originals/
  arxiv_fulltext: true          # 优先抓取 arXiv HTML5 全文，无则 PDF 回退

pipeline:
  mode: full                    # brief (快速) | full (完整)
  work_dir: ./research-output
  resume: true                  # 启用幂等断点续跑
```

查看脱敏后的生效配置：

```bash
research config
```

---

## 🖥️ 使用指南

### 1. CLI 命令行界面

#### 一键全流程调研（`research run`）

```bash
# 1. 基础调研（默认九段管线，使用 Tavily 搜索引擎）
research run "具身智能多模态大模型进展" -s tavily

# 2. 简报模式（brief：快速产出核心报告，减少 LLM 开销）
research run "Agentic AI 架构演进" --mode brief -s tavily

# 3. 完整调研（full：强制完整产物生成与阶段校验）
research run "固态电池商业化现状" --mode full -s tavily -s web

# 4. 新学术论文精准综述（官方源强制优先 + 自动扩展引用与相关工作）
research run "DeepSeek-V3 Technical Report" \
  --official-url "https://github.com/deepseek-ai/DeepSeek-V3" \
  --official-url "https://arxiv.org/abs/2412.19437"

# 5. 人物与跨机构调研（两阶段锚定 + 去锚 + 画像迭代）
research run "中南民族大学 康怡琳" --core "康怡琳" --facets "博士,论文,南洋理工" --profile-iterations 2

# 6. 限定时间窗口检索（学术发展脉络）
research collect "Graph Neural Networks" --from-year 2022 --to-year 2026 -s openalex -s arxiv

# 7. 矩阵式深搜（多排序策略 × 多页翻页）
research collect "量子纠错编码" --deep-search --deep-pages 3 --deep-sorts "relevance,date,citations" -s openalex
```

#### 单阶段独立调试与断点执行

```bash
# 阶段1：仅搜集并查看结果（dry-run 不抓取正文）
research collect "Diffusion Policy" -s arxiv -s openalex --dry-run

# 阶段2：对 raw/ 目录执行清洗与去噪
research clean ./research-output/diffusion_policy/raw

# 阶段3：抽取实体与三元组
research extract ./research-output/diffusion_policy/clean

# 阶段4：构建多节点知识树
research organize ./research-output/diffusion_policy/extracted

# 阶段5：合成定制风格报告（report / feasibility / review / article）
research report ./research-output/diffusion_policy/tree --style review

# 查看任务阶段完成状态
research status ./research-output/diffusion_policy
```

#### 多模态与扩展命令

```bash
# 本地 PDF 批量摄取与综述（支持 OCR 与中文翻译）
research ingest-pdf ./papers/ -T "大模型长上下文技术" --translate
research run "大模型长上下文技术" --pdf-dir ./papers/ --translate

# 扫描本机可用 OCR 引擎
research ocr-engines

# 视频摄入与研读（B站 / YouTube）
research run "Transformer 原理解析" --video-url "https://www.bilibili.com/video/BV1xx411c7mD"

# 生成不可变知识包（供 Wiki/Obsidian 使用）
research wiki-stage ./research-output/diffusion_policy --build
```

### 2. Gradio 可视化界面

部署 `recommended` 或 `full` 档位后，启动交互式 Web 界面：

```bash
research ui --port 7861
```

- 🌐 **Web 调研面板**：支持输入主题、配置搜索源、选择产物版本（`brief` / `full`）、实时查看九段执行状态与流式日志。
- 📄 **PDF 研读面板**：支持批量上传论文 PDF、选择 OCR 引擎与翻译选项、一键生成中文知识树与综述。
- 📊 **产物管理**：在线浏览知识树节点（`00-主表.md`、`N*.md`）、阅读 Markdown 报告并打包下载。

### 3. Python 异步 SDK

可以在自己的 Python 项目中直接调用核心引擎：

#### 快速单任务调研

```python
import asyncio
from research_tool import research

async def main():
    result = await research(
        topic="具身智能感知与规划",
        llm_provider="deepseek",
        search_engines=["tavily", "openalex"],
        mode="full"
    )
    print(f"调研完成！报告路径: {result.report_result.report_path}")
    print(f"总耗时: {result.elapsed_sec:.2f} 秒")

asyncio.run(main())
```

#### 流式事件监听与自定义编排

```python
import asyncio
from research_tool import ResearchPipeline, load_config

async def main():
    cfg = load_config("config.yaml", overrides={"mode": "full"})
    pipeline = ResearchPipeline(cfg)
    
    async for event in pipeline.stream("神经符号 AI 前沿"):
        print(f"[{event.status.upper()}] 阶段: {event.stage} - {event.message}")

asyncio.run(main())
```

#### 批量视频摄入与转写

```python
import asyncio
from research_tool.application.video_pipeline import process_videos

async def main():
    report = await process_videos(
        topic="Sora 核心技术解析",
        urls=[
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ],
        work_dir="./research-output",
        run_pipeline=True  # 视频转写总结后自动接力后续清洗与报告阶段
    )
    print(f"成功: {report.success_count}, 失败: {report.failed_count}")

asyncio.run(main())
```

---

## 🔍 深度特性与方法论

### 15+ 搜索后端与学术优先机制

| 类别 | 标识 (`-s`) | 数据源 | 鉴权说明 | 特性与适用场景 |
|---|---|---|:---:|---|
| **学术核心** | `openalex` | OpenAlex | 免 (可选邮箱) | **论文首选**：2.5亿+文献，自动平衡经典高引与近期发表 |
| | `arxiv` | arXiv | 免 | 预印本首选：支持 HTML5 全文解析与 PDF 备选抓取 |
| | `crossref` | Crossref | 免 (可选邮箱) | 1.5亿+ DOI 元数据覆盖，出版商交叉索引 |
| | `semantic_scholar`| S2 | 可选 Key | 权威学术图谱，无 Key 易触发限流 |
| | `pubmed` | PubMed | 免 | 3700万+ 生物医学与生命科学文献 |
| | `cvpr` | CVPR / DBLP | 免 | 计算机视觉顶级会议论文精确定位 |
| **通用与新闻**| `tavily` | Tavily | **需 API Key**| LLM 深度优化搜索，高信噪比网页提取 |
| | `web` | DuckDuckGo | 免 | 通用网页搜索（无追踪） |
| | `google_news`| Google News | 免 | 实时新闻与最新突发进展 |
| | `wikipedia` | 维基百科 | 免 | 百科定义、学术脉络与实体背景（中英双语） |
| **代码与社交**| `github` | GitHub | 可选 Token | 开源项目、核心算法实现与社区热度（支持代码级检索） |
| | `x` / `twitter`| X (Twitter) | 需登录态 | 前沿观点与推文讨论（通过 OpenCLI 复用浏览器登录态） |
| | `bilibili` | Bilibili | 免 | B 站技术演讲、视频教程发现 |
| | `youtube` | YouTube | 免 | 全球学术会议 Talk 与技术视频发现 |
| **浏览器兜底**| `opencli` | OpenCLI + Chrome | 需本地扩展 | 遇反爬/验证码时复用本地 Chrome 会话进行交互式采集 |

> 💡 **学术调研建议**：针对新论文，强烈推荐使用 `--official-url` 传入实验室项目页或官方 PDF。系统将强制优先验证并抓取官方页面，确认无误后再通过 OpenAlex 和 arXiv 进行网状引用扩展。

### ② Clean 清洗工程与 NearDup 近似去重

依据 `clean/architecture/` 规范，清洗阶段在 `raw/` 摄入后立即执行，严格将传输级 raw 转为高信噪比的可抽取正文：

- **输入/输出公开合同**：`CleanRequest(raw_dir, delta_manifest?, policy)` → `CleanResult(clean_dir, kept[], dropped[], quality.json)`。
- **`raw/` 保持只读存证**：清洗阶段只读 `raw/` 资料，绝对不修改或删除任何原始抓取文件。
- **TextNormalizer 降噪流水线**：去 HTML 标签、脚本、导航与广告代码，智能识别正文起始点并截尾，在 `quality.json` 中完整记录每步 `dropped_chars`。
- **NearDup 近似去重**：基于 `char-5gram Jaccard` 相似度（默认阈值 `0.85`）构建指纹索引；相似文档组内保留最长有效正文，其余标记 `dedup_of` 剔除。
- **增量 Delta 清洗支持**：在补搜或增量运行（`delta_manifest`）时，仅对新增 raw 执行清洗，保留历史 clean 编号与指纹索引，实现增量幂等合并。
- **LLM 语义相关性评分（可选）**：配置 `cleaner.relevance_filter: true` 时，批量对文档进行 0-1 语义相关性打分，低分文档移出 `clean/` 并记录在 `quality.json`。

### 两阶段锚定 / 去锚（突破检索偏差）

在进行特定人物、学者或细分交叉主题调研时，直接搜索复合主题（如 `"中南民族大学 康怡琳"`）容易导致检索结果被单一历史机构或限定词严重绑架。

- **Phase 1（锚定）**：使用完整输入查询确定实体核心画像。
- **Phase 2（去锚）**：通过 `--core "康怡琳"` 与 `--facets "博士,论文,南洋理工"`，自动剥离早期机构限定，生成高维度去锚查询，全景式捕获其博士阶段、海外任职及最新科研成果。

### Deepen 反偏差深挖与时间线回溯

Deepen 阶段内置多重认知对抗机制：
- **实体拆分**：将多实体复杂主题拆解为独立子实体进行全方位视角搜索。
- **结构化画像提取**：从初步搜集内容中抽取中英文名、机构变迁与核心领域（由于国际学术数据库中文人名索引受限，**英文名画像注入**是突破学术壁垒的关键）。
- **时间线回溯（Timeline Backtracking）**：按履历阶段自动施加年份约束进行回溯式文献检索。
- **同名消歧（Disambiguation）**：基于所属机构与领域置信度判定内容归属，疑似非同人资料自动归档至 `raw/_disambig/`，杜绝信息污染。

### 多模态 PDF 综述（MinerU / OCR / 自动翻译）

提供将批量学术 PDF 一键合成为中文知识体系的端到端方案：

```
PDF 文件夹 ─→ MinerU / PaddleOCR-VL ─→ Markdown (保留公式/表格) ─→ [LLM 中文翻译] ─→ raw/
                                                                                     │
                       report.md ◀─ tree/ ◀─ extracted/ ◀─ clean/ ◀──────────────────┘
```

- **OCR 引擎切换**：内置 `mineru`、`paddleocr-vl`、`unlimited-ocr`、`custom` 与 `vision-llm`。
- **公式与版面保留**：完整提取 LaTeX 数学公式、多列排版与图表引用。
- **智能翻译流**：支持并发将英文学术文档翻译为地道学术中文 Markdown。

### 视频结构化研读（VideoIngest V1.1）

针对 Bilibili 与 YouTube 视频，提供音视频智能提取与知识转化能力：

- **白名单安全过滤**：原生支持 `bilibili.com`、`b23.tv`、`youtube.com`、`youtu.be`（非白名单秒级安全拦截）。
- **音轨转写引擎**：本地优先使用 `faster-whisper`（支持 GPU/CPU 自动加速），云端支持 `Groq` 极速转写。
- **结构化笔记提炼**：由独立配置的 MiniMax / LLM 生成包含时间戳章节、核心要点、板书截图索引的标准化 Markdown（统一 `video_` 前缀元数据）。
- **自适应并发调度**：内置 `Semaphore(3)` 资源池，结合 `psutil` 动态探测内存状态，防止本地转写 OOM。

### 知识库与 Wiki 导出（wiki-stage）

支持将调研产物一键打包并无缝桥接至个人知识库（Obsidian 等）：

```bash
# 生成内容寻址不可变研究包
research wiki-stage ./research-output/transformer --build
```

- **内容寻址与防篡改**：基于包内容生成全局唯一 Hash 与元数据清单。
- **安全沙箱**：严禁直接覆写活跃 Vault，输出至受控 Draft 隔离区；自动化敏感凭证与绝对路径脱敏。

---

## 🛡️ 可靠性与安全工程

- 🔒 **严格 SSRF 防护（`url_guard`）**：全面拦截指向 `127.0.0.1`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`169.254.169.254`（云元数据）等私有 IP 范围的恶意抓取与重定向请求。
- 📂 **文件系统状态原子性**：每个阶段仅在完全成功后向 `.stage-complete/<stage>.json` 写入原子完成标记，断点续跑可靠无歧义。
- 🛡️ **敏感信息脱敏**：CLI `research config`、日志输出及错误报告中自动对 API Key、密码及敏感 URL 参数执行脱敏（`***`）。
- 🚦 **网络与代理预检**：启动前对显式配置的代理执行 TCP 端口活性探测，端口不可达时立即报错拦截，杜绝静默假死。

---

## 📋 错误码与排障速查

系统采用三段式结构化错误报告（场景 / 原因 / 建议解决方案）：

| 错误代码 | 场景分类 | 常见原因 | 推荐解决方案 |
|---|---|---|---|
| `E_VID_URL_REJECTED` | 视频摄入 | 输入了非 B 站/YouTube 平台的链接 | 检查 URL，确保属于受支持平台白名单 |
| `E_DL_001_DENO_MISSING` | 依赖缺失 | YouTube 视频下载缺少 Deno 环境 | 安装 Deno 2.0+（`curl -fsSL https://deno.land/install.sh \| sh`） |
| `E_DL_BILI_403` | 鉴权失败 | B 站视频要求会员或登录态访问 | 通过 `--cookie-file` 注入包含 `SESSDATA` 的 Cookie |
| `E_TX_001_WHISPER_INIT_FAILED`| 转写错误 | `faster-whisper` 模型加载失败 | 运行 `pip install faster-whisper` 或调小模型规格 |
| `E_AUTH_401` | LLM 调用 | API Key 无效或已欠费 | 运行 `research setup --secrets-only` 更新对应 Key |
| `E_PROXY_UNREACHABLE` | 网络连接 | 本地配置的 HTTP/SOCKS 代理未启动 | 启动代理客户端，或在 `.env` 中清空 `HTTPS_PROXY` |
| `E_PIPE_DISK_FULL` | 磁盘/IO | 磁盘可用空间低于 100MB | 清理存储空间后继续任务 |

---

## 🛠️ 开发与贡献

### 架构分层规范

项目严格遵循单向依赖的 5 层整洁架构：

```
research_tool/
├── presentation/     # 表现层：Typer CLI (cli.py) 与 Gradio Web UI (webui.py)
├── application/      # 应用层：管线编排 (pipeline.py / video_pipeline.py)
├── domain/           # 领域层：Pydantic 数据模型 (models.py)、配置与错误码体系
├── infrastructure/   # 基础设施层：各阶段实现 (stages/)、LLM (llm/)、搜索 (search/)、摄取 (ingest/)
└── common/           # 横切公共层：日志规范、中文 Slug、SSRF 防护 (url_guard.py)
```

### 运行测试与代码质检

```bash
# 激活开发环境
source .venv/bin/activate

# 运行完整测试套件（850+ 测试用例）
pytest

# 执行代码风格与规范检查（Ruff）
ruff check research_tool/
```

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
