# ResearchCTL 接入路线图

> 对应方案：[2026-10-18-researchctl-integration-plan.md](2026-10-18-researchctl-integration-plan.md)

## 当前状态

- [x] 已完成两仓库只读架构核对
- [x] 已确认 `research-tool` 是证据生产者，ResearchCTL 是派生检索层
- [x] 已确认不把 ResearchCTL 实现为 `SearchBackend`
- [x] 已固定 manifest v1、哈希分层、状态边界和 Fail-Closed 原则
- [x] 已在 `research-tool` 创建任务分支 `task/researchctl-integration`
- [x] 已创建接入方案和路线图
- [x] 生成 `retrieval-manifest.json`
- [x] `research-tool` Retrieval SDK/CLI 薄适配器已接入
- [x] ResearchCTL 读取 `research-tool` layout
- [x] ResearchCTL 投影索引与 Fail-Closed 查询路由
- [x] 运行后可选刷新派生索引并记录 stale 状态
- [x] ResearchCTL Pi typed tool 支持 `research-tool` layout
- [x] DSH research tool 支持 `research-tool` layout 与 canonical 写入保护
- [x] MCode 可通过 `research retrieval` CLI/SDK 使用统一 Query Envelope

## 路线图

### M0：合同与边界

**目标**：让双方对输入、输出、真源、派生物和失败语义有单一理解。

交付：

- 方案文件
- 路线图
- manifest schema v1
- hash 语义说明
- 状态映射表

完成判据：

- 文档明确列出 canonical files 和 derived files。
- 文档明确区分 body hash、artifact hash、report hash、input fingerprint。
- 文档明确规定失败和 stale 不得伪装成功。

### M1：research-tool 运行检索清单

**目标**：每个九段运行都能产出 ResearchCTL 可读取的稳定 manifest。

文件范围：

```text
research_tool/infrastructure/retrieval/__init__.py
research_tool/infrastructure/retrieval/manifest.py
research_tool/infrastructure/retrieval/client.py
research_tool/application/pipeline.py
research_tool/presentation/cli.py
research_tool/tests/test_retrieval_manifest.py
research_tool/tests/test_retrieval_client.py
```

交付：

- manifest 数据模型或稳定构建函数
- 成功运行生成 manifest
- 非成功运行生成状态清单但不声明成功输出
- 来源 body/artifact 哈希
- 搜索空间 / 实际请求 / 保留正文统计
- 原子写入和路径 containment 校验
- `research retrieval` 公开 CLI/SDK 薄适配器

验收：

- fixture 测试覆盖成功、失败和 source 变更。
- 现有九段 pipeline 测试无回归。
- Retrieval SDK/CLI 不读取 ResearchCTL SQLite。
- `ruff` 和 `git diff --check` 通过。

### M2：ResearchCTL layout reader

**目标**：ResearchCTL 可以索引 `research-output/<topic>/`，不要求改变 `research-tool` 目录布局。

建议文件范围（外部仓库）：

```text
researchctl/layouts/base.py
researchctl/layouts/research_tool.py
researchctl/tests/test_research_tool_layout.py
```

交付：

- topic/run/report/source/evidence graph 识别
- 运行状态和 final 哈希核验
- 派生 SQLite 投影
- `INDEX.md` 导航投影

验收：

- 多 topic、多 run fixture 能索引。
- `STOP_ERROR`、`STOP_BUDGET` 不进入当前成功报告。
- source 缺失和 hash mismatch Fail-Closed。

### M3：只读查询接线

**目标**：统一暴露 `query / sources / trace / history / reconcile`。

交付：

- `research-tool` 内部 retrieval adapter
- 稳定 Query Envelope
- 错误码映射
- Python SDK
- CLI 子命令

验收：

- Agent 不需要了解 ResearchCTL 的 SQLite schema。
- identity/version/provenance 查询不走 semantic fallback。
- semantic 只用于模糊召回，并标注 advisory。

### M4：运行后派生索引刷新

**状态**：已实现；已用隔离 ResearchCTL worktree 和真实临时 v1 fixture 验收 `refresh/index/generate-index`。未进行真实联网科研质量验收。

**目标**：成功运行后维护最新派生索引，但不把索引失败混入研究成功状态。

交付：

- 可选 post-run index hook
- stale 状态记录
- 重建命令和诊断信息
- 不回滚 canonical run 的错误处理

验收：

- 索引刷新失败时，研究结果仍保持原有成功/失败语义。
- 查询发现 stale 后返回 `INDEX_STALE`，不读旧结果。
- 并发刷新不互相覆盖。

### M5：Agent 工具与技能接入

**状态**：Pi typed tool、DSH research tool 和 `research-tool retrieval` CLI/SDK 均已支持 `research-tool` layout；Fail-Closed 的 `process_ok/ok` 语义已统一。DSH wiki/真实 fixture 相关测试在隔离 worktree 因主工作树未提交依赖缺失而 skip。

**目标**：Pi、DSH、MCode Agent 通过同一检索入口使用跨运行状态。

交付：

- typed tool / CLI adapter
- 检索路线决策树
- 运行日志与调用审计
- 典型任务 fixture

验收：

- 当前结论先查当前成功运行。
- 来源问题下钻到 source artifact。
- 历史问题锁定具体 run 和 hash。
- 任何索引漂移、来源缺失或版本歧义都会停止并报告。

## 验收矩阵

| 能力 | M1 | M2 | M3 | M4 | M5 |
|---|---:|---:|---:|---:|---:|
| 运行身份与阶段状态 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 来源正文与哈希 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 跨运行索引 |  | ✅ | ✅ | ✅ | ✅ |
| 来源查询 |  | ✅ | ✅ | ✅ | ✅ |
| provenance trace |  | ✅ | ✅ | ✅ | ✅ |
| 历史演化 |  |  | ✅ | ✅ | ✅ |
| stale / fail-closed | ✅ | ✅ | ✅ | ✅ | ✅ |
| Agent 工具 |  |  | ✅ | ✅ | ✅ |

## 当前执行顺序

1. 固化 M0 文档。
2. 实现 M1 manifest 纯函数和写入器。
3. 在 pipeline 的最终收束处生成 manifest。
4. 增加专项测试并运行受影响回归。
5. 建立隔离的 ResearchCTL worktree。
6. 完成 M2 layout reader、索引投影和 Fail-Closed 查询。
7. 使用 `research-tool` 真实输出 fixture 验收 SDK/CLI 和跨仓库查询。
8. 实现可选 post-run refresh，并将刷新失败记录为 stale。
9. 再进入 Pi / DSH / MCode Agent 工具接入。

## 保护边界

- `research-tool` 当前开发分支：`task/researchctl-integration`。
- ResearchCTL 当前独立开发 worktree：`/Users/xbpd/Projects/researchctl-layout`，分支：`task/research-tool-layout`。
- `/Users/xbpd/Projects/文字Agent检索系统` 主工作树有大量未提交修改，不在其上写入或清理。
- 不修改用户已有未提交文件。
- 不提交或记录任何 API key、Cookie、Token、私钥或环境变量值。
