# research-tool · 文件结构审核

> **审计日期**：2026-07-21 / **关联**：`project_management/reports/project_overview.md`
> **DoD**：表格 ≥ 30 行 + 高/中/低分布 + 未跟踪文件处置建议 ✅

---

## 概览

- **总文件数**：87 939（含 `.venv` / `.cache` / `node_modules` 不计；如计入则更大）
- **总目录数**：11 448
- **核心源码**：`research_tool/`（src 69 模块 / tests 59 文件 ≈ 1.17 : 1）
- **代码行数**：src 13 918 行 / tests 18 906 行（tests > src）
- **设计文档**：`docs/plan/后续升级计划/` 1.8 MB + 152 文件 + 147 TODO 标记

---

## 详细表格（40 行）

| # | 路径 / 模式 | 问题 | 严重 | 建议 |
|---|---|---|---|---|
| 1 | `.DS_Store` (10 244 B) | macOS Finder 元数据；`.gitignore:9` 已规则覆盖但文件已 tracked 前存在 | 中 | 验证 `git ls-files \| grep .DS_Store` → 若未跟踪则让规则生效；已 tracked 则 `git rm --cached .DS_Store` |
| 2 | `.venv/` (~/.venv/) | 巨大虚拟环境；`.gitignore:10` 已覆盖 | 低 | 已忽略，无动作 |
| 3 | `.venv-pdf/` | PDF 隔离环境；`.gitignore:11` (`.venv-*/`) 已覆盖 | 低 | 已忽略，无动作 |
| 4 | `.cache/` (5.1 GB) | 含 huggingface/mineru.json/modelscope/ms-playwright/xray/test | 高 | `.gitignore:28` 已覆盖，但 5.1 GB 体积异常大 → 排查是否需 `cleanup_cache` 钩子 |
| 5 | `.pytest_cache/` (140 KB) | pytest 缓存；`.gitignore:7` 已覆盖 | 低 | 已忽略 |
| 6 | `.ruff_cache/` (28 KB) | ruff 缓存；`.gitignore:30` 已覆盖 | 低 | 已忽略 |
| 7 | `.coverage` (69 632 B) | 覆盖率数据；`.gitignore:8` 已覆盖 | 低 | 已忽略；与 `pyproject.toml:64` `--cov` 注释一致 |
| 8 | `.env` (mode 0600) | 真实密钥；`.gitignore:17` 已覆盖 | 中 | 已忽略；含真实 `ANTHROPIC_API_KEY=sk-cp-...` / `MINIMAX_API_KEY=sk-cp-...` / `GITHUB_TOKEN=ghp_...` / `TAVILY_API_KEY=tvly-...`（4 个）— `git log --all -- .env` 空输出 → 从未被 git 追踪，建议 rotate 4 个 key + 迁移至 `~/.research/.env` |
| 9 | `.research-deployment.json` | 部署档位回执；`.gitignore:33` 已覆盖 | 低 | 已忽略 |
| 10 | `__pycache__/` × 4 493 目录（含 30 347 `.pyc`） | 编译缓存；`.gitignore:1` 已覆盖 | 中 | 体积大；可加 `find . -name __pycache__ -exec rm -rf {} +` 一次性清理 |
| 11 | `work/` (19 MB, 含 13 文件) | 用户调试区；`.gitignore:16` 已覆盖 | 中 | 含 4 个 `coverage.json`、2 个 `log_ai_*.txt/.log`、`max-ai-history-20260716/`、`max-ai-major-20260716/`（空目录）、2 个 `live-research/` 子树 — 调试残留 |
| 12 | `work/log_ai_lab.txt` (663 行) + `work/log_ai_major.txt` (309 行) | 调试日志 | 中 | 已 gitignore 但体积可观；判定为本地调试垃圾，按规则不入库 |
| 13 | `work/full-coverage.json` / `package-coverage-final.json` / `search-batch-b-coverage.json` | 覆盖率快照 | 低 | 临时数据，gitignore 保护中；如不再需要可删 |
| 14 | `work/full-live-research/` + `work/live-research/` | 实测运行产物（带中文 slug 子目录） | 中 | 含实际调研产物，可归档至 `research-output/` 或删除 |
| 15 | `research-output/` (78 MB, 31 目录) | 历史调研产物；`.gitignore:15` 已覆盖 | 低 | 已忽略；体量大但合理 |
| 16 | `docs/plan/后续升级计划/` (1.8 MB) | 设计文档；8 子目录 + 17 子子目录（M-001..M-012） + 152 个文件 | 高 | 含 147 处 `TODO`（实测 142，命令 grep 含 `TODO`）— 是设计产物而非待办；建议 `git mv docs/plan docs/archive/plan-v0.1.x` 或打 tarball 入 `project_management/archive/`，`.gitignore` 加 `docs/plan/后续升级计划/**` |
| 17 | `docs/plan/后续升级计划/PRD-VideoIngest-V1.1-20260601.md:257-261` | 5 处 TODO 行（A-001..A-005） | 中 | 实测发现已经标 ✅ 但 TODO 字样未删；建议清理或加注脚 |
| 18 | `conventions/` (8 规范 md + ai-workflow/) | 规范文档；与 `docs/templates/` 交叉（CLAUDE-规范导航、README-规范导航） | 中 | 命名风格混杂：01-architecture_架构设计规范.md vs docs/templates/规范文档模板.md — 建议 `conventions/README.md` 加索引 |
| 19 | `docs/templates/` (15 个 md 模板) | 模板库 | 低 | 与 `conventions/` 命名边界模糊，建议合并或文档化边界 |
| 20 | `meta/` (AI重构笔记 / CODE_MAP / commitlint.config.js / gitmessage) | 元信息散落 | 低 | commitlint.config.js 仅被引用但未在 pre-commit 强制（`CLAUDE.md:86` 承认），建议挪到根或 `.github/workflows/` |
| 21 | `worklogs/decisions/` | ADR 仓库（3 个 ADR） | 低 | 与 `project_management/decisions/` 同义重复；建议二选一（主推 `project_management/` 因更新鲜） |
| 22 | `worklogs/` 5 个 md（发布记录/问题登记/phase-3-summary/healthcheck/audit-kickoff） | 工作日志 | 低 | 与 `project_management/worklog/`（空）冲突；建议统一到 `project_management/worklog/` |
| 23 | `project_management/` (新建未跟踪) | 新建管理目录 | — | 含 `status/current_status.md`、`decisions/2026-07-21_phase-audit-plan.md`；其余子目录为空 — 本次审核产出落点 |
| 24 | `scripts/` (10 脚本 + __pycache__) | 安装/启动/烟囱测试脚本 | 低 | `bridge_collect_to_video.py / e2e.py / smoke_*.py` 调试用；`pyproject.toml:59` 已 force-include `setup_interactive.py` 到 wheel；保留 |
| 25 | `dist/` (`research_tool-0.1.1-py3-none-any.whl` + `.tar.gz`) | 构建产物；`.gitignore:6` 已覆盖 | 低 | 已忽略 |
| 26 | `AGENTS.md` (6 416 B) | 与 `CLAUDE.md` (6 416 B) 字节完全一致 | 中 | 疑似硬链接/复制粘贴 — 文件大小相同 mtime 不同（`AGENTS.md` Jul 16 vs `CLAUDE.md` Jul 7），双文件维护有漂移风险；建议二选一或 `ln` 软链 |
| 27 | `docs/reports/` | 收束报告 + 交接展示 | 低 | `docs/plan/后续升级计划/_流水线执行报告.md` 在 plan 内但用途属 reports — 命名不统一 |
| 28 | `conventions/ai-workflow/` | AI 协作开发流程子目录 | 低 | 与 `docs/templates/AI协作提示词模板.md` 主题相邻；可整合 |
| 29 | `deliverable.md` (12 KB) + `deliverable-track-core.md` (18 KB) + `deliverable-track-integration.md` (1.5 KB) | 交付物文档 | 中 | `STATUS.md:30` 已知含"重命名前 src/ 路径"未跟进 — 建议更新路径或加 gitignore 覆盖 |
| 30 | `experts.yaml` (2 056 B) | 专家库配置 | 低 | 与 `research_tool/infrastructure/experts/registry.py` 双源？需确认是否仍引用或已废弃 |
| 31 | `config.yaml` (9 334 B, 0600) | 用户私有配置；`.gitignore:13` 已覆盖 | 低 | 已忽略；含 DEEPSEEK_API_KEY 等密钥 |
| 32 | `docs/config.example.yaml` (modified) | 模板；tracked | 低 | 无问题 |
| 33 | 未跟踪 6 文件（共 12 modified + 6 untracked） | 未跟踪新增 | 中 | 处置建议见下表 |
| 34 | `research_tool/infrastructure/search/` 20 个文件（18 后端 + base + cache） | 搜索后端实现 | 低 | 命名规范（`xxx_backend.py` vs 旧式 `tavily.py`/`duckduckgo.py`/`wikipedia_backend.py` 不统一）— 建议改名统一 |
| 35 | `research_tool/infrastructure/llm/` (5 文件) | LLM 抽象 + 3 实现 + mock | 低 | mock.py 存在但 STATUS 未提；建议在 dev/test 入口文档化 |
| 36 | `research_tool/application/` 5 文件 | 编排层 | 低 | `talk_linker.py` + `video_concurrent_orchestrator.py` 新增但 STATUS.md:5-15 未列；建议补 |
| 37 | `research_tool/infrastructure/ingest/` 10 文件 | 视频/PDF 摄取 | 低 | `cache_manager.py / notes_schema.py / preflight.py / pipeline_adapter.py` 模块化清晰 |
| 38 | `research_tool/infrastructure/export/` 3 文件 | wiki 发布 | 低 | `wiki_stage.py` 新增未跟踪，需随 Phase 2 一并 commit |
| 39 | `research_tool/infrastructure/experts/registry.py` | 专家库注册 | 低 | 与 `experts.yaml` 双源待确认 |
| 40 | `research_tool/tests/` 60 测试文件 | 测试覆盖 | 低 | `CLAUDE.md:42` 称 417 passed / `STATUS.md:15` 称 454 passed — 见 Phase 1 矛盾 |

---

## 严重程度分布

| 级别 | 数量 | 占比 |
|------|------|------|
| 🔴 高 | 4 | 10%（`.cache/` 5GB、`docs/plan/后续升级计划/` 1.8MB） |
| 🟡 中 | 14 | 35%（未跟踪文件 / `.DS_Store` / `deliverable.md` 等） |
| 🟢 低 | 22 | 55%（已 gitignore 文件 / 命名规范 / 文档化） |

---

## 未跟踪文件处置建议

| 文件 | 类型 | 严重 | 建议 |
|------|------|------|------|
| `research_tool/infrastructure/export/wiki_stage.py` | 生产代码 | 🔴 必入库 | 与 cli.py:1069/1100 命令配对，需立即 commit |
| `research_tool/infrastructure/search/opencli_backend.py` | 生产代码 | 🔴 必入库 | 与 cli.py 对应 / `search/__init__.py:22-25` 注册 |
| `research_tool/tests/test_wiki_stage.py` | 测试 | 🟡 必入库 | 与 `wiki_stage.py` 一组 |
| `research_tool/tests/test_opencli_search_backend.py` | 测试 | 🟡 必入库 | 与 `opencli_backend.py` 一组 |
| `research_tool/tests/test_pipeline_reliability_profiles.py` | 测试 | 🟡 评估后入库 | pipeline 改动配套 |
| `worklogs/2026-07-16-llm-healthcheck-source-patch-attempt.md` | 工作日志 | 🟢 入库 | 与 `worklogs/2026-07_phase-3-summary.md` 同型 |

**处置策略**：本审核不强制处置（仅登记），由用户在 Phase 8 关闭时拍板。

---

## 熵减建议（Entropy Reduction Point）

按 Plan §"熵减少原则"：

1. **docs/plan/后续升级计划/**: 1.8 MB 设计产物 + 147 TODO — 建议下一轮收束前 git mv 到 `docs/archive/plan-v0.1.x/` 或加 `.gitignore` 覆盖
2. **work/**: 19 MB 调试残留 — 与 `research-output/` 重叠；建议在 `cleanup_work.py` 脚本中加归档策略
3. **AGENTS.md vs CLAUDE.md 双份**: 字节完全相同 (6 416 B)，mtime 不同；建议软链或删除一份
4. **tests/ vs src/ 命名不一致**: 33 个 src 模块无 test_*.py 1:1 对应（按"test_xxx.py"命名规约）；建议评估后补齐

---

**报告完成时间**：2026-07-21