# research-tool Agent 入口

> 本文件是本仓库唯一的 AI Agent 工作入口。`CLAUDE.md` 不再使用；不要创建第二份同类上下文文件。

## 1. 开工顺序

在任何修改前，先在仓库根目录执行：

```bash
git rev-parse --show-toplevel
git status --short --branch
git log -3 --oneline
```

然后按任务读取：

- 项目用途与 CLI/SDK：`README.md`
- 当前状态、验证证据和限制：`STATUS.md`
- 现行版本与外部旧版本归档制度：`docs/plan/PROJECT-VERSION-POLICY.md`
- 当前九段实现边界：`docs/plan/NINE-STAGE-ONLY-TRIM-PLAN.md`
- 代码入口索引：`docs/meta/CODE_MAP.md`
- 开发规范：`docs/standards/`

事实以源码、运行时状态、测试结果和当前 Git 历史为准。历史文档只用于追溯，不能覆盖当前事实。

## 2. 当前仓库边界

```text
.
├── Agent.md                 # 唯一 AI Agent 入口
├── README.md                # 人类使用说明
├── STATUS.md                # 当前状态与验收证据
├── pyproject.toml           # Python 项目与 CLI 元数据
├── uv.lock                  # 依赖锁定
├── .env.example             # 无密钥配置示例
├── .github/                 # CI 与仓库自动化
├── research_tool/           # 唯一生产 Python 包与测试
├── scripts/                 # 可复用的开发/验收脚本
├── config/                  # 可提交的配置样例与专家库
├── docs/                    # 当前仍有效的文档资产
│   ├── architecture/       # 当前架构图和系统说明
│   ├── plan/               # 当前执行计划、基线与版本制度
│   ├── standards/          # 开发规范与 AI 协作流程
│   ├── templates/          # 可复用模板
│   ├── records/            # 当前工作记录、决策和报告
│   ├── meta/               # 代码地图和 Git 辅助配置
│   └── history/            # 发布历史
├── archive/                 # 仓库内只读历史归档，按批次保存
└── research-output/         # 本地运行产物，已被 Git 忽略，不入库
```

### 生产边界

- `research_tool/` 是唯一生产代码和测试边界。不要再创建 `src/`、第二个 `research_tool/` 或平行实现。
- `scripts/` 只放可复用脚本；一次性运行日志和临时脚本放系统临时目录或被忽略的运行目录。
- `config/` 只放不含凭据的可提交配置。真实密钥只能通过环境变量或本地忽略配置注入。
- `research-output/` 是运行输出，不得把报告、缓存、日志或原始资料提交到 Git。
- `.venv/`、缓存、构建产物和 `__pycache__/` 不属于项目结构，不得整理进仓库。

## 3. 文件放置与命名

1. 新文件先按职责选择最近的现有边界；禁止把功能文档、日志、脚本和配置重新堆到根目录。
2. 根目录只保留项目入口、构建/依赖配置、仓库自动化和必要的公开说明；新的计划、报告、模板、规范、工作记录都放入对应的 `docs/` 子目录。
3. 目录名使用稳定职责名：`architecture`、`plan`、`standards`、`templates`、`meta`、`history`、`archive`。不要创建 `misc/`、`new/`、`temp/` 或按文件扩展名分桶的目录。
4. 文件名优先使用 ASCII、`kebab-case` 或既有中文名称；日期使用 `YYYY-MM-DD`，版本使用 `vMAJOR.MINOR`。不为美观重命名公共路径。
5. 生产代码、测试、脚本和配置的移动必须同时检查 import、CLI、CI、打包清单、文档链接和测试；无法确认调用方时停止迁移并提出具体方案。
6. 只读历史材料不得伪装成当前计划、当前状态或生产代码。需要复用时，先在当前边界建立新的、经过核验的文件。

## 4. 归档制度

### 4.1 仓库内归档

当前工作记录根为 `docs/records/`；历史归档根为 `archive/`。每次结构迁移、版本收束或历史资料下线都建立一个不可覆盖的批次目录：

```text
archive/YYYY-MM-DD-<purpose>/
├── README.md                 # 批次说明、来源、范围、恢复方法
├── MANIFEST.sha256           # 文件清单与 SHA-256；不得写入密钥内容
├── original/                 # 按迁移前相对路径保存的原始文件
└── RESTORE.md                # 反向路径映射和恢复步骤（需要时）
```

规则：

- 批次目录一经发布不覆盖、不复用；修正建立新批次。
- `original/` 保留原相对路径，避免同名文件冲突并支持逐项恢复。
- 归档前记录当前分支、HEAD、工作区状态、文件清单和哈希；有未提交文件时必须保留并明确标注。
- 归档不是删除证明。未经用户明确授权，不执行 `git clean`、`reset --hard`、覆盖写入或永久删除。
- 归档文件默认只读参考，不在其中继续开发；需要恢复时先在非 canonical 路径核对，再通过新提交回迁。

### 4.2 外部版本归档

历史仓库、旧 worktree 和未提交快照继续统一放在：

`/Users/xbpd/Projects/archives/research_tool_archive/versions/<timestamp>/`

本地唯一日常开发入口仍是 `/Users/xbpd/Projects/research-tool`。不要再创建 `research-tool-old`、`research-tool-refactor` 或其他平行日常入口。外部归档的路径映射和恢复规则以 `docs/plan/PROJECT-VERSION-POLICY.md` 为准。

### 4.3 运行产物归档

研究运行结果统一写入被忽略的 `research-output/<topic>/`，由运行自己的 `run_id`、摘要和 SHA-256 负责版本化。运行产物不复制进 `docs/records/`；若要永久保存，使用项目现有的不可变导出/打包流程，并保留来源与哈希。

## 5. Git 与远程仓库

- 日常开发使用 feature/fix/chore 分支；`main` 只接收经过审查的合并。
- 本次目录重组使用独立分支 `chore/reinitialize-project-layout`，完成后推送同名远程分支；不直接改 `origin/main`。
- 提交前必须检查：`git status`、`git diff --check`、路径引用、构建/导入和受影响测试。
- 不提交 `.env`、密钥、`research-output/`、缓存、虚拟环境或本地临时文件。
- 远程仓库只同步 Git 跟踪的当前结构；本地 ignored 产物不作为远程目录结构的一部分。

## 6. 本次结构重组的恢复点

- 重组前 Git 回滚点：`144f042`。
- 当前批次：`archive/2026-10-18-root-reorganization/`。
- 当前批次的 `README.md`、`RESTORE.md` 记录本次所有移动；完成收束后补充 `MANIFEST.sha256` 逐文件校验清单。
- 若验证失败，优先回到该分支的上一个提交或按 `RESTORE.md` 做逆向迁移；不得用 reset/clean 隐藏问题。

## 7. 交付前检查

```bash
find . -maxdepth 1 -mindepth 1 -print | sort
git status --short --branch
git diff --check
rg -n 'CLAUDE\.md|AGENTS\.md|conventions/|project_management/|worklogs/|docs/config\.example\.yaml' README.md STATUS.md Agent.md docs research_tool scripts config
rg -n 'experts\.yaml' README.md STATUS.md Agent.md docs research_tool scripts config
python -B -m pytest -q -p no:cacheprovider research_tool/tests
ruff check --no-cache research_tool/ pyproject.toml
```

报告时明确区分：已移动、已归档、已运行测试、已验证真实 CLI/SDK、仍未验证和需要用户行动的事项。
