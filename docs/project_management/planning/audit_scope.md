# research-tool · 审核范围定义（Audit Scope）

> **创建日期**：2026-07-21
> **关联**：`planning/task_plan.md` / `planning/agent_assignment.md`

---

## 审核目标

对 research-tool v0.1.1 进行全面审核，建立完整认知模型，为后续演进提供决策依据。

---

## 审核范围内（In Scope）

### 代码资产

| 范围 | 路径 | 说明 |
|------|------|------|
| 核心源码 | `research_tool/`（69 文件，18,906 行） | 5 层架构全部 |
| 测试代码 | `research_tool/tests/`（59 文件，13,918 行） | 覆盖率 + 质量 |
| 脚本 | `scripts/`（10 文件） | 安装/启动/烟囱测试 |
| 配置 | `pyproject.toml` / `config.yaml` / `.env.example` | 构建 + 运行配置 |

### 文档资产

| 范围 | 路径 | 说明 |
|------|------|------|
| 项目文档 | `README.md` / `STATUS.md` / `AGENTS.md` / `CLAUDE.md` | 对外 + 对内 |
| 规范 | `conventions/`（8 规范 + ai-workflow/） | 开发规范 |
| 设计文档 | `docs/plan/` / `docs/templates/` | 历史设计 |
| 工作日志 | `worklogs/` | ADR + 日志 |

### 基础设施

| 范围 | 路径 | 说明 |
|------|------|------|
| CI/CD | `.github/workflows/` | 仅 gitleaks |
| Git 历史 | 58 commits | 质量 + 规范 |
| 构建产物 | `dist/` | wheel + tarball |

### 外部资产

| 范围 | 说明 |
|------|------|
| GitHub 仓库元数据 | Star/Fork/Issue/Topics/Description |
| 竞品项目 | 5 个对标（DeepResearch/just-prompt/chatgpt-reviewer/LangChain 等） |

---

## 审核范围外（Out of Scope）

| 排除项 | 原因 |
|--------|------|
| `.venv/` / `.venv-pdf/` | 虚拟环境，非项目资产 |
| `.cache/`（5.1 GB） | 工具缓存（huggingface/mineru/playwright） |
| `research-output/`（78 MB） | 调研产物，非代码 |
| `work/`（19 MB） | 调试残留，已 gitignore |
| `node_modules/`（如有） | 前端依赖 |
| 真实 API 密钥安全性 | 无法验证是否已 rotate |
| 生产环境部署 | 本项目为本地工具，无生产部署 |
| 性能压测 | 超出审核范围（需专项） |

---

## 审核维度

| 维度 | Phase | 深度 |
|------|-------|------|
| 项目用途与技术栈 | 1 | 全面 |
| 文件结构 | 2 | 全面（40 行表格） |
| 代码质量（Smell + Bug） | 3 | 全面（22 条） |
| 架构设计 | 4 | 全面（15 条问题 + 迁移方案） |
| 技术债务 | 5 | 全面（25 条 P0/P1/P2） |
| 创新点 | 6 | 全面（9 个 + 实验设计） |
| 开源竞争力 | 7 | 全面（5 维度 + 竞品对比） |
| 发展规划 | 8 | 全面（10 方向 + 10 Star 增长） |

---

## 审核方法

| 方法 | 适用场景 |
|------|---------|
| 命令行实证 | 文件数/行数/测试数/lint 状态 |
| 源码阅读 | 架构/模块设计/代码质量 |
| grep 模式匹配 | 重复代码/异常处理/命名规范 |
| GitHub API | 仓库元数据/竞品对比 |
| 交叉验证 | 文档 vs 代码一致性 |

---

## 审核约束

1. **只读**：不修改 `research_tool/` 下任何源码
2. **不删除**：所有文件保留，仅登记处置建议
3. **实证**：所有结论需命令证据或文件路径引用
4. **诚实**：无法验证标记 UNKNOWN，禁止伪造
5. **可追溯**：所有决策记录到 `decisions/`

---

## DoD（Definition of Done）

| Phase | DoD |
|-------|-----|
| 0 | `project_facts.md` + `planning/` 4 文件 + git 留痕 |
| 1 | 4 章节齐全 + 真实路径引用 ≥ 20 处 |
| 2 | 表格 ≥ 30 行 + 高/中/低分布 + 未跟踪文件处置 |
| 3 | 每条 ≥ 5 字段 + P0/P1/P2 分类 + 真实路径 ≥ 30 处 |
| 4 | 当前架构 + 15 条问题 + 目标架构 + 迁移方案 |
| 5 | 25 条债务 + S/M/L 成本 + 回合节奏 |
| 6 | 9 创新点 + 9 实验设计 + 诚实性报告 |
| 7 | 5 维度 ≥ 3 条/维度 + 竞品对比 + Top 3 瓶颈 |
| 8 | HTML 报告 14 章节 + 一致性检查通过 |

---

**更新时间**：2026-07-21
