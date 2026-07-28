# 工作日志：research-tool 全面审核与演进规划 — 任务启动

> 日期: 2026-07-21
> 阶段: Phase 0（启动）
> 关联 ADR: 候选 0004/0005/0006
> 关联 Plan: `/Users/xbpd/.claude/plans/agile-pondering-graham.md`

## 做了什么

- [x] 启动 audit task：审核与演进规划项目化
- [x] 启动 Phase 0：创建长程管理结构
- [x] 写 plan 文件 `/Users/xbpd/.claude/plans/agile-pondering-graham.md`
- [x] 启动 3 个 Explore agent 收集事实（结构 / 架构 / 质量）
- [x] ExitPlanMode 获用户批准
- [x] 创建 `project_management/` 8 个子目录
- [x] `git status --short` 留痕（任务开始时）

## 启动时 git 状态（必填，审计前后对比基线）

```
 M README.md
 M docs/config.example.yaml
 M research_tool/application/pipeline.py
 M research_tool/domain/config.py
 M research_tool/domain/models.py
 M research_tool/infrastructure/export/wiki_publisher.py
 M research_tool/infrastructure/llm/anthropic_client.py
 M research_tool/infrastructure/llm/openai_client.py
 M research_tool/infrastructure/search/__init__.py
 M research_tool/infrastructure/stages/extractor.py
 M research_tool/infrastructure/stages/reporter.py
 M research_tool/presentation/cli.py
?? research_tool/infrastructure/export/wiki_stage.py
?? research_tool/infrastructure/search/opencli_backend.py
?? research_tool/tests/test_opencli_search_backend.py
?? research_tool/tests/test_pipeline_reliability_profiles.py
?? research_tool/tests/test_wiki_stage.py
?? worklogs/2026-07-16-llm-healthcheck-source-patch-attempt.md
```

- 当前分支：`master`
- 最近 5 个 commit：

```
f7222ea3 2026-07-16 feat: expand research workflows and diagnostics
64746bb0 2026-07-07 docs: close Phase 3 — STATUS/CLAUDE alignment + phase-3 summary
60e52c1b 2026-07-07 test(pipeline): cover backward loop + _invalidate_after_collect (P2-3)
d4a3ab8e 2026-07-07 docs: record M-010 reconciliation; STATUS update (R10)
09cc7639 2026-07-07 fix(errors): register legacy M-010 codes; reconcile raise/register mismatches
```

## 关键决策

| 决策 | 原因 | 影响 |
|------|------|------|
| 新建 `project_management/` 而非 `audit/` | 用户 prompt 规定 + 长期演进看板需要 | 8 子目录隔离审计产物 |
| 复用 research-tool 自有模板（`docs/templates/`）而非 devguard 模板 | 项目内模板优先级最高 | 风格一致，无需跨项目同步 |
| 不自动 commit / push | 用户未授权 | 所有产出物暂存工作区 |
| 仅追加 `STATUS.md` 不删除现有项 | 保护收束历史 | Phase 3 新发现项以增量方式登记 |
| 实验可 UNVERIFIED，禁止伪造 | plan §7 风险约束 | `validation_report.md` 强制诚实性章节 |

## 遇到的问题

- **STATUS.md vs CLAUDE.md 测试数矛盾（454 vs 417）**：以最近 commit `f7222ea3` (2026-07-16) 推断，CLAUDE.md 已过期；Phase 3 用 `pytest --collect-only` 复核权威值。
- **未跟踪文件较多（6 个）**：含 2 个生产代码（`wiki_stage.py` 已跟踪，`opencli_backend.py` 未跟踪）+ 3 个测试 + 1 个 worklog。Phase 2 列出处置建议，由用户拍板。

## 给下一位的交接

- Phase 1（项目整体分析）可立即启动 — 已有 README/STATUS/CLAUDE 充分素材
- Phase 2（文件结构审核）可与 Phase 1 并行
- Phase 3（代码质量）建议在 Phase 1/2 完成后启动（避免 grep 与 overview 重复）
- Phase 4/5 依赖 Phase 3 产出
- Phase 6 创新点 brainstorm 需要 Opus 模型
- Phase 7 网络 API 受限时降级为手动记录
- Phase 8 汇总 + HTML 渲染在所有 Phase 完成后启动

## 启动期产物（本文件已生成）

- `project_management/status/current_status.md` — 初始状态看板
- `project_management/decisions/2026-07-21_phase-audit-plan.md` — 计划本身的决策记录
- 计划文件：`/Users/xbpd/.claude/plans/agile-pondering-graham.md`