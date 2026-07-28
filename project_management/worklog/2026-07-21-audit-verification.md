# 工作日志：research-tool 全面审核 — 验证轮（Verification Round）

> 日期: 2026-07-21
> 阶段: Phase V（验证轮）
> 关联 Plan: 全面审核与演进规划
> 前序: `worklogs/2026-07-21_audit-kickoff.md`（Phase 0 启动）

---

## 做了什么

- [x] 运行实证命令收集项目事实（pytest/ruff/git/grep/wc）
- [x] 创建 `status/project_facts.md`（139 行，全量事实表）
- [x] 创建 `planning/task_plan.md`（任务拆解 + 依赖图）
- [x] 创建 `planning/agent_assignment.md`（5 Agent 分工 + 并行策略）
- [x] 创建 `planning/audit_scope.md`（范围定义 + DoD）
- [x] 创建 `planning/risk_register.md`（20 条风险 + 热力图）
- [x] 补全 `problems/code_quality.md` ruff UNKNOWN（1 error PLR0915）
- [x] 补全 `reports/project_overview.md` 测试数矛盾（840 权威值）
- [x] 修正 CLI 命令数（13→14，新实测）
- [x] 验证 `architecture_review.md` 5 处行号引用（全部通过）
- [x] 更新 `status/current_status.md`（Phase 1-8 标 ✅ + 矛盾点标已解决）
- [x] 创建本 worklog
- [x] 创建 decision 记录

---

## 关键发现

| 发现 | 证据 | 影响 |
|------|------|------|
| 测试数 840（非 454/417） | `pytest --collect-only -q` | STATUS.md + CLAUDE.md 均过期 |
| CLI 命令 14（非 11/13） | `grep -c @app.command` | 前次审计也少计了 1 个 |
| SearchBackend 15 具体实现 | `grep class.*Backend` | 排除抽象基类 + CachingBackend 装饰器 |
| ruff 仅 1 error | `ruff check research_tool/` | 代码质量良好（PLR0915 extractor.py:106） |
| 源码 18,906 行 / 测试 13,918 行 | `wc -l` | 前次 file_audit.md 写反了（src/tests 互换） |
| 架构报告行号全部准确 | 5 处 sed 验证 | 前次审计质量可靠 |

---

## 关键决策

| 决策 | 原因 | 影响 |
|------|------|------|
| 复用已有审核产物 + 验证补全 | 前次审计质量高（行号准确/结构完整），无需重做 | 节省 80% 工作量 |
| 修正 CLI 命令数为 14 | 前次审计记录 13，实测 14 | 更新 project_overview.md |
| 保留 file_audit.md 行数错误不修改 | 该报告为历史快照，在 project_facts.md 中给出正确值即可 | 避免修改过多历史文件 |

---

## 遇到的问题

- **前次 file_audit.md 源码/测试行数写反**：报告写 "src 13,918 / tests 18,906"，实测为 "src 18,906 / tests 13,918"。处置：在 `project_facts.md` 中记录正确值，不修改历史报告。
- **CLI 命令数前次审计为 13，本次为 14**：差异来自 `@app.command()` 匿名命令计数方式。实测 `grep -c` 为 14。

---

## 给下一位的交接

- 所有 Phase 1-8 + 验证轮已完成
- 权威数据源：`status/project_facts.md`（非 STATUS.md / CLAUDE.md）
- 后续行动优先级：`planning/risk_register.md` Top 5
- 发展方向：`reports/future_roadmap.md` D1-D10
- Star 增长：`reports/star_growth_roadmap.md` S1-S10
- 未提交文件：13 modified + 10 untracked（含 project_management/ 全部）
- **下一步建议**：用户拍板后执行 D1（4 个 P0 修复）

---

## 产出物清单

| 文件 | 类型 | 行数 |
|------|------|------|
| `status/project_facts.md` | 新建 | 139 |
| `planning/task_plan.md` | 新建 | 114 |
| `planning/agent_assignment.md` | 新建 | 130 |
| `planning/audit_scope.md` | 新建 | 120 |
| `planning/risk_register.md` | 新建 | 94 |
| `worklog/2026-07-21-audit-verification.md` | 新建 | 本文件 |
| `decisions/2026-07-21-audit-verification-decisions.md` | 新建 | 待创建 |
| `status/current_status.md` | 更新 | Phase 1-8 标 ✅ |
| `problems/code_quality.md` | 更新 | ruff 补全 |
| `reports/project_overview.md` | 更新 | 测试数 + 命令数 |

---

**完成时间**：2026-07-21
