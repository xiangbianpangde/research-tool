# research-tool Audit — 当前状态看板

> 生成日期: 2026-07-21
> 关联 Plan: `/Users/xbpd/.claude/plans/agile-pondering-graham.md`
> 关联 Worklog: `worklogs/2026-07-21_audit-kickoff.md`

## 整体进度

| Phase | 标题 | 状态 | 启动时间 | 完成时间 | 产出物 |
|-------|------|------|---------|---------|--------|
| 0 | 启动留痕 | ✅ | 2026-07-21 | 2026-07-21 | `worklogs/2026-07-21_audit-kickoff.md` + 8 子目录 |
| 1 | 项目整体分析 | ✅ | 2026-07-21 | 2026-07-21 | `reports/project_overview.md` |
| 2 | 文件结构审核 | ✅ | 2026-07-21 | 2026-07-21 | `reports/file_audit.md` |
| 3 | 代码质量审核 | ✅ | 2026-07-21 | 2026-07-21 | `problems/code_quality.md` |
| 4 | 架构审核 | ✅ | 2026-07-21 | 2026-07-21 | `reports/architecture_review.md` |
| 5 | 技术债务评级 | ✅ | 2026-07-21 | 2026-07-21 | `problems/technical_debt.md` |
| 6 | 创新点分析 | ✅ | 2026-07-21 | 2026-07-21 | `innovation/catalog.md` + `experiments/catalog.md` + `validation_report.md` |
| 7 | Star 增长分析 | ✅ | 2026-07-21 | 2026-07-21 | `reports/github_growth_analysis.md` + `star_growth_roadmap.md` |
| 8 | 汇总 + HTML | ✅ | 2026-07-21 | 2026-07-21 | `reports/final_report.html` + `future_roadmap.md` + `final_structure.md` + `entropy_management.md` |
| V | 验证轮（补全 UNKNOWN + 一致性检查） | ✅ | 2026-07-21 | 2026-07-21 | `status/project_facts.md` + `planning/` 4 文件 + worklog + decision |

## 阻塞项 / 风险

- ⚠️ P0-1 `.env` 含真实密钥（ANTHROPIC_API_KEY + MINIMAX_API_KEY），`.gitignore` 已排除但需 git history 复核（Phase 3 任务）
- ⚠️ P0-2 `cli.py:1073` 硬编码 `/Volumes/项目/research-output`（Phase 2 任务）
- ⚠️ P0-3 17 处 `except ... pass` 静默吞错（Phase 3 任务）
- ⚠️ P0-4 `addopts` 注释，CI 无 pytest 强制（Phase 5 任务）

## 已知矛盾点（验证轮已复核）

| 矛盾 | 文件 A | 文件 B | 处置结果 |
|---|---|---|---|
| 测试通过数 | STATUS.md: 454 | CLAUDE.md: 417 | ✅ 已解决：`pytest --collect-only` 实测 **840**（权威值） |
| 当前阶段 | STATUS.md: R12 | worklogs/phase-3-summary.md: R11 | ✅ 已确认：R12 为 docs-only |
| 命令数 | STATUS.md FP04: 11 | cli.py: 14 | ✅ 已解决：`grep -c @app.command` 实测 **14** |
| 搜索后端数 | STATUS.md FP02: 12 | search/ 目录: 15 具体实现 | ✅ 已解决：15 个具体 Backend 子类（排除抽象基类 + 装饰器） |

## 未跟踪文件登记（Phase 2 需拍板）

| 文件 | 类型 | 建议处置 |
|------|------|---------|
| `research_tool/infrastructure/export/wiki_stage.py` | 生产代码 | 建议合入 master |
| `research_tool/infrastructure/search/opencli_backend.py` | 生产代码 | 建议合入 master |
| `research_tool/tests/test_opencli_search_backend.py` | 测试 | 与 opencli_backend.py 一起合入 |
| `research_tool/tests/test_pipeline_reliability_profiles.py` | 测试 | 评估后决定 |
| `research_tool/tests/test_wiki_stage.py` | 测试 | 与 wiki_stage.py 一起合入 |
| `worklogs/2026-07-16-llm-healthcheck-source-patch-attempt.md` | 工作日志 | 建议保留（已留痕未修改源码） |

## 工作区状态（启动期基线）

- 分支：`master`
- 最近 commit：`f7222ea3 2026-07-16 feat: expand research workflows and diagnostics`
- 距今 5 天未提交
- 12 个 modified + 6 个 untracked（详见 kickoff worklog）