# RESTORE — 2026-10-18 根目录重组

本文件只描述如何在审查或恢复需要时反向恢复本批次的路径。默认不要恢复旧入口；当前唯一入口是根目录 `Agent.md`。

## 前置条件

1. 在仓库根执行 `git status --short --branch`，确认没有未保存的用户改动。
2. 为当前 canonical 工作树创建新的归档或使用一个新分支；不得覆盖当前工作。
3. 先阅读 `README.md` 和 `MANIFEST.sha256`，确认归档完整。
4. 恢复动作必须以新的 Git 提交记录，不使用 `reset --hard`、`clean` 或强制覆盖。

## 反向映射

### 历史入口与管理材料

```text
original/AGENTS.md                                  -> AGENTS.md
original/CLAUDE.md                                  -> CLAUDE.md
original/project_management/                         -> project_management/
original/worklogs/                                   -> worklogs/
original/deliverable.md                              -> deliverable.md
original/deliverable-track-core.md                   -> deliverable-track-core.md
original/deliverable-track-integration.md            -> deliverable-track-integration.md
original/docs/plan/后续升级计划/                      -> docs/plan/后续升级计划/
original/docs/plan/HANDOFF.md                        -> docs/plan/HANDOFF.md
original/docs/reports/                               -> docs/reports/
original/docs/notes/                                 -> docs/notes/
original/meta/AI重构笔记.md                          -> meta/AI重构笔记.md
```

### 当前资料移动

```text
config/experts.yaml                                 -> experts.yaml
config/example.yaml                                 -> docs/config.example.yaml
docs/architecture/NINE-LOOP.md                      -> collect/architecture/system/NINE-LOOP.md
docs/architecture/nine-stage-loop.png              -> collect/architecture/system/nine-stage-loop.png
docs/standards/01-*                                 -> conventions/01-*
docs/standards/02-*                                 -> conventions/02-*
docs/standards/03-*                                 -> conventions/03-*
docs/standards/04-*                                 -> conventions/04-*
docs/standards/05-*                                 -> conventions/05-*
docs/standards/06-*                                 -> conventions/06-*
docs/standards/08-*                                 -> conventions/08-*
docs/standards/Agent-规范导航.md                    -> conventions/CLAUDE-规范导航.md
docs/standards/README-规范导航.md                   -> conventions/README-规范导航.md
docs/standards/ai-workflow_AI协作开发流程/          -> conventions/ai-workflow_AI协作开发流程/
docs/meta/CODE_MAP.md                              -> meta/CODE_MAP.md
docs/meta/commitlint.config.js                      -> meta/commitlint.config.js
docs/meta/gitmessage                                -> meta/gitmessage
docs/history/CHANGELOG.md                           -> CHANGELOG.md
docs/templates/Agent模板.md                         -> docs/templates/CLAUDE模板.md
```

## 手工恢复示例

只恢复一份文件时，使用 `git mv` 的逆向路径并检查引用：

```bash
git switch -c restore/root-layout-20261018
mkdir -p "$(dirname <target>)"
git mv <archive-relative-path> <target>
rg -n '<old-path>|<new-path>' README.md STATUS.md docs research_tool scripts config
python -B -m pytest -q -p no:cacheprovider research_tool/tests
```

完整回滚本次提交时，优先使用 Git 生成的反向提交（`git revert <reorganization-commit>`）并审阅结果；不要对共享分支强制改写历史。
