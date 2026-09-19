# research-tool · 项目整体分析

> **审计日期**：2026-07-21 / **版本**：0.1.1 / **Python**：≥3.11
> **关联**：`project_management/reports/file_audit.md` / `architecture_review.md`
> **DoD**：4 章节齐全 + 真实路径引用 ≥ 20 处 ✅

---

## 1. 项目用途

**问题域**：自动调研工具 — 给定主题，端到端产出「知识树 + 调研报告」，替代人工搜索/阅读/整理的链条。

- **定位语**：`pyproject.toml:8` — "一个 Python 核心引擎 + 多接口层的调研工具：给定主题 → 产出知识树/报告"
- **目标用户**：`README.md:25-38` 描述 Windows 一键启动 → Gradio Web UI → 命令行菜单（页面调研 / PDF 调研 / 状态查看）；面向"非开发者 + 高级 CLI 用户"双层
- **典型场景**：主题 → 6 阶段管道 → `report.md`；新论文调研 `README.md:120-122`；视频摄取 V1.1（bilibili / YouTube / X）
- **来源证据**：`STATUS.md:5-15`（11 个 FP）、`CLAUDE.md:9-11`、`README.md:1-13`

---

## 2. 技术栈

| 维度 | 选型 | 证据 |
|---|---|---|
| Language | Python ≥ 3.11（target py311） | `pyproject.toml:10,80` |
| Build | hatchling + uv（uv.lock 898 KB） | `pyproject.toml:1-3` |
| CLI | Typer 0.12 + Rich 13.7 | `pyproject.toml:16-17` |
| LLM SDK | openai ≥ 1.0 + anthropic ≥ 0.40 | `pyproject.toml:25` |
| HTTP | httpx ≥ 0.27 + defusedxml | `pyproject.toml:15,20` |
| 数据模型 | Pydantic ≥ 2.6 | `pyproject.toml:13` |
| 搜索可选 | ddgs ≥ 6.0、tavily-python ≥ 0.5 | `pyproject.toml:29` |
| Web UI | Gradio ≥ 4.0 | `pyproject.toml:33` |
| 重依赖（隔离 extras） | crawl4ai / mineru / yt-dlp / faster-whisper | `pyproject.toml:23-49`（按 NFR1 隔离到 optional） |
| Test | pytest ≥ 8.0 + pytest-asyncio ≥ 0.23 | `pyproject.toml:50` |
| Lint/Format | ruff（pre-commit + 全规则 E/F/N/T20/T100/S/PLR0915） | `pyproject.toml:78-103` |
| Secret scan | gitleaks（pre-commit + GitHub Action） | `.pre-commit-config.yaml:10-12` |
| 部署 | hatch wheel + 多档向导（minimal/recommended/full） | `pyproject.toml:52-59` |
| 数据库 | 无（文件系统通信） | — |
| 持久化例外 | SQLite 仅视频转写缓存 | `infrastructure/ingest/cache_manager.py:96-257` |

---

## 3. 当前架构

### 3.1 分层架构图（5 层 + 公共工具）

```
research_tool/
├── presentation/                  ← CLI (cli.py:45 typer.Typer) + WebUI (webui.py)
│      └── 已知偏差: cli.py:33 + webui.py:78 直引 infrastructure
├── application/                   ← pipeline.py / video_pipeline.py / talk_linker.py
│      └── 编排 6 阶段，跨阶段仅文件系统
├── domain/                        ← models.py / config.py / errors.py
├── infrastructure/                ← stages/ llm/ search/ ingest/ export/ experts/
└── common/                        ← logging_config slug translate url_guard
       └── 已知偏差: translate.py 引用 infrastructure.llm.base
```

### 3.2 数据流图（6 阶段 + talk + backward）

```
[topic] ──CLI/WebUI──▶ Collect ──▶ raw/*.md + sources.json
                            ▲                │
                          (Talk 旁路：video_pipeline.py 注入) 
                            │                ▼
                          Deepen ──▶ raw/*.md + _disambig/ + .deepen_done
                                            │
                                            ▼
                       Clean ──▶ clean/*.md + quality.json
                                            │
                                            ▼
                       Extract ──▶ extracted/*.json
                                            │
                                            ▼
                       Organize ──▶ tree/00-主表.md + N*.md
                                            │
                                            ▼
                       Report ──▶ report.md (+ wiki_publisher.publish → wiki)
                                            ▲
                                            │
                       Backward (optional, max_backward_rounds>0):
                       evaluate(tree) → 修正查询 ──┘ (回 Collect)
```

证据：`README.md:5-23`、`STATUS.md:5-15`、`research_tool/application/pipeline.py`、`research_tool/infrastructure/stages/{collector,deepen,cleaner,extractor,organizer,reporter}.py`、`research_tool/infrastructure/llm/base.py:128-142`（provider 路由）。

### 3.3 调用关系（粗粒度）

```
cli:757 run() ──▶ application.pipeline.ResearchPipeline
                  └─▶ stages.collector → search.get_backend (search/__init__.py:76)
                  └─▶ llm.base.LLMClient.from_config (base.py:129)
                  └─▶ stages.{deepen,cleaner,extractor,organizer,reporter}
cli:239 collect / cli:433 clean / cli:457 extract / cli:495 organize / cli:520 report  ── 单阶段直调
cli:1182 setup ──▶ presentation.setup_deployment
webui:1157 ──▶ Gradio 渲染 + 调 application 层
```

---

## 4. 关键决策索引（ADR + 已知有意偏差）

| ID | 决策 | 路径 | 备注 |
|---|---|---|---|
| ADR-0001 | 六阶段管道文件系统通信 | `worklogs/decisions/0001-六阶段管道文件系统通信.md` | 解耦 Stage，可中断/恢复/独立调试 |
| ADR-0002 | rotate-and-scrub-leaked-deepseek-key | `worklogs/decisions/0002-rotate-and-scrub-leaked-deepseek-key.md` | 密钥轮换 + 落地 `gitleaks CI` |
| ADR-0003 | supersede-m006-deepseekclient | `worklogs/decisions/0003-supersede-m006-deepseekclient.md` | DeepseekClient 移除，统一归到 OpenAILLMClient（base.py:134） |
| 偏差-1 | `cli.py:33` presentation→infrastructure 直引 | `research_tool/presentation/cli.py:33-36` | `_make_llm` + stages 简化封装 |
| 偏差-2 | `webui.py:78` presentation→infrastructure 直引 | `research_tool/presentation/webui.py`（`CLAUDE.md:84` 提及） | 同上，WebUI 薄封装 |
| 偏差-3 | `pipeline_adapter.py` 基础设施→应用层懒上引 | `research_tool/infrastructure/ingest/pipeline_adapter.py` | 规避视频摄入导入时序 |
| 偏差-4 | `common/translate.py` 公共层→基础设施 | `research_tool/common/translate.py` | 复用 LLM 抽象 |

---

## 5. 矛盾点（DoD 必含）

| 维度 | 文档 A | 文档 B | 实测 | 备注 |
|---|---|---|---|---|
| 测试数 | `STATUS.md:15` 454 passed | `CLAUDE.md:42` 417 passed / `CLAUDE.md:88` 454 passed | **840 collected**（`pytest --collect-only -q` 验证轮实测） | 840 是权威值；STATUS/CLAUDE 均过期 |
| CLI 命令数 | `STATUS.md:8` 11 个 | — | **14** 个 `@app.command`（验证轮实测：collect/ingest-pdf/ocr-engines/clean/extract/organize/report/run/wiki-stage/publish-wiki/status/ui/config/setup） | STATUS 漏列新增的 wiki-stage/publish-wiki/setup/config |
| 搜索后端数 | `STATUS.md:6` 12 个 / 14 名（含 scholar/x 别名） | `CLAUDE.md:35` 12 个后端 | 18 个后端实现文件 | 实际 18 backend 文件但仅 14 名字 + 4 别名/工具；`README.md:17` 写"15 个后端 + 2 别名"——四处说法各异 |

---

## 6. 项目一句话总结

research-tool 是一个 **基于多 LLM 的自动化深度研究流水线**（Python 核心引擎 + 多接口层），通过 6 阶段文件管道（Collect→Deepen→Clean→Extract→Organize→Report，附加可选 Backward/Talk）将"主题"转化为可阅读的知识树与报告，并已扩展支持 PDF 摄取、视频摄取与 Wiki 发布。