# [ARCHIVED] research-tool — 历史 AI 项目上下文 (CLAUDE.md)

> ⚠️ **已废弃并归档 (DEPRECATED & ARCHIVED)**
> 
> - **归档日期**：2026-09-19
> - **迁移说明**：本项目已全面升级 AI Agent 协议体系。根据架构治理决策（Requirement R1），AI Agent 权威项目上下文与治理规范已统一提升并迁移至项目根目录的 **[`AGENTS.md`](../../AGENTS.md)**。
> - **重定向指引**：所有 AI 协作工具（Claude、Cursor、Codex、Gemini、Antigravity 等）及自动化脚本请立即转向阅读根目录 **`AGENTS.md`**。
> - **存档状态**：本文件作为历史治理快照永久归档于 `docs/archive/CLAUDE.md`，不再随代码演进而更新。

---

*(以下为归档的历史原文内容)*

# research-tool — AI 项目上下文

> **⚠️ 仅供 AI Agent 读取**，新会话自动加载。人类请读 `README.md`。

---

## 项目概述

Python 核心引擎 + 多接口层的智能深度调研工具：给定主题 → 自动产出知识树 / 调研报告。
原生九段闭环管线（Collect → Clean → Extract → Knowledge → Inspect → Targeted → Merge → QGate → Report），
Stage 之间严格通过文件系统不可变数据通信，断点幂等可恢复、可自愈补搜、可独立调试。

---

## 目录索引

```
.                                ← 项目根（极简规范根目录）
├── .env.example                 # 环境变量配置模板
├── .gitignore                   # Git 忽略规则（严格屏蔽 .env、.agents/、worklogs/）
├── .pre-commit-config.yaml      # Pre-commit 代码规范与防凭证泄漏检查
├── CLAUDE.md                    # 本文件（AI 项目上下文）
├── pyproject.toml               # 项目元数据、依赖构建与 Ruff/Pytest 规则
├── README.md                    # 官方完整架构与使用指南
├── uv.lock                      # 依赖版本锁定
├── research_tool/               # Python 核心包（5 层整洁架构 + 原生 9 段闭环引擎）
│   ├── presentation/            #   表现层 (cli.py / webui.py / setup_deployment.py)
│   ├── application/             #   应用层 (pipeline.py / video_pipeline.py / talk_linker.py)
│   ├── domain/                  #   领域层 (models.py / config.py / errors.py)
│   ├── nine_loop/               #   原生 9 阶段闭环核心调度引擎
│   ├── infrastructure/          #   基础设施层 (stages/ / search/ / llm/ / ingest/ / export/)
│   └── common/                  #   公共横切工具 (logging / url_guard / slug)
├── tests/                       # 全局 E2E 分层测试 (Tier 1-4) 与对抗测试 (adversarial/)
│   ├── e2e/                     #   分级端到端测试体系
│   └── adversarial/             #   真实 API 熔断、网络瞬断与对抗压力挑战
├── scripts/                     # 运维与辅助脚本 (setup / smoke / proxy)
└── docs/                        # 统一文档中心
    ├── PROJECT.md               #   项目演进规范与契约白皮书
    ├── TEST_INFRA.md            #   测试基础设施规格
    ├── TEST_READY.md            #   测试验收与交付判定报告
    ├── STATUS.md                #   开发状态与进度看板
    ├── CHANGELOG.md             #   版本变更历史
    ├── AGENTS.md                #   多 Agent 协作规则与上下文
    ├── architecture/            #   架构设计蓝图与九段闭环流程图
    ├── conventions/             #   开发、代码、测试、Git 规范全集
    ├── meta/                    #   代码图谱与重构笔记
    ├── project_management/      #   项目管理审计报告与演进追踪
    ├── plan/                    #   各阶段开发实施计划
    ├── reports/                 #   收束报告与交付物
    └── templates/               #   各类规范文档模板
```

---

## 架构分层（严格 01-架构规范）

```
research_tool/presentation/   表现层   ← cli.py / webui.py
        ↓ 单向依赖
research_tool/application/    应用层   ← pipeline.py / video_pipeline.py
        ↓
research_tool/domain/         领域层   ← models.py / config.py / errors.py
        ↓
research_tool/infrastructure/ 基础设施 ← stages/ / llm/ / search/ / ingest/
        ↑
research_tool/common/         公共工具 ← logging_config.py / slug.py / translate.py / url_guard.py（横切，各层均可引用）
```

**已知偏差**（3 处有意简化，详见下方规范表 01-架构）：

- `presentation/cli.py` 的 `_make_llm()` 直接构造 LLMClient（表现层→基础设施）——CLI 薄封装简化；管道自身通过 `load_config` + `ResearchPipeline` 正确分层。
- `infrastructure/ingest/pipeline_adapter.py` 在函数内懒加载 `application.pipeline.ResearchPipeline`（基础设施→应用层上引）——避免视频摄入硬依赖应用层导入时序。
- `common/translate.py` 引用 `infrastructure.llm.base.LLMClient`（公共层→基础设施）——翻译工具复用 LLM 抽象的权衡。

---

## 规范遵守状态（2026-05 收束节点；2026-07 持续优化更新）

| 规范 | 状态 | 备注 |
|------|------|------|
| 01-架构 | 🟡 | 分层总体 OK；3 处有意简化：`cli._make_llm` 跨层、`ingest/pipeline_adapter` 懒上引 application、`common/translate` 引用 infra/llm。P3-1 待扩展：`cli.py:33`、`webui.py:78` 的 presentation→infrastructure 导入 |
| 02-代码 | ✅ | print()→logging 完成；配置 ruff；异常加 as exc+日志 |
| 03-Git | 🟡 | gitleaks CI 已生效；commitlint 配置已就位（`meta/commitlint.config.js`），pre-commit/CI 强制待落地 |
| 04-API | N/A | 无 HTTP API（CLI + Gradio 本地工具） |
| 05-测试 | ✅ | 454 passed / 5 skipped；FP09 反向循环已覆盖（R11，test_pipeline_backward.py，8 用例）；覆盖率待测（pytest-cov 待安装） |
| 06-文档 | ✅ | README + CLAUDE.md + STATUS.md；本轮对齐代码现状；docstring 覆盖公共 API |
| 08-图谱 | N/A | 中小项目，未达启用门槛（< 5 万行, < 10 模块） |

---

## 常见任务速查

| 用户说 | AI 做什么 |
|--------|----------|
| "修复 bug" | 先跑相关测试确认重现 → 修改 → pytest 全绿 |
| "添加搜索源" | 参考 `search/` 下现有后端模式：继承 SearchBackend → 注册到 `__init__.py` 工厂 |
| "新增 Stage" | 参考 `stages/` 下模式：类 + 模块级便捷函数 → `__init__.py` 注册 |
| "修改配置" | 改 `models.py` 对应 Config → 同步 `config.example.yaml` → 测试 |
| "重构" | 先读本文件了解架构 → 修改 → pytest + ruff check 全绿 |

---

## 当前状态

- **版本**: 0.1.1
- **测试**: 454 passed / 5 skipped
- **Python**: >=3.11
- **LLM**: 5 provider（DeepSeek / OpenAI / Anthropic / Ollama / MiniMax）；`from_config` 把 openai/deepseek/ollama/minimax 路由到 `OpenAILLMClient`（OpenAI 兼容），anthropic 走 `AnthropicLLMClient`
- **搜索源**: 12 个后端（DDG/OpenAlex/Crossref/arXiv/S2/PubMed/Wikipedia/GitHub/GoogleNews/Tavily/Bilibili/X）；`SearchEngine` Literal 含 14 名（`scholar`→arxiv、`x`/`twitter`→x_backend 为别名）
- **错误码（M-010）**: 26 个 VideoIngest 错误码，全功能（3 段式 format_error + 仲裁退出码 resolve_exit_code；R9 CLI 接线 + R10 注册遗留码 + 对齐 register/raise）
- **反向循环（FP09）**: 已覆盖（R11，test_pipeline_backward.py，8 用例）
