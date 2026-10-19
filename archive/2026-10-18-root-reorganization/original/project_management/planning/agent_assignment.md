# research-tool · Agent 调度策略（Agent Assignment）

> **创建日期**：2026-07-21
> **关联**：`planning/task_plan.md` / `decisions/2026-07-21_phase-audit-plan.md`

---

## 调度原则

1. Phase 0 串行完成后，Phase 1-7 可最大化并行
2. 每个 Agent 输出 ≤ 8k token，超则拆多轮
3. 所有 Agent 必须留下结构化记录（背景/方法/发现/建议）
4. Agent 可创建子 Agent，但必须在 worklog 登记

---

## Agent 分工

### Agent 1: 项目结构分析

| 维度 | 内容 |
|------|------|
| 负责 Phase | Phase 1 + Phase 2 |
| 输入 | `project_facts.md` + 目录树 + `pyproject.toml` |
| 产出 | `reports/project_overview.md` + `reports/file_audit.md` |
| 工具 | find / wc / grep / git log |
| 子 Agent | 无 |
| 状态 | DONE（2026-07-21 首轮） |

### Agent 2: 代码质量审查

| 维度 | 内容 |
|------|------|
| 负责 Phase | Phase 3 |
| 输入 | `research_tool/` 全部源码 |
| 产出 | `problems/code_quality.md` |
| 工具 | ruff check / grep（except pass / noqa / TODO）/ wc |
| 子 Agent | 可拆：CS-Agent（Code Smell）+ BR-Agent（Bug Risk） |
| 状态 | DONE（2026-07-21 首轮）+ 验证轮补全 ruff 数据 |

### Agent 3: 架构分析

| 维度 | 内容 |
|------|------|
| 负责 Phase | Phase 4 + Phase 5 |
| 输入 | Agent 1 产出 + `research_tool/` import 拓扑 |
| 产出 | `reports/architecture_review.md` + `problems/technical_debt.md` |
| 工具 | grep（import / from）/ LSP（goToDefinition / findReferences） |
| 子 Agent | 可拆：Import-Agent（依赖图）+ Debt-Agent（债务评级） |
| 状态 | DONE（2026-07-21 首轮） |

### Agent 4: 创新点分析

| 维度 | 内容 |
|------|------|
| 负责 Phase | Phase 6 |
| 输入 | Agent 3 架构分析 + 竞品对比 |
| 产出 | `innovation/catalog.md` + `experiments/catalog.md` + `validation_report.md` |
| 工具 | 代码阅读 + 文献对比 + WebSearch（竞品） |
| 子 Agent | Experiment-Agent（实验设计） |
| 状态 | DONE（2026-07-21 首轮） |
| 约束 | 实验允许 UNVERIFIED，禁止伪造 |

### Agent 5: 开源增长分析

| 维度 | 内容 |
|------|------|
| 负责 Phase | Phase 7 |
| 输入 | GitHub API + README + 竞品仓库 |
| 产出 | `reports/github_growth_analysis.md` + `reports/star_growth_roadmap.md` |
| 工具 | gh api / grep / wc / WebSearch |
| 子 Agent | Competitor-Agent（竞品对比） |
| 状态 | DONE（2026-07-21 首轮） |
| 降级策略 | 网络 API 受限时降级为手动记录 |

### 汇总 Agent（主 Agent）

| 维度 | 内容 |
|------|------|
| 负责 Phase | Phase 8 + 验证轮 |
| 输入 | Agent 1-5 全部产出 |
| 产出 | `reports/future_roadmap.md` + `reports/final_report.html` + 一致性检查 |
| 工具 | 文件读写 + 交叉验证 |
| 状态 | IN_PROGRESS（验证轮） |

---

## 并行策略

```
时间线：
  T0 ──── Phase 0（串行，主 Agent）
  T1 ─┬── Agent 1（结构）
      ├── Agent 2（质量）     ← 可并行
      ├── Agent 5（增长）
      └── Agent 4（创新，部分依赖 Agent 3）
  T2 ──── Agent 3（架构，依赖 Agent 1 产出）
  T3 ──── Agent 4（创新，依赖 Agent 3 完成）
  T4 ──── 主 Agent 汇总 Phase 8
  T5 ──── 验证轮（本次）
```

---

## 输出格式契约

每个 Agent 产出必须包含：

```markdown
# [Phase N] 标题

> 审计日期 / 关联文件 / DoD 状态

## 方法
（用了什么命令/工具）

## 发现
（结构化表格/列表）

## 建议
（按优先级排序）

## UNKNOWN
（无法确认的项）
```

---

**更新时间**：2026-07-21
