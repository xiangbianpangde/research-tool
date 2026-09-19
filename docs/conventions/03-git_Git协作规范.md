# Git 协作规范

> **本规范是 [ai-workflow 第二步·迭代开发 §2.6 Git 提交](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) 的细化**，展开提交格式、分支策略与 Review 流程；也支撑 §2.3/§2.4 的审查与确认在 Git 层面落地。
>
> **档位即分支模型**：轻量（1-3 人）用 Trunk-Based；标准（4-10 人）用 GitHub Flow；团队（10+ 人）用 Git Flow。

---

## 一、红线（必守）

| # | 红线 | 怎么自动抓 |
|---|------|-----------|
| 1 | `main` 禁止直接 push，全部走 PR | 平台分支保护规则 |
| 2 | 提交符合 Conventional Commits（`type(scope): desc`） | `commitlint` + commit-msg hook |
| 3 | 一个 commit 只做一件事，禁止 `WIP`/`fix`/`update` 等模糊提交 | 审查 + commitlint |
| 4 | 禁止 `git push --force` 到共享分支（main/develop） | 平台分支保护 |
| 5 | 合入需至少 1 人 Approve（团队档；单人项目以自审 + DeepSeek 审查替代） | 平台分支保护 |

---

## 二、落地：提交与 PR 的可复制配置

**`commitlint.config.js` + 钩子（提交即校验格式）：**

```js
module.exports = { extends: ['@commitlint/config-conventional'] };
```

```yaml
# .pre-commit-config.yaml 追加
  - repo: https://github.com/alessandrojcm/commitlint-pre-commit-hook
    rev: v9.16.0
    hooks: [{ id: commitlint, stages: [commit-msg] }]
```

**`.gitmessage` 提交模板**（`git config commit.template .gitmessage`）：

```
# <type>(<scope>): <50 字内描述>
#
# 为什么这样改（动机 / 方案，而非复述 diff）：
#
# Closes #
```

**`.github/pull_request_template.md`：**

```markdown
## 做了什么
## 为什么这样做
## 怎么验证（命令 + 可观测证据，对应 ai-workflow §2.2）
```

**分支保护设置**（GitHub Settings → Branches，或 `gh api`）：勾选 Require PR、Require 1 approval、Require status checks、Restrict force push。一次配好，红线 1/4/5 由平台强制。

---

## 三、决策表

### 分支命名（kebab-case）

| 前缀 | 用途 | | 前缀 | 用途 |
|------|------|---|------|------|
| `feature/` | 新功能 | | `refactor/` | 重构 |
| `fix/` | Bug 修复 | | `docs/` | 文档 |
| `hotfix/` | 紧急修复（从 main 切） | | `chore/` | 工程配置 |

### 提交 type

| type | | type | | type |
|------|---|------|---|------|
| feat 新功能 | | fix 修复 | | refactor 重构 |
| docs 文档 | | test 测试 | | perf 性能 |
| style 格式 | | chore 构建/依赖 | | |

### 合并策略（按场景选）

| 场景 | 策略 |
|------|------|
| 功能分支细碎 commit 多 | Squash Merge |
| 追求线性历史的小团队 | Rebase + `merge --ff-only` |
| 需保留完整分支历史 | `merge --no-ff` |

| PR 约束 | 要点 |
|---------|------|
| 描述三段 | 做了什么 / 为什么 / 怎么验证 |
| 变更量 < 500 行 | 超过拆分 |
| 提交前自审 | `git diff main...HEAD` 确认无无关改动 |
| Review 关注逻辑 > 风格 | 非阻塞建议用 `nit:` 前缀 |

---

## 四、反模式（保留 Git 独有）

### ❌ 巨型 PR
`PR #42: 重构用户模块 + 新增订单 + 改文档 + 修3个bug（+3200 -1800，47文件）` → Review 无法完成，出问题无法定位。
✅ 拆成 3 个聚焦单主题的 PR，各 15 分钟可审完，`git bisect` 能精确定位。

### ❌ 模糊提交
`git commit -m "fix"` / `"WIP"` / `"改了一些东西"` → 3 个月后 `git log` 成天书。
✅ `fix(auth): 修复 token 过期未自动刷新` + body 解释动机，`git log --grep="fix(auth)"` 可检索。

---

## 五、检查清单

- [ ] 从最新 `main` 切分支，分支名符合 `feature/xxx` 等格式
- [ ] commit message 符合 Conventional Commits（commitlint 通过）
- [ ] 每个 commit 只做一件事，无 `WIP`
- [ ] PR 描述含：做了什么 / 为什么 / 怎么验证
- [ ] PR 变更量 < 500 行
- [ ] 至少 1 Approve（或单人项目已 DeepSeek 审查）
- [ ] 未对共享分支 force push
- [ ] 合并后删除功能分支

---

## 六、关联

| 方向 | 链接 |
|------|------|
| 细化自 | [ai-workflow 第二步 §2.6 Git 提交](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) |
| 验收标准 | [docs/specs/03-git.md](../docs/specs/03-git.md) |
| 验证证据要求 | [ai-workflow §2.2 可观测验证](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) |

---

## 更新记录

| 日期 | 版本 | 变更说明 |
|------|------|---------|
| 2026-05-27 | v2.0 | 重构为 §2.6 的细化；档位绑定分支模型；红线配 commitlint/分支保护；新增 .gitmessage、PR 模板、保护设置等可复制物料 |
| 2026-05-26 | v1.0 | 按统一模板改造 |
