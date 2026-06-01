# 规范导航（AI 版）

> **⚠️ 仅供 AI Agent**。人类读 `README-规范导航.md`。
> 用途：开工前先按"任务→规范"定位该加载哪几篇，再读对应篇的 §一红线 + §二落地配置。不要一次读完 7 篇。

---

## 1. 规范 = 流程的细化

`conventions/` 各篇不是孤立文档，是 [ai-workflow](ai-workflow_AI协作开发流程/) 各步骤的**细化**。到流程的哪一步，就读对应规范：

| 流程步骤 | 读哪篇 | 这篇定义"那一步具体怎么做" |
|---------|--------|--------------------------|
| 第一步·技术选型与架构 | [01 架构](01-architecture_架构设计规范.md) | 分层 / 依赖方向 / 模块划分 / 配置 |
| 第二步 §2.1 实现 | [02 代码](02-coding_代码编写规范.md) + [04 API](04-api_API设计规范.md)(若写接口) | 命名/结构/错误处理/安全 + REST 设计 |
| 第二步 §2.1 TDD / §2.2 验证 | [05 测试](05-testing_测试规范.md) | TDD 节奏 / 覆盖率 / Mock 边界 |
| 第二步 §2.3 审查 diff | [02 §六检查清单](02-coding_代码编写规范.md) | 审查清单 = 该篇检查清单 |
| 第二步 §2.6 提交 | [03 Git](03-git_Git协作规范.md) | 分支 / 提交格式 / PR / 合并 |
| §2.5 记录 + 07-汇报 | [06 文档](06-documentation_文档规范.md) | README/CHANGELOG/docstring/worklog |
| 第二步 §2.9 工具增强 | [08 图谱](08-code-understanding_代码理解与图谱规范.md) | 双图谱：CodeGraph（AI 用）+ Understand-Anything（人用） |

> 档位裁剪：轻量项目只守红线；标准/团队守全部。档位定义见 [第一步 §1.0](ai-workflow_AI协作开发流程/03-第一步_编写计划.md)。

---

## 2. 红线总表（违反 = 打回，写代码/审查时逐条核）

| 维度 | 红线 | 自动检测 |
|------|------|----------|
| 01 | 禁循环依赖 · 领域层不依赖框架/ORM · 禁跨层调用 | import-linter / dependency-cruiser |
| 02 | 禁 print·console.log · SQL 参数化 · 密钥走环境变量 · 禁 `except:pass` · 禁注释代码块/调试断点 · 日志脱敏 · 输入校验 | ruff + gitleaks（pre-commit） |
| 03 | main 禁直推 · Conventional Commits · 一 commit 一事 · 禁 force push 共享分支 · ≥1 Approve | commitlint + 分支保护 |
| 04 | 名词复数+kebab-case 无动词 · 统一响应 code·message·data · 状态码与 code 一致 · 错误码唯一 · 默认认证 · 输入校验 · 向后兼容 | spectral OpenAPI lint |
| 05 | 测试独立不依赖顺序 · 只 Mock 外部边界 · 覆盖正常+边界+异常 · 无 flaky | 覆盖率门禁 |
| 06 | 必有 README(快速开始) · 发布更 CHANGELOG · 文档随码同 PR · 公共 API 有 docstring · 注释与代码一致 | 审查 |

落地配置（复制即用）见各篇 §二，或 `src/<维度>/`：`importlinter.ini` / `ruff.toml` / `.pre-commit-config.yaml` / `.spectral.yaml` / `pytest.ini`。

---

## 3. 单一权威 / 去重映射（改动时别复制到别处）

| 主题 | 唯一权威 | 别处只引用 |
|------|----------|-----------|
| 密钥 / 代码级安全 | 02 §一 | 01 配置管理只引用 |
| 跨层调用 / 循环依赖 / 分层 | 01 | 08 提供大型项目的自动检测 |
| 调用图 / @entrypoint / CALL_GRAPH / 影响分析 / 代码地图 / 依赖黑洞 | **08（全部收拢）** | 01/02/05/06 各留一句指向 08 |
| 错误响应格式 | 04 | 02 错误处理引用 |
| Conventional Commits | 03 | — |
| 文档模板正文 | `docs/templates/` | 06 只链接不重抄 |

---

## 4. 改规范时的同步清单

1. 改 `conventions/NN-*.md` → 同步 `docs/specs/NN-*.md` BDD。
2. 动到 §二落地配置 → 同步 `src/<维度>/` 可运行示例。
3. 红线增删 → 同步本文件 §2 + `README-规范导航.md` 红线速查。
4. 汇报：更新 `worklogs/` + `STATUS.md`（见 [CLAUDE.md](../CLAUDE.md) 工作流程）。

> 各篇正文结构统一：§一红线（配检测）→ §二落地配置 → §三决策表 → 反模式 → 检查清单 → 关联。
