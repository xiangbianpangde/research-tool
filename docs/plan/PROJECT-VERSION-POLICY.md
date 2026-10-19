# 本机项目版本与归档制度

## 唯一入口

日常打开、开发、安装和运行只使用 `/Users/xbpd/Projects/research-tool`。默认分支为 `main`；根目录重组已通过 PR #2 以 `56f370e` 合入。当前仍是面向 AI Agent 的 macOS CLI/SDK-only，保留完整九阶段，已移除 WebUI/Gradio。`96c18be` 是 2026-10-17 迁移时选择的历史基线，不是当前分支标识。

版本身份由 Git 的分支和提交确定，不用目录名、修改时间或“old/new/final”猜测。开始工作先执行：

```bash
cd /Users/xbpd/Projects/research-tool
git status --short --branch
git log -3 --oneline
```

不再建立 `~/Projects/research-tool-old`、`research-tool-refactor`、`research-tool-worktrees` 等平行开发副本。新功能使用当前仓库的 feature/fix 分支；需要隔离且已获授权时，临时 worktree 放入仓库内部忽略目录，任务结束后归档或移除，不再产生第二个公开项目入口。归档仓库只读参考，不继续在其中开发。

## 归档位置和索引

仓库内已下线文档进入 `archive/<batch>/original/`；当前有效工作记录仍在 `docs/records/`。已发布批次与清单不覆盖，详细规则以根 `Agent.md` 为准。`archive/2026-10-18-root-reorganization/` 保存 PR #2 迁移原件；其清单中的活动文件哈希是重组提交快照，后续活动文件变化由 Git 追踪，不应重写该清单。

本机历史仓库、worktree 与未提交快照统一保存到：

`/Users/xbpd/Projects/archives/research_tool_archive/versions/<时间标记>/`

本次归档时间标记为 `2026-10-17T1840+0800`。入口为归档根 `README.md`；该版本内有 `inventory-before.json`、`canonical-selection.json` 和 `RESTORE.md`。归档根使用非 `research-tool` 前缀，避免在 Projects 下再次出现多个候选入口。

| 原始位置 | 本次归档目录 | 用途 |
|---|---|---|
| `Projects/research-tool`（原 master / ab2b0d6） | `legacy-main-workspace` | 旧主线、29 个 tracked 修改和未跟踪内容 |
| `Projects/research-tool-old`（97de0ae） | `legacy-old-workspace` | 旧运行代码、DRB 数据及未提交补丁 |
| `Projects/research-tool-refactor`（e039dca） | `refactor-experiment-repo` | 独立重构实验及实验产物 |
| `Projects/research-tool-refactor-backup.git`（f863710） | `refactor-backup-bare.git` | 重构实验 bare 备份 |
| `Projects/research-tool-worktrees/v1.1-optimization` | `legacy-worktree-v1.1-optimization` | 已修复 Git 指针的旧 worktree |
| `.codex/worktrees/nine-stage-only/research-tool`（96c18be） | `codex-nine-stage-worktree` | 本次 canonical 来源 worktree |
| `Projects/research-tool-worktrees` | `legacy-worktrees-root` | 旧 worktree 容器残留 |

表中 `Projects` 指 `/Users/xbpd/Projects`，`.codex` 指 `/Users/xbpd/.codex`。不要把旧版本的整棵文件树直接合回当前九阶段 CLI 版。

## 未来迁移的必做检查

1. 读取当前分支、HEAD、worktree、tracked/untracked/ignored 状态；检查是否有写入中的进程。
2. 为要保留的文件生成相对路径、大小和 SHA-256 清单；清单不得包含密钥内容。保留 ignored 的研究数据、未跟踪文件及本地配置，不把它们提交或上传。
3. 写明原路径→归档路径、选择理由和反向恢复步骤；移动前记录回滚方案。
4. 使用不覆盖的可逆移动。linked worktree 使用 Git move/repair，移动 common repo 后复查双向指针。禁止 reset/clean/stash 来隐藏未提交工作。
5. 移动后校验 HEAD、脏状态、哈希、worktree 列表与必要运行测试，确认 Projects 下仅剩 canonical 项目入口。
6. 不自动删除归档；需要回收空间时单独征求用户确认，并使用系统可恢复删除。

## 本次证据边界

本次移动前保存了路径、HEAD、分支、脏状态和大小，但**未保存逐文件内容哈希**；所以可以核对 Git 身份、状态与文件存在性，不能声称所有脏文件做过迁移前后逐字节校验。2026-10-17 独立复核确认归档 HEAD/脏状态保留、两组 worktree 指针有效，bare 仓库 `git fsck --full` 通过。

历史 `NINE-STAGE-BASELINE.json` 是不可变恢复证据，不改其中原始路径；使用本文件和归档索引解析旧路径。例如历史 `Projects/research-tool-old/work/drb-bench/` 现位于 `legacy-old-workspace/work/drb-bench/`。

## 产物与密钥

运行产物放当前仓库被忽略的 `research-output/<任务>/`，不再散落到新项目副本。测试端点、模型名、超时等可写无密钥配置；真实密钥仅通过当前进程环境或用户管理的本机忽略配置注入，不写入源码、报告、归档索引或 Git。

## 恢复旧版本

先为 canonical 做一份新归档，随后在非 canonical 路径恢复目标版本，核对分支/HEAD/配置；不要覆盖当前入口。确需整体回滚时按该版本 `RESTORE.md` 的反向映射移动并运行 `git worktree repair`。恢复后仍只能保留一个日常开发入口。
