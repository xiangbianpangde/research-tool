# 决策记录 — research-tool 全面审核与演进规划（计划本身）

> 日期: 2026-07-21
> 类型: 计划决策（启动期一次性记录）
> 关联 Plan: `/Users/xbpd/.claude/plans/agile-pondering-graham.md`
> 状态: 已采纳

## 背景

research-tool 项目（v0.1.1 + R12 阶段）需要长期整理和发展。在任何代码修改、删除文件、架构调整之前，必须先完成：

1. 项目事实收集（3 个 Explore agent 已完成）
2. 任务拆解（8 个审核 Phase + 长程管理结构）
3. Agent 规划（5 个并行 Agent + 子 Agent）
4. 审核计划（已落盘 plan 文件）
5. 创建管理文件结构（Phase 0）

## 决策

### 决策 1: 新建 `project_management/` 而非 `audit/`

| 方案 | 优点 | 缺点 |
|------|------|------|
| `project_management/`（采纳） | 长期演进看板；不暗示一次性 audit | 命名不直观 |
| `audit/` | 直白说明用途 | 暗示一次性，与"长期整理"目标不符 |
| 复用 `docs/` | 风格一致 | 与已有 `docs/{plan,reports,templates}` 角色冲突 |

### 决策 2: 复用 research-tool 自有模板

| 方案 | 优点 | 缺点 |
|------|------|------|
| research-tool 自有 `docs/templates/`（采纳） | 项目内模板优先级最高；风格一致 | 部分模板比 devguard 简单 |
| devguard `docs/templates/` | 收束报告 HTML 模板更精美 | 跨项目同步成本 |
| 全新模板 | 完全定制 | 增加维护成本；不必要 |

### 决策 3: 8 个 Phase 全部串行 Phase 0 启动后并行 1–7

| 方案 | 优点 | 缺点 |
|------|------|------|
| Phase 0 → Phase 1–7 并行 → Phase 8 汇总（采纳） | 启动一致；中间最大化并行；最后汇总 | 需严格的产出物契约 |
| 全部串行 | 简单 | 慢；失去并行机会 |
| 全部并行 | 最快 | Phase 6 依赖 Phase 3 事实 |

### 决策 4: 实验允许 UNVERIFIED

| 方案 | 优点 | 缺点 |
|------|------|------|
| 允许 UNVERIFIED + 强制诚实性声明（采纳） | 尊重现实约束；用户可后续补 | 需要诚信纪律 |
| 强制要求实验跑通 | 结果可信 | 不可行（无 GPU/数据/算力） |
| 跳过实验 | 简化 | 失去验证维度 |

### 决策 5: 不修改 STATUS.md 现有内容

| 方案 | 优点 | 缺点 |
|------|------|------|
| 仅追加审计 Phase 增量（采纳） | 保护收束历史；可追溯 | 表格变长 |
| 重写 STATUS.md | 清晰 | 丢失收束节点证据 |
| 创建 audit_status.md | 隔离 | 双轨制易混乱 |

### 决策 6: 不删除任何文件，仅登记

| 方案 | 优点 | 缺点 |
|------|------|------|
| 仅登记到 `archive/deleted_files/`（采纳） | 用户保留最终决定权；零破坏 | 需要后续人工动作 |
| 审计期间直接删除 | 立即清理 | 风险高；可能误删 |

## 影响

- 8 个 Phase 各自有 DoD 校验，无产出物不进入下一 Phase
- 矛盾点用 grep/cat/pytest --collect-only 实证解决，不靠记忆
- 所有 P0/P1/P2 债务有路径 + 行号 + 命令证据
- 实验若未跑通，强制 UNVERIFIED 标记 + 诚实性报告
- 审计期 `git status` 必须与启动期一致（无未授权修改）

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| Agent 输出超 context | 每 Agent ≤ 8k token；超则拆多轮 |
| 实验无算力 | UNVERIFIED + 用户后续补 |
| 工作区被未授权修改 | 启动期 `git status` 留痕；退出期对比 |
| 5 个 Agent 输出格式不一 | 每个 Agent 严格结构化（背景/方法/发现/建议） |

## 后续 ADR 候选

- **ADR 0004**: 长期审核与演进规划机制（`project_management/` 长程管理结构）
- **ADR 0005**: 创新点实验设计模板（`innovation/experiment.md` 模板）
- **ADR 0006**: 报告命名规范（`reports/YYYY-MM-DD_<topic>.md`）

候选 ADR 将在 Phase 6/8 完成后正式写入 `worklogs/decisions/`，由用户拍板是否合入 master。