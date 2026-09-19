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
├── README.md / AGENTS.md / STATUS.md
└── AGENTS.md                 # 本文件（给 AI 读）
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
| 06-文档 | ✅ | README + AGENTS.md + STATUS.md；本轮对齐代码现状；docstring 覆盖公共 API |
| 08-图谱 | N/A | 中小项目，未达启用门槛（< 5 万行, < 10 模块） |

---

## 常见任务速查

| 用户说 | AI 做什么 |
|--------|----------|
| "修复 bug" | 先跑相关测试确认重现 → 修改 → pytest 全绿 |
| "执行调研" / "调研xxx" | 使用完整管线 `python -m research_tool.presentation.cli run "<topic>" -s tavily`；严禁使用 `--mode brief`，严禁修改工作区任何源码与配置文件，产物必须输出到指定输出目录 |
| "添加搜索源" | 参考 `search/` 下现有后端模式：继承 SearchBackend → 注册到 `__init__.py` 工厂 |
| "新增 Stage" | 参考 `stages/` 下模式：类 + 模块级便捷函数 → `__init__.py` 注册 |
| "修改配置" | 改 `models.py` 对应 Config → 同步 `config.example.yaml` → 测试 |
| "重构" | 先读本文件了解架构 → 修改 → pytest + ruff check 全绿 |

---

## ⚠️ AI Agent 运行与使用核心守则（强制执行）

1. **严禁调研偷懒缩水（No Cognitive Shortcuts / No `--mode brief`）**：
   - 当用户要求调研或测试调研工具时，**必须执行完整的五阶段/九段闭环管线**（`collect → clean → extract → organize → report`）。
   - **绝对禁止使用 `--mode brief`** 或 `--skip extract` / `--skip organize`！`brief` 模式只会跑 `collect-clean-report`，直接跳过了事实抽取与知识树构建，破坏报告的真实证据链。正式任务一律使用默认模式或 `--mode full`。
2. **零工作区修改约束（Zero Workspace Mutation on Research）**：
   - Agent 在使用 `research-tool` 开展调研时，**绝对禁止修改项目源码（`.py` 文件）或任何配置文件（`config.yaml`、`.env`、`pyproject.toml` 等）**。
   - 所有运行时定制参数（包括 `-s <source>`、`--model <model>`、`--output <dir>`、`--core <keyword>`、`--facets <tags>` 等）**必须全部通过 CLI 命令行选项或临时环境变量注入**，严禁通过直接 `edit`/`write` 代码或配置文件来达到目的。
   - 严禁在项目根目录下生成任何临时运行脚本（如 `run_test.py`、`demo.sh` 等）。
3. **严格产物隔离（Output Isolation）**：
   - 调研产生的所有原始素材、清洗文件、抽取结果和最终报告，必须严格限制在 `--output` 指定的隔离目录（如 `./research-output/`，已在 `.gitignore`），绝不能污染 Git 工作树。
4. **清洗质量保障（Clean Before Extract）**：
   - 必须确保 `clean` 阶段生效，先执行广告/导航/cookie/Base64大图剥离、长度硬截断（`max_content_length: 50000`）、MinHash 近似去重及 LLM 语义相关性评分（`relevance_filter: true`），严禁未清洗的超大脏数据冲入后续 LLM 抽取阶段。

---

## 当前状态

- **版本**: 0.1.1
- **测试**: 454 passed / 5 skipped
- **Python**: >=3.11
- **LLM**: 5 provider（DeepSeek / OpenAI / Anthropic / Ollama / MiniMax）；`from_config` 把 openai/deepseek/ollama/minimax 路由到 `OpenAILLMClient`（OpenAI 兼容），anthropic 走 `AnthropicLLMClient`
- **搜索源**: 12 个后端（DDG/OpenAlex/Crossref/arXiv/S2/PubMed/Wikipedia/GitHub/GoogleNews/Tavily/Bilibili/X）；`SearchEngine` Literal 含 14 名（`scholar`→arxiv、`x`/`twitter`→x_backend 为别名）
- **错误码（M-010）**: 26 个 VideoIngest 错误码，全功能（3 段式 format_error + 仲裁退出码 resolve_exit_code；R9 CLI 接线 + R10 注册遗留码 + 对齐 register/raise）
- **反向循环（FP09）**: 已覆盖（R11，test_pipeline_backward.py，8 用例）
