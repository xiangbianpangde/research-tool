# research-tool · 最终目录结构 + 迁移方案

> 数据基础：Phase 2 文件结构审核 + Phase 4 架构审核 + 25 条技术债

## 当前结构（简化）

```
/Volumes/项目/research-tool/
├── .github/                       # 仅 gitleaks.yml
├── .venv/, .venv-pdf/, .cache/    # 隔离环境
├── .DS_Store, .env, .coverage     # 临时/敏感
├── AGENTS.md = CLAUDE.md          # 6 416 B 双份
├── README.md (616 行, 中文) / CLAUDE.md / STATUS.md
├── docs/{plan,reports,templates}/
├── conventions/                   # 8 规范 + ai-workflow 7 份
├── research_tool/                 # 5 层 + 公共（69 模块）
├── research-output/ (78 MB)
├── scripts/
├── work/ (19 MB 调试残留)
├── worklogs/{decisions/}          # 3 ADR
├── meta/
├── deliverable.md + deliverable-track-{core,integration}.md  # 含旧 src/ 路径
└── pyproject.toml + uv.lock + ruff + pytest
```

## 目标结构（增量）

```
.github/
├── workflows/
│   ├── gitleaks.yml               # 已有
│   ├── test.yml                   # 新增 (matrix py + ruff + pytest)
│   ├── release.yml                # 新增 (PyPI trusted publisher)
│   └── dependabot.yml             # 新增
├── ISSUE_TEMPLATE/                # 新增
└── PULL_REQUEST_TEMPLATE.md       # 新增

README.md                          # 重做首屏
README.en.md                       # 新增（英文前 80 行）
LICENSE                            # 新增（pyproject 声明 MIT）
CHANGELOG.md                       # 新增
CONTRIBUTING.md                    # 新增
AGENTS.md → 软链 CLAUDE.md         # 修复双份

docs/
├── plan/archive/plan-v0.1.x/      # ← docs/plan/后续升级计划/ 1.8 MB 迁移
├── showcase/                      # 新增（对外展示）
├── why-research-tool.md           # 新增（3 大差异化）

research_tool/
├── presentation/
│   ├── cli.py (缩为 200 行)
│   ├── cli/{commands,render,redact}.py  # 新增
│   └── webui.py
├── domain/
│   ├── models.py (聚合 import)
│   ├── configs.py / results.py / video.py  # 新增
├── infrastructure/
│   ├── ingest/transcriber/{engines,runner,cache}/  # 新增
│   └── services.py                # 新增（薄服务层）

project_management/                # 本次新增（保留作长期看板）
```

## 迁移步骤（4 步）

### Step 1：文档归档（无破坏）

```bash
git mv docs/plan/后续升级计划 docs/archive/plan-v0.1.x-20260708
sed -i '' 's|src/research_tool|research_tool|g' deliverable*.md
git add deliverable*.md && git commit -m "docs: align deliverable paths to research_tool/ rename"
```

### Step 2：基础设施补齐

```bash
# .github/workflows/{test,release,dependabot}.yml
# .github/ISSUE_TEMPLATE/{bug,feature,question}.md
# .markdownlint.json
rm AGENTS.md && ln -s CLAUDE.md AGENTS.md
curl -s -o LICENSE https://raw.githubusercontent.com/github/choosealicense.com/gh-pages/_licenses/mit.txt
# CHANGELOG.md / CONTRIBUTING.md（参考 devguard/worklogs/decisions）
```

### Step 3：归档登记（仅登记，不删）

`project_management/archive/deleted_files/2026-07-21_planned-cleanup.md`：
- `docs/plan/后续升级计划/` → archive/
- AGENTS.md → 软链 CLAUDE.md
- `work/log_ai_*.txt` / `max-ai-*/` / `*.json` 用户拍板
- `__pycache__/` 全树（gitignore 已覆盖）

### Step 4：核心代码迁移（Round 8 起）

```bash
# 4.1 拆 domain/models.py
# 4.2 拆 cli.py + transcriber.py
# 4.3 application/services.py 新增
# 4.4 StageRegistry 化
```

## 迁移约束

- **不修改**：`conventions/`（规范冻结）/ `STATUS.md` 现有内容 / `docs/plan/` 已批准计划
- **不删除**：仅登记到 `archive/deleted_files/`
- **不破坏 public API**：所有 import 兼容
- **每步验证**：pytest 全绿 + ruff check

---

**报告完成时间**：2026-07-21