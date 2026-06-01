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
.                              ← 项目根（即工作区根）
├── src/                       # Python 包（按 01-架构 五层组织）
│   ├── __init__.py            #   公开 API 入口
│   ├── presentation/          #   表现层
│   │   ├── cli.py             #     Typer CLI（7 命令）
│   │   └── webui.py           #     Gradio Web 界面
│   ├── application/           #   应用层
│   │   └── pipeline.py        #     ResearchPipeline 编排
│   ├── domain/                #   领域层
│   │   ├── models.py          #     30+ Pydantic 模型
│   │   ├── config.py          #     YAML 配置加载
│   │   └── errors.py          #     6 异常类
│   ├── infrastructure/        #   基础设施层
│   │   ├── stages/            #     6 个 Stage 实现
│   │   ├── llm/               #     LLM 客户端抽象
│   │   ├── search/            #     10 个搜索后端
│   │   └── ingest/            #     PDF 摄取
│   └── common/                #   公共工具
│       ├── logging_config.py  #     统一日志
│       ├── slug.py            #     中文→文件名
│       └── translate.py       #     Markdown 翻译
├── tests/                     # pytest（92 用例）
├── docs/                      # 文档资产
│   ├── reports/               #   收束报告 + 交接展示
│   ├── plan/                  #   项目计划
│   ├── specs/                 #   BDD 规格
│   ├── templates/             #   模板
│   └── research/              #   调研
├── meta/                      # 元信息（FILE_GRAPH, CODE_MAP）
├── worklogs/                  # 工作日志 + decisions/
│   └── decisions/             #   ADR（收束节点产出）
├── pyproject.toml             # 项目元数据 + ruff
├── .pre-commit-config.yaml
├── README.md / CLAUDE.md / STATUS.md
└── CLAUDE.md               # 本文件（给 AI 读）
```

---

## 架构分层（严格 01-架构规范）

```
src/presentation/        表现层   ← cli.py / webui.py
        ↓ 单向依赖
src/application/         应用层   ← pipeline.py
        ↓
src/domain/              领域层   ← models.py / config.py / errors.py
        ↓
src/infrastructure/      基础设施 ← stages/ / llm/ / search/ / ingest/
        ↑
src/common/              公共工具 ← logging_config.py / slug.py / translate.py（横切，各层均可引用）
```

**已知偏差**：`presentation/cli.py` 的 `_make_llm()` 直接构造 LLMClient（表现层→基础设施），
这是 CLI 薄封装的有意简化——管道自身通过 `load_config` + `ResearchPipeline` 正确分层。

---

## 规范遵守状态（2026-05 收束节点）

| 规范 | 状态 | 备注 |
|------|------|------|
| 01-架构 | 🟡 | 分层总体 OK；`_make_llm()` 跨层调用为有意简化 |
| 02-代码 | ✅ | print()→logging 完成；配置 ruff；异常加 as exc+日志 |
| 03-Git | 🟡 | Conventional Commits 待 CI 落地 |
| 04-API | N/A | 无 HTTP API（CLI + Gradio 本地工具） |
| 05-测试 | ✅ | 92 用例全绿；覆盖率待测（pytest-cov 待安装） |
| 06-文档 | ✅ | README + CLAUDE.md；docstring 覆盖公共 API |
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

- **版本**: 0.1.0
- **测试**: 92/92 通过
- **Python**: >=3.11
- **LLM**: DeepSeek / OpenAI / Anthropic / Ollama
- **搜索源**: 10 个（DDG/OpenAlex/Crossref/arXiv/S2/PubMed/Wikipedia/GitHub/GoogleNews/Tavily）
