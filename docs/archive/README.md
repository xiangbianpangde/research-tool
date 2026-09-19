# research-tool 文档归档库 (Documentation Archive)

> **⚠️ 历史文档专区 (READ-ONLY ARCHIVE)**  
> 本目录及全部子目录下的文档均为 `research-tool` 历史演进阶段的归档记录，已于 **2026-09-19** 封存归档。  
> **活跃文档与项目最新规范请参阅 [`docs/`](../README.md) 及根目录权威文件 [`AGENTS.md`](../../AGENTS.md)。**

---

## 1. 归档背景 (Background)

随着 `research-tool` 项目完成全面重构与技术跃迁，生产主调度管线已彻底废弃旧版 5/6 阶段架构，正式确立以**原生九段闭环架构**（① Collect → ② Clean → ③ Extract → ④ Knowledge → ⑤ Inspect → ⑥ Targeted → ⑦ Merge → ⑧ QGate → ⑨ Report）为唯一的执行引擎，并建立了双模确定性 Identity 引擎（Rust 核心 + 纯 Python 回退）以及覆盖全链路的 1,760+ 项分层自动化测试集。

为避免历史阶段设计、旧版项目管理过程记录与过往会话纪要对当前系统规范造成语义污染与维护干扰（Documentation Drift），按照 R2/R3 文档治理规范，特设立本归档库：
1. **解除双份权威与认知漂移**：原根目录 `CLAUDE.md` 迁移至根目录权威文件 `AGENTS.md`，旧文件封存入档；
2. **沉淀历史过程资产**：将 2026-05 至 2026-08 期间的阶段交付物、项目管理审查报告、V1.1 方案演化文档与重构问答集中封存；
3. **隔离私有工作日志**：确保历史管理目录下的本地工作日志（`worklog/`）继续受 `.gitignore` 保护，杜绝敏感记录意外入库。

---

## 2. 归档清单与目录树 (Archive Catalog)

```
docs/archive/
├── README.md                      # 本归档索引自述（目录分类、背景与治理规范）
├── CLAUDE.md                      # 历史根目录 AI 上下文契约（迁移前版本，带头部归档警示）
├── deliverables/                  # 历史阶段交付物与收束产物
│   ├── deliverable.md             #   Track-Foundation 交付报告（横切基础层 6 模块，2026-06）
│   ├── deliverable-track-core.md  #   Track-Core 交付报告（核心能力层 3 模块，2026-06）
│   ├── deliverable-track-integration.md # Track-Integration 交付报告（集成入口层 3 模块，2026-06）
│   ├── 收束报告-v0.1.1.md          #   项目首次全基线收束报告（2026-05）
│   └── 项目交接展示.html           #   历史 v0.1.0 架构可视化交接大屏（2026-05）
├── project_management/            # 历史项目管理、度量与技术债审计专区（2026-07）
│   ├── validation_report.md       #   项目有效性验证报告
│   ├── decisions/                 #   历史架构决策与审计方案
│   │   ├── 2026-07-21-audit-verification-decisions.md
│   │   └── 2026-07-21_phase-audit-plan.md
│   ├── experiments/               #   历史实验目录与记录
│   │   └── catalog.md
│   ├── innovation/                #   创新点清单与技术预研
│   │   └── catalog.md
│   ├── planning/                  #   历史任务分工、风险登记册、审计范围
│   │   ├── agent_assignment.md
│   │   ├── audit_scope.md
│   │   ├── risk_register.md
│   │   └── task_plan.md
│   ├── problems/                  #   代码质量审计与历史技术债记录
│   │   ├── code_quality.md
│   │   └── technical_debt.md
│   ├── reports/                   #   历史架构审查、代码熵管理、演进路线、全景展示
│   │   ├── architecture_review.md
│   │   ├── entropy_management.md
│   │   ├── file_audit.md
│   │   ├── final_report.html
│   │   ├── final_structure.md
│   │   ├── future_roadmap.md
│   │   ├── github_growth_analysis.md
│   │   ├── project_overview.md
│   │   ├── star_growth_roadmap.md
│   │   └── verify_report.md
│   ├── scripts/                   #   历史运维与债务修复辅助脚本
│   │   ├── commit_untracked.sh
│   │   └── fix_p0_debts.py
│   ├── status/                    #   历史开发状态与事实核验表
│   │   ├── current_status.md
│   │   └── project_facts.md
│   └── worklog/                   #   本地审计工作日志（受 .gitignore 隔离，不入库）
│       └── 2026-07-21-audit-verification.md
└── plans_and_notes/               # 历史开发实施计划与过往技术纪要
    ├── plan/                      #   历史版本开发方案（共 155 个文件）
    │   ├── EXEC-ExpertLib-CVPRTalk-20260711.md # 专家库与 CVPR 视频执行方案
    │   ├── HANDOFF.md             #   历史阶段交接说明
    │   ├── research-tool后续优化计划-CVPR视频与专家库-20260708.md # 优化专项计划
    │   └── 后续升级计划/          #   V1.1 VideoIngest 8 阶段完整设计规范（152 个设计文件）
    │       ├── 00-索引.md
    │       ├── 01-需求澄清/       #     需求记录、PRD 与追溯矩阵
    │       ├── 02-调研验证/       #     技术调研、假设验证与参考源
    │       ├── 03-逻辑梳理/       #     业务流程与逻辑分解
    │       ├── 04-整体结构设计/   #     系统拓扑、架构决策 (ADR) 与数据流
    │       ├── 05-技术架构设计/   #     API 设计与安全规范
    │       ├── 06-详细设计/       #     接口契约 (IC) 与模块详细设计
    │       ├── 07-文件框架/       #     各模块框架脚手架与单元测试
    │       ├── 08-系统模拟运行/   #     端到端时序仿真与异常演练
    │       └── _流水线执行报告.md
    └── notes/                     #   过往重构纪要与调研记录
        └── refactor-qa-2026-08.md #   企业级重构流程与现状分析问答记录
```

---

## 3. 分区详细内容说明 (Detailed Section Breakdown)

### 3.1 历史 Agent 上下文 (`CLAUDE.md`)
- **文件路径**：`docs/archive/CLAUDE.md`
- **原文件路径**：`CLAUDE.md`（项目根目录）
- **内容概述**：在迁移至根目录权威规范 `AGENTS.md` 之前，作为 AI Agent 交互的入口上下文文件。记录了旧版目录结构、六阶段与早期九段闭环转换期的上下文信息。文件头部已追加重定向警示，引导所有智能体转为遵循根目录 `AGENTS.md`。

### 3.2 历史交付物与阶段收束 (`deliverables/`)
- **文件路径**：`docs/archive/deliverables/`
- **原文件路径**：
  - `docs/deliverable.md`
  - `docs/deliverable-track-core.md`
  - `docs/deliverable-track-integration.md`
  - `docs/reports/收束报告-v0.1.1.md`
  - `docs/reports/项目交接展示.html`
- **内容概述**：
  - `deliverable.md`：2026-06 实施的 VideoIngest V1.1 基础横切层 6 模块（M-002 预检、M-004 缓存、M-006 LLM 客户端、M-009 ffmpeg 封装、M-010 错误处理、M-011 结构化日志）交付与测试验证记录；
  - `deliverable-track-core.md`：核心能力层 3 模块（M-003 下载器、M-005 转写器、M-007 笔记结构）交付与测试验证记录；
  - `deliverable-track-integration.md`：集成入口层 3 模块（M-001 CLI 绑定、M-008 管道适配器、M-012 并发编排）交付与测试验证记录；
  - `收束报告-v0.1.1.md`：项目初版基线建立时的整理、测试与红线审计全景报告；
  - `项目交接展示.html`：早期的交互式项目交接与全链路架构展示大屏。

### 3.3 历史项目管理与技术债审计 (`project_management/`)
- **文件路径**：`docs/archive/project_management/`
- **原文件路径**：`docs/project_management/`（整体归档，共 26 个文件）
- **内容概述**：记录 2026-07 期间针对历史遗留系统开展的工程审计、代码复杂度评估、熵增控制、GitHub 增长分析与技术债务分析；
- **安全与隐私保障**：子目录 `worklog/` 下的本地工作日志受项目根目录 `.gitignore`（`**/worklog/` 规则）严格拦截，保持本地保留、绝不跟踪提交。

### 3.4 历史计划与阶段纪要 (`plans_and_notes/`)
- **文件路径**：`docs/archive/plans_and_notes/`
- **原文件路径**：
  - `docs/plan/`（整体归档至 `plans_and_notes/plan/`，共 155 个文件）
  - `docs/notes/`（整体归档至 `plans_and_notes/notes/`，共 1 个文件）
- **内容概述**：
  - `plan/`：包含 2026-06 至 2026-07 期间 VideoIngest V1.1 的全生命周期设计规范（01 需求澄清、02 调研验证、03 逻辑梳理、04 整体结构设计、05 技术架构设计、06 详细设计、07 文件框架、08 系统模拟运行），以及专家库（ExpertLib）与 CVPR 研读专项优化方案；
  - `notes/`：`refactor-qa-2026-08.md` 记录了针对企业级项目开发流程、架构约束梳理与技术债治理的长篇深度问答纪要。

---

## 4. 治理与访问规范 (Governance & Maintenance Policy)

1. **只读不可变 (Read-Only & Immutable)**：
   本归档专区内的所有文件均为历史既定事实的冷存封存记录。除全局引用路径修复外，禁止在归档目录内新增、篡改或重新激活业务代码与实施方案。
2. **单向依赖 (Unidirectional Dependency)**：
   项目活跃源码（`research_tool/`）与活跃文档中心（`docs/`）不得对 `docs/archive/` 产生硬编码依赖。若需在活跃文档中提及过往设计，须明确注明为历史参考（如 `[历史方案参考](archive/plans_and_notes/...)`）。
3. **工作日志隔离 (Worklog Isolation Enforcement)**：
   归档后的 `project_management/worklog/` 严格受 `.gitignore` 规则屏蔽。执行 Git 操作时严禁强制添加（`git add -f`）任何属于 `worklog/` 的文件。
