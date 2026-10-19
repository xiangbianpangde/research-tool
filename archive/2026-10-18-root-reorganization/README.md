# 2026-10-18 根目录重组归档批次

## 批次信息

- 目的：收敛根目录、消除 `CLAUDE.md`/`AGENTS.md` 双入口，分离当前资料与历史资料，建立可恢复归档制度。
- 执行分支：`chore/reinitialize-project-layout`
- 重组前 HEAD：`144f0426973fbe5a6607d6238e2fe0bee66e6f88`
- 重组前远程基线：`origin/main` 同为 `144f0426973fbe5a6607d6238e2fe0bee66e6f88`
- 归档内容：原根入口、旧管理目录、旧工作日志、旧交付物、已结束的大型 VideoIngest 设计包和不再作为当前入口的历史报告。

## 当前路径与归档路径

| 原位置 | 归档位置 | 说明 |
|---|---|---|
| `AGENTS.md` | `original/AGENTS.md` | 由新的根 `Agent.md` 取代 |
| `CLAUDE.md` | `original/CLAUDE.md` | 用户明确不再使用 |
| `project_management/` | `original/project_management/` | 历史审计/管理材料，避免与当前文档边界重叠 |
| `worklogs/` | `original/worklogs/` | 历史日志与 ADR，保留原层级 |
| `deliverable*.md` | `original/deliverable*.md` | 历史交付报告，含旧路径说明 |
| `docs/plan/后续升级计划/` | `original/docs/plan/后续升级计划/` | 已结束的大型设计资料，保留原相对路径 |
| `docs/reports/` | `original/docs/reports/` | 历史报告与展示页 |
| `docs/notes/` | `original/docs/notes/` | 历史审查笔记 |
| `meta/AI重构笔记.md` | `original/meta/AI重构笔记.md` | 历史重构笔记 |
| `docs/plan/HANDOFF.md` | `original/docs/plan/HANDOFF.md` | 过时的视频实现交接，已实现内容不作为当前契约 |

## 当前结构中的移动

以下不是删除，而是进入当前职责边界：

- `experts.yaml` → `config/experts.yaml`
- `docs/config.example.yaml` → `config/example.yaml`
- `collect/architecture/system/*` → `docs/architecture/*`
- `conventions/*` → `docs/standards/*`
- `meta/CODE_MAP.md`、`meta/commitlint.config.js`、`meta/gitmessage` → `docs/meta/*`
- `CHANGELOG.md` → `docs/history/CHANGELOG.md`
- `docs/templates/CLAUDE模板.md` → `docs/templates/Agent模板.md`
- `conventions/CLAUDE-规范导航.md` → `docs/standards/Agent-规范导航.md`

## 重要边界

- `research_tool/`、`scripts/`、`pyproject.toml`、`uv.lock`、`README.md` 和 `STATUS.md` 仍是当前工作边界。
- 归档文件只读参考，不在其中继续开发。
- 归档不含 `research-output/`、`.venv/`、缓存和其他 ignored 运行产物。
- 本批次不包含任何凭据；清单只记录路径、大小和 SHA-256。

详细反向路径见 `RESTORE.md`；完整哈希见 `MANIFEST.sha256`。清单覆盖本次提交中的 247 个非清单文件路径（含当前文件、移动后的路径和归档原件）。
