# research-tool · 审核任务拆解（Task Plan）

> **创建日期**：2026-07-21
> **关联**：`project_management/status/project_facts.md` / `decisions/2026-07-21_phase-audit-plan.md`

---

## 总任务

对 research-tool 项目进行全面审核与演进规划，产出：项目事实、代码质量分析、架构评估、技术债务评级、创新点挖掘、开源竞争力分析、后续发展规划。

**约束**：禁止修改源代码、禁止删除文件、所有结论需实证。

---

## 子任务分解

### Phase 0: 启动与事实收集

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T0-1 | 创建 project_management/ 目录结构 | P0 | 无 | DONE | 8 子目录 |
| T0-2 | git status 留痕 | P0 | 无 | DONE | worklog 记录 |
| T0-3 | 运行实证命令收集项目事实 | P0 | 无 | DONE | `status/project_facts.md` |
| T0-4 | 创建 planning/ 4 文件 | P0 | T0-3 | DONE | 本文件 + 3 个 |

### Phase 1: 项目整体分析

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T1-1 | 分析项目用途/用户/场景 | P0 | T0-3 | DONE | `reports/project_overview.md` §1 |
| T1-2 | 梳理技术栈 | P0 | T0-3 | DONE | `reports/project_overview.md` §2 |
| T1-3 | 生成架构图/数据流/调用关系 | P0 | T1-1 | DONE | `reports/project_overview.md` §3 |
| T1-4 | 用实测数据补全矛盾点 | P1 | T0-3 | DONE | `reports/project_overview.md` §5 |

### Phase 2: 文件结构审核

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T2-1 | 遍历目录结构 | P0 | T0-3 | DONE | `reports/file_audit.md` |
| T2-2 | 识别问题文件（临时/重复/废弃） | P1 | T2-1 | DONE | 40 行表格 |
| T2-3 | 未跟踪文件处置建议 | P1 | T2-1 | DONE | 6 文件建议表 |

### Phase 3: 代码质量审核

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T3-1 | Code Smell 扫描 | P0 | T0-3 | DONE | `problems/code_quality.md` §A（12 条） |
| T3-2 | Bug 风险扫描 | P0 | T0-3 | DONE | `problems/code_quality.md` §B（8 条） |
| T3-3 | 测试覆盖分析 | P1 | T3-1 | DONE | `problems/code_quality.md` §C |
| T3-4 | 运行 ruff check 补全 UNKNOWN | P1 | T0-3 | DONE | ruff 1 error |

### Phase 4: 架构审核

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T4-1 | 分层架构分析 | P0 | T1-3 | DONE | `reports/architecture_review.md` §1-2 |
| T4-2 | 扩展性审视 | P1 | T4-1 | DONE | §3 |
| T4-3 | 偏差复核 | P1 | T4-1 | DONE | §4（4 处） |
| T4-4 | 目标架构 + 迁移方案 | P1 | T4-2 | DONE | §6-7 |

### Phase 5: 技术债务评级

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T5-1 | 汇总 P0/P1/P2 债务 | P0 | T3,T4 | DONE | `problems/technical_debt.md`（25 条） |
| T5-2 | 修复成本估算 | P1 | T5-1 | DONE | S/M/L 标注 |
| T5-3 | 回合节奏建议 | P2 | T5-2 | DONE | Round 7-12 |

### Phase 6: 创新点分析

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T6-1 | 识别创新点 | P1 | T4 | DONE | `innovation/catalog.md`（9 个） |
| T6-2 | 设计验证实验 | P1 | T6-1 | DONE | `experiments/catalog.md`（9 个） |
| T6-3 | 诚实性报告 | P0 | T6-2 | DONE | `validation_report.md` |

### Phase 7: Star 增长分析

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T7-1 | GitHub API 基线探测 | P0 | 无 | DONE | `reports/github_growth_analysis.md` §0 |
| T7-2 | 5 维度分析 | P0 | T7-1 | DONE | §1-5 |
| T7-3 | 竞争项目对比 | P1 | T7-2 | DONE | §5 对比表 |
| T7-4 | Star 增长方向 Top 10 | P1 | T7-3 | DONE | `reports/star_growth_roadmap.md` |

### Phase 8: 汇总与规划

| ID | 子任务 | 优先级 | 依赖 | 状态 | 产出 |
|----|--------|--------|------|------|------|
| T8-1 | 未来 10 个发展方向 | P0 | T5,T6 | DONE | `reports/future_roadmap.md` |
| T8-2 | 文件整理方案 | P1 | T2 | DONE | `reports/final_structure.md` |
| T8-3 | 熵管理 | P2 | 全部 | DONE | `reports/entropy_management.md` |
| T8-4 | 最终 HTML 报告 | P0 | 全部 | DONE | `reports/final_report.html` |
| T8-5 | 验证轮补全 + 一致性检查 | P1 | T0-3 | IN_PROGRESS | 本次更新 |

---

## 依赖关系图

```
T0-3 (事实收集)
  ├── T1 (项目分析) ──→ T4 (架构) ──→ T5 (债务)
  ├── T2 (文件审核) ──→ T8-2 (整理方案)
  ├── T3 (代码质量) ──→ T5 (债务)
  └── T7 (Star 分析) ──→ T8-1 (规划)
T5 + T6 ──→ T8-1 (发展方向)
全部 ──→ T8-4 (HTML) ──→ T8-5 (验证)
```

---

**更新时间**：2026-07-21
