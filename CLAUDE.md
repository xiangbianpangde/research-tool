# research-tool — AI 项目上下文

> **⚠️ 仅供 AI Agent 读取**，新会话自动加载。人类请读 `README.md`。

---

## 项目概述

Python 核心引擎 + 多接口层的调研工具：给定主题 → 自动产出知识树 / 调研报告。
六阶段管道（Collect → Deepen → Clean → Extract → Organize → Report），
Stage 之间仅通过文件系统通信，可中断、可恢复、可独立调试。

---

## 目录索引

```
.                                ← 项目根（即工作区根）
├── research_tool/               # Python 包（按 01-架构 五层组织）
│   ├── __init__.py              #   公开 API 入口
│   ├── presentation/            #   表现层
│   │   ├── cli.py               #     Typer CLI（11 命令）
│   │   └── webui.py             #     Gradio Web 界面
│   ├── application/             #   应用层
│   │   ├── pipeline.py          #     ResearchPipeline 编排
│   │   ├── video_pipeline.py    #     V1.1 VideoIngest 编排
│   │   └── video_concurrent_orchestrator.py  # 视频并发调度
│   ├── domain/                  #   领域层
│   │   ├── models.py            #     30+ Pydantic 模型
│   │   ├── config.py            #     YAML 配置加载
│   │   └── errors.py            #     7 异常类 + V1.1 错误码体系
│   ├── infrastructure/          #   基础设施层
│   │   ├── stages/              #     6 个 Stage 实现
│   │   ├── llm/                 #     LLM 客户端抽象（5 provider）
│   │   ├── search/              #     12 个搜索后端
│   │   └── ingest/              #     PDF/视频摄取（9 模块）
│   └── common/                  #   公共工具
│       ├── logging_config.py    #     统一日志
│       ├── slug.py              #     中文→文件名
│       ├── translate.py         #     Markdown 翻译
│       └── url_guard.py         #     SSRF 防护
├── research_tool/tests/         # pytest（417 passed / 5 skipped）
├── docs/                        # 文档资产
│   ├── reports/                 #   收束报告 + 交接展示
│   ├── plan/                    #   项目计划
│   └── templates/               #   模板
├── meta/                        # 元信息（CODE_MAP）
├── worklogs/                    # 工作日志 + decisions/
│   └── decisions/               #   ADR（收束节点产出）
├── pyproject.toml               # 项目元数据 + ruff
├── .pre-commit-config.yaml
├── README.md / CLAUDE.md / STATUS.md
└── CLAUDE.md                 # 本文件（给 AI 读）
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
| 01-架构 | 🟡 | 分层总体 OK；3 处有意简化：`cli._make_llm` 跨层、`ingest/pipeline_adapter` 懒上引 application、`common/translate` 引用 infra/llm |
| 02-代码 | ✅ | print()→logging 完成；配置 ruff；异常加 as exc+日志 |
| 03-Git | 🟡 | gitleaks CI 已生效；Conventional Commits 钩子/CI 强制待落地（`meta/commitlint.config.js` 已就位） |
| 04-API | N/A | 无 HTTP API（CLI + Gradio 本地工具） |
| 05-测试 | ✅ | 417 passed / 5 skipped；覆盖率待测（pytest-cov 待安装） |
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
- **测试**: 417 passed / 5 skipped
- **Python**: >=3.11
- **LLM**: 5 provider（DeepSeek / OpenAI / Anthropic / Ollama / MiniMax）；`from_config` 把 openai/deepseek/ollama/minimax 路由到 `OpenAILLMClient`（OpenAI 兼容），anthropic 走 `AnthropicLLMClient`
- **搜索源**: 12 个后端（DDG/OpenAlex/Crossref/arXiv/S2/PubMed/Wikipedia/GitHub/GoogleNews/Tavily/Bilibili/X）；`SearchEngine` Literal 含 14 名（`scholar`→arxiv、`x`/`twitter`→x_backend 为别名）
