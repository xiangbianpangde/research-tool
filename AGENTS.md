# research-tool — AI Agent 权威上下文协议

> **⚠️ 仅供 AI Agent 与自动化系统读取**，新会话自动加载。人类读者请参阅 `README.md`。

---

## 一、项目概述与定位

`research-tool` 是一套基于 Python 核心引擎 + 多接口层的智能深度调研系统。系统接收指定调研主题，原生驱动九段闭环管线，自主执行全网与学术多源检索、精准清洗、事实抽取、知识图谱合成、盲区检视、靶向补搜、CAS 增量合并与质量门控，最终生成具备 100% 引用核验（Citation Coverage 1.0）的高质量调研报告与知识树。

### 核心设计原则
1. **原生九段闭环**：单向驱动与局部自愈闭环无缝协同，无旧版 5/6 阶段代码残留。
2. **文件系统不可变状态通信**：Stage 之间严格通过 Protocol v1 JSON 状态信封与文件系统产物传递数据，完全解耦。
3. **确定性断点恢复（`--resume`）**：全阶段支持基于输入指纹（SHA-256）与 CAS 检查点的幂等恢复与秒级断点续跑。
4. **双模确定性身份引擎**：Rust 高性能扩展核心 + 100% 逐比特等价的纯 Python 降级兜底，兼顾高通量与零二进制环境韧性。
5. **严密质量门控与引用断言**：未核验假说物理剔除，下游无缝对接 `wiki-stage` 不可变知识库发布规范。

---

## 二、项目极简目录结构索引

```
.                                ← 项目根目录（极简标准布局）
├── .agents/                     # 多 Agent 协同元数据与状态机（Git 严格忽略）
├── .env.example                 # 环境变量配置模板
├── .gitignore                   # Git 忽略规则（严格隔离 .env、.agents/、worklogs/）
├── .pre-commit-config.yaml      # 代码规范检查与防凭证泄漏安全钩子
├── AGENTS.md                    # 本文件（AI Agent 唯一权威上下文与运行规范）
├── pyproject.toml               # 项目元数据、依赖构建与 Ruff/Pytest 配置
├── README.md                    # 官方完整架构与用户使用指南
├── uv.lock                      # 依赖版本精确锁定
├── research_tool/               # Python 核心包（5 层整洁架构 + 原生九段调度核心）
│   ├── presentation/            #   表现层 (cli.py / webui.py / setup_deployment.py)
│   ├── application/             #   应用层 (pipeline.py / video_pipeline.py / talk_linker.py)
│   ├── domain/                  #   领域层 (models.py / config.py / errors.py)
│   ├── nine_loop/               #   原生九段闭环核心调度引擎与双模 Identity 适配器
│   ├── infrastructure/          #   基础设施层 (stages/ / search/ / llm/ / ingest/ / export/)
│   └── common/                  #   公共横切工具 (logging / url_guard / slug / translate)
├── tests/                       # 全局 E2E 分层测试 (Tier 1-4) 与对抗测试 (adversarial/)
│   ├── e2e/                     #   黑盒端到端分级测试套件 (225 项测试)
│   │   ├── tier1_features/      #     Tier 1: 功能点回归验证 (F01–F20)
│   │   ├── tier2_boundaries/    #     Tier 2: 边界极端条件验证 (B01–B20)
│   │   ├── tier3_combinations/  #     Tier 3: 参数与模式两两组合矩阵验证
│   │   └── tier4_scenarios/     #     Tier 4: 真实复杂端到端场景演练
│   └── adversarial/             #   白盒对抗挑战、并发争用与网络熔断测试 (214 项测试)
├── scripts/                     # 运维与辅助脚本 (preflight / setup / proxy)
└── docs/                        # 统一文档中心
    ├── PROJECT.md               #   项目演进规范与契约白皮书
    ├── TEST_INFRA.md            #   测试基础设施规格说明
    ├── TEST_READY.md            #   测试验收与交付判定基准
    ├── STATUS.md                #   开发看板与进度状态
    ├── CHANGELOG.md             #   版本变更历史
    ├── architecture/            #   架构设计蓝图与九段闭环流程图
    ├── conventions/             #   开发、代码、测试、Git 规范全集
    ├── meta/                    #   代码图谱与重构元信息
    ├── templates/               #   标准化文档与报告模板
    ├── config.example.yaml      #   全量配置参考模板
    ├── experts.yaml             #   领域专家提示词与配置
    └── archive/                 #   [结构化归档专区] 历史交付物、审计与纪要库
        ├── README.md            #     归档专区索引与说明清单
        ├── CLAUDE.md            #     历史 CLAUDE.md 归档文件（含重定向指引）
        ├── deliverables/        #     历史里程碑交付报告与终审收束物
        ├── project_management/  #     历史项目管理审计与演进记录
        └── plans_and_notes/     #     历史实施计划 (plan/) 与技术纪要 (notes/)
```

---

## 三、原生九段闭环管线架构

### 1. 阶段流转模型
管线按序执行九个阶段，并在阶段 ⑤-⑧ 形成自主增量自愈闭环：

$$\text{① Collect} \longrightarrow \text{② Clean} \longrightarrow \text{③ Extract} \longrightarrow \text{④ Knowledge} \longrightarrow \text{⑤ Inspect} \longrightarrow \text{⑧ QGate} \overset{\text{CONTINUE}}{\rightleftarrows} \begin{pmatrix} \text{⑥ Targeted} \\ \downarrow \\ \text{⑦ Merge (CAS)} \end{pmatrix} \longrightarrow \text{⑨ Report}$$

| 阶段序号 | 阶段标识 (Canonical) | 别名 (Aliases) | 职责与技术实现 | 产物输出路径 |
|---|---|---|---|---|
| **①** | `collect` | - | 多源并发采集（支持 12+ 搜索引擎后端与本地多模态资源） | `raw/*.md` |
| **②** | `clean` | - | 正文剥离、广告清理、MinHash 近似去重、LLM 语义相关性评分（支持 `http://`, `https://`, `file://`） | `clean/*.md` |
| **③** | `extract` | - | 结构化事实陈述抽取、核心实体与证据跨度提取 | `extracted/*.json` |
| **④** | `knowledge` | `network`, `organize` | 知识图谱/网络构建，实体关联拓扑化，生成全景主表与分节点知识树 | `tree/00-主表.md`, `tree/*.md` |
| **⑤** | `inspect` | - | 知识盲区、未决证据与矛盾点深度检视，按严重度（High/Medium/Low）输出 Gap Findings | `artifacts/inspect.json` |
| **⑥** | `targeted` | - | 盲区靶向转化：自主生成解耦的精确补搜查询，发起第二轮针对性检索 | `artifacts/targeted.json` |
| **⑦** | `merge` | - | 增量证据 CAS（Compare-And-Swap）无损融合，维护版本冲突 (`conflict_version`)，不破坏前置缓存 | `artifacts/merge.json` |
| **⑧** | `qgate` | `gate` | 质量闸门评估：判定 High/Total 缺陷数是否达标，输出 `STOP_SUCCESS` 或触发自主补搜 `CONTINUE` | `artifacts/qgate.json` |
| **⑨** | `report` | - | 引用严格溯源合成：执行 Citation Coverage 1.0 断言，剔除未核验假设，交付 wiki-stage 产物 | `report.md`, `sources.json`, `run-summary.json` |

### 2. 状态机与契约规范 (Protocol v1)
- 阶段间以不可变 JSON Envelope 通信：包含 `v: 1`, `run_id`, `stage`, `idempotency_key`, `budget_lease`, `result`, `error`。
- 断点恢复：通过 `ChainState` 读取持久化检查点，输入内容哈希未变更时自动跳过已完成阶段（`skipped`）。

---

## 四、确定性双模身份引擎 (Dual-Mode Identity Engine)

位于 `research_tool/nine_loop/rt_identity_adapter.py`，负责 URL 规范化、安全防护与确定性哈希计算：

1. **双模执行机制**：
   - **Rust 引擎**：通过 `AdapterClient` 驱动编译好的 `rt-identity` 原生子进程，提供微秒级高并发 URL/Hash 规范化。
   - **Python 兜底**：若无 Rust 二进制、校验哈希不匹配或子进程异常，透明回退至 `PythonIdentityEngine`，具备 **100% 逐比特等价** 保证。
2. **安全防御规范**：
   - **SSRF 拦截**：强制封禁环回地址、私有网段（`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` 等）及 IPv6 链路本地地址。
   - **敏感与追踪参数剥离**：自动剔除 `api_key`, `token`, `secret` 等敏感字段及 `utm_*`, `gclid` 等营销追踪参数。
   - **确定性规整**：小写化 Scheme/Host、参数字典序排序、去除 URL Fragment (`#`)、统一尾部斜杠。

---

## 五、分层测试体系与工程现状

全库拥有完备的多层自动化测试体系，在 `/opt/anaconda3/envs/research-tool` 环境下保持 **1,768 项测试 100% 全绿通过**：

| 测试层级 | 目录路径 | 用例数量 | 测试范围与重点 |
|---|---|---|---|
| **单元与模块集成** | `research_tool/tests/` | **1,329 passed** | 涵盖 85 个测试模块，包括各 Stage 调度、LLM 适配、搜索后端、CAS 状态机、多模态解析等 |
| **Tier 1: 功能测试** | `tests/e2e/tier1_features/` | **100 passed** | 覆盖 20 项核心功能特性（F01–F20：原生调度、SDK、CAS 检查点、双模 Fallback、Wiki 产物等） |
| **Tier 2: 边界测试** | `tests/e2e/tier2_boundaries/` | **105 passed** | 覆盖 20 项极端边界条件（B01–B20：空字节、超长参数、恶意 SSRF 输入、状态文件损坏等） |
| **Tier 3: 组合测试** | `tests/e2e/tier3_combinations/` | **15 passed** | 针对 CLI 选项、断点续跑、多模态与纯 Python 降级等组合进行两两正交矩阵验证 |
| **Tier 4: 场景测试** | `tests/e2e/tier4_scenarios/` | **5 passed** | 5 大真实复杂业务场景演练（学术文献全流程、视频研读增量融合、离线灾难恢复等） |
| **对抗加固测试** | `tests/adversarial/` | **214 passed** | 涵盖并发写争用、网络熔断重试、CAS 冲突检测、极限压力挑战等白盒破坏性测试 |
| **总计** | 全库全量 | **1,768 passed** | **0 failed, 0 errors, 0 flaky；Ruff 校验 0 报错（Exit code 0）** |

---

## 六、⚠️ AI Agent 运行与使用核心守则（强制执行）

所有访问本仓库的 AI Agent 必须严格遵守以下守则，违反守则的行为将被打回：

1. **严禁调研缩水（No Cognitive Shortcuts / No `--mode brief`）**：
   - 当用户要求调研或执行测试任务时，**必须执行完整的原生九段闭环管线**。
   - **绝对禁止使用 `--mode brief`** 或跳过抽取/组织阶段！正式调研一律使用默认模式或 `--mode full`，确保完整的事实抽取、检视补搜与引用验证。
2. **零工作区修改约束（Zero Workspace Mutation on Research）**：
   - Agent 在使用 `research-tool` 开展调研时，**绝对禁止修改项目源码（`.py` 文件）或生产配置文件（`config.yaml`、`pyproject.toml` 等）**。
   - 运行时参数调整必须通过 CLI 选项或临时环境变量注入，禁止在项目根目录生成临时执行脚本（如 `temp_test.py`）。
3. **严格产物隔离（Output Isolation）**：
   - 调研产物（`raw/`, `clean/`, `extracted/`, `tree/`, `artifacts/`, `report.md` 等）必须输出到 `--output` 指定的隔离目录（如 `./research-output/`），严禁污染 Git 跟踪区。
4. **清洗质量保障（Clean Before Extract）**：
   - 严禁未经清洗的超大原始数据直接冲入后续 LLM 抽取阶段。必须确保 MinHash 去重、硬截断（`max_content_length: 50000`）与 LLM 相关性过滤生效。
5. **引用覆盖率 1.0 断言（Citation Coverage 1.0）**：
   - 最终合成的报告必须严格保持引用覆盖率 1.0，所有论断必须有确凿证据跨度（Evidence Span）支持，未核验推测必须物理剥离。

---

## 七、常见开发任务速查表

| 用户需求 | Agent 推荐行动路径 | 验证命令 |
|---|---|---|
| **执行正式调研** | 使用 CLI 运行完整闭环：`python -m research_tool.presentation.cli run "<topic>" -s tavily --output ./research-output` | 检查 `research-output/report.md` 及 `run-summary.json` |
| **断点恢复调试** | 追加 `--resume` 选项重新执行同一主题：`python -m research_tool.presentation.cli run "<topic>" --resume --output ./research-output` | 观察控制台各 Stage 显示 `[skipped] 已有输出` |
| **修复代码 Bug** | 定位目标文件并进行最小改动，严禁破坏已建立的分层边界与九段状态机契约 | 运行对应测试及全量测试：`/opt/anaconda3/envs/research-tool/bin/pytest -q` |
| **扩展搜索源** | 在 `research_tool/infrastructure/search/` 继承 `SearchBackend` 并注册到工厂 | 运行 `pytest research_tool/tests/test_search_backends.py` |
| **调整配置字段** | 修改 `domain/models.py` 对应 Config 类，同步更新 `docs/config.example.yaml` | 运行 `pytest research_tool/tests/test_config.py` |
| **静态代码检查** | 修改任意 Python 代码后必须确保通过 Ruff 检查 | `/Users/xbpd/.local/bin/ruff check research_tool/ tests/ pyproject.toml` |

---

## 八、规范遵守与架构分层状态

```
research_tool/presentation/   表现层   ← cli.py / webui.py
        ↓ 单向依赖
research_tool/application/    应用层   ← pipeline.py / video_pipeline.py / talk_linker.py
        ↓
research_tool/domain/         领域层   ← models.py / config.py / errors.py
        ↓
research_tool/nine_loop/      核心调度 ← chain_state.py / rt_identity_adapter.py / 各 *_min.py
        ↓
research_tool/infrastructure/ 基础设施 ← stages/ / llm/ / search/ / ingest/
        ↑
research_tool/common/         公共工具 ← logging_config.py / slug.py / translate.py / url_guard.py（横切层）
```

**已知有意架构简化**：
1. `presentation/cli.py` 中的 `_make_llm()` 直接构造 LLMClient（CLI 薄封装权衡）。
2. `infrastructure/ingest/pipeline_adapter.py` 函数内懒加载 `ResearchPipeline`（解耦视频摄入与应用层启动顺序）。
3. `common/translate.py` 引用 `infrastructure.llm.base.LLMClient`（复用 LLM 客户端抽象）。

**当前状态**：
- **版本**: 0.1.1
- **测试状态**: 1,768 passed / 0 failed
- **Python 环境**: Python >= 3.11 (`/opt/anaconda3/envs/research-tool`)
- **LLM 支持**: 5+ Provider（火山引擎 Ark / DeepSeek / OpenAI / Anthropic / Ollama / MiniMax）
- **搜索源**: 12+ 后端（DDG / OpenAlex / Crossref / arXiv / S2 / PubMed / Wikipedia / GitHub / GoogleNews / Tavily / Bilibili / X）
