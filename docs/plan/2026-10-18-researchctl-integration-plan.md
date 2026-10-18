# research-tool 接入 ResearchCTL 方案

- 状态：已批准，进入实施
- 日期：2026-10-18
- 实施仓库：`/Users/xbpd/Projects/research-tool`
- 上游参考仓库：`/Users/xbpd/Projects/文字Agent检索系统`
- 当前开发分支：`task/researchctl-integration`
- 基线提交：`4266aaa`

## 1. 目标

把 ResearchCTL 的确定性研究状态检索能力接入当前 `research-tool`，使 Agent 能够在九段调研完成后，对主题、运行、报告、来源和证据链进行可追溯查询，同时保持当前九段主链和证据图不变。

目标链路：

```text
research-tool
  collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report
                                                        │
                                                        ▼
                                             retrieval-manifest.json
                                                        │
                                                        ▼
ResearchCTL
  index → query / sources / trace / history / reconcile
```

## 2. 核心决策

### 2.1 双方职责

- `research-tool` 是证据生产者：负责搜索、抓取正文、清洗、事实抽取、证据图、报告和运行状态。
- ResearchCTL 是研究状态检索层：负责跨运行索引、路径定位、哈希核验、历史版本和 provenance 查询。
- SQLite、`INDEX.md` 和 semantic index 都是派生物，不得成为第二真源。
- ResearchCTL 不替换 `research-tool` 的搜索后端，不实现为 `SearchBackend`。

### 2.2 接入方式

采用 **layout adapter + retrieval manifest**，而不是目录合并或源码整体复制：

1. `research-tool` 为每次运行生成 `runs/<run_id>/retrieval-manifest.json`。
2. ResearchCTL 增加 `research-tool` layout reader，读取现有九段产物。
3. ResearchCTL 将结果投影到自己的派生 SQLite 索引。
4. 查询通过统一 envelope 返回，索引过期、来源缺失、哈希不符或版本歧义时 Fail-Closed。

### 2.3 真源与派生物

`research-tool` 运行目录中的以下文件是 canonical evidence / runtime records：

```text
runs/<run_id>/initial/raw/**/*.md
runs/<run_id>/targeted-*/raw/**/*.md
runs/<run_id>/final/report.md
runs/<run_id>/final/sources.json
runs/<run_id>/final/tree/knowledge.json
artifacts/<run_id>/**/*.json
runtime-state.json
run-summary.json
```

以下文件是 ResearchCTL 派生物，可以删除并重建：

```text
.index/research.sqlite
index/INDEX.md
```

### 2.4 哈希语义

必须同时区分：

- `body_sha256`：正文 body 的 SHA-256，用于 `research-tool` 证据 span 和 claim 校验。
- `artifact_sha256`：完整 Markdown 文件字节的 SHA-256，用于物理文件完整性校验。
- `report_sha256`：完整报告文件字节的 SHA-256。
- `input_fingerprint`：运行输入、配置和运行合同的指纹。

不能把 `content_sha256` 直接当作完整文件哈希使用。

### 2.5 当前报告规则

第一版不在 `research-tool` 中新建 `reports/CURRENT.md`，避免产生第二份当前报告。

ResearchCTL 通过以下条件识别可作为当前候选的运行：

- `run-summary.json` 声明 `STOP_SUCCESS`；
- `pipeline_complete == true`；
- `runtime-state.json` 可读取且阶段产物哈希通过；
- final 文件集合和哈希通过；
- 多个候选可以按明确时间和 run identity 排序；无法判定时返回 `AMBIGUOUS_VERSION`。

失败、取消和预算停止的运行保留在历史执行记录中，但不能被投影为当前成功报告。

## 3. Retrieval Manifest v1

文件位置：

```text
runs/<run_id>/retrieval-manifest.json
```

它是运行级检索投影，不替代 `run-summary.json`、`runtime-state.json` 或 `knowledge.json`。

最小字段：

```json
{
  "schema_version": 1,
  "producer": "research-tool",
  "topic": "主题",
  "topic_slug": "主题目录",
  "run_id": "32 位运行 ID",
  "verdict": "STOP_SUCCESS",
  "pipeline_complete": true,
  "input_fingerprint": "sha256:...",
  "stages": {
    "collect": "completed",
    "clean": "completed",
    "extract": "completed",
    "knowledge": "completed",
    "inspect": "completed",
    "targeted": "skipped",
    "merge": "skipped",
    "qgate": "completed",
    "report": "completed"
  },
  "search": {
    "planned_query_count": 0,
    "actual_attempt_count": 0,
    "successful_hit_count": 0,
    "retained_body_source_count": 0,
    "failed_request_count": 0
  },
  "artifacts": {
    "report": "runs/<run_id>/final/report.md",
    "sources": "runs/<run_id>/final/sources.json",
    "knowledge": "runs/<run_id>/final/tree/knowledge.json"
  },
  "sources": []
}
```

来源记录至少包含：

```json
{
  "source_id": "research-tool source_id",
  "locator": "https://...",
  "title": "标题",
  "source_engine": "openalex",
  "evidence_kind": "body",
  "raw_path": "runs/<run_id>/initial/raw/01-example.md",
  "body_sha256": "sha256:...",
  "artifact_sha256": "sha256:...",
  "fetched_at": "..."
}
```

路径必须是相对于主题根目录的路径，不写入机器相关绝对路径。

## 4. 目录与模块边界

第一阶段只在当前仓库新增最小必要模块：

```text
research_tool/
└── infrastructure/
    └── retrieval/
        ├── __init__.py
        └── manifest.py
```

`manifest.py` 负责：

- 从已生成的运行产物读取 canonical 状态；
- 构建稳定、可序列化的 manifest；
- 计算正文哈希和完整 artifact 哈希；
- 使用原子写入保存 manifest；
- 不调用网络、不调用 LLM、不修改现有报告和证据图。

后续 ResearchCTL adapter 留在 `/Users/xbpd/Projects/文字Agent检索系统` 的独立工作分支，当前任务不覆盖该仓库的未提交工作树。

## 5. 分阶段路线

### Phase 0：合同固化

- 本方案和路线图进入 `docs/plan/`。
- 固定 manifest v1、哈希语义、状态映射和 Fail-Closed 规则。
- 不改变九段阶段顺序。

### Phase 1：运行 manifest

- 在 `research-tool` 生成 `retrieval-manifest.json`。
- 覆盖成功、失败、取消、预算停止和部分阶段完成状态。
- 保留 planned search space、actual attempts、retained body evidence 的分层统计。
- 添加无网络单元测试。

### Phase 2：ResearchCTL layout reader

- 在 ResearchCTL 中新增 `research-tool` layout reader。
- 识别 topic、run、report、source、knowledge graph 和 stage artifacts。
- 首先完成 `index / query / sources / trace / reconcile` 的只读路径。

### Phase 3：统一 SDK/CLI

- 为当前 Agent 提供 `research retrieval` 命令组和 Python SDK。
- 对 ResearchCTL envelope 做稳定适配。
- 不暴露 ResearchCTL 内部 SQLite 表结构。

### Phase 4：自动刷新派生索引

- 成功运行后可刷新 ResearchCTL 派生索引。
- 刷新失败不回滚已经成功的研究运行，但必须记录 stale 状态和恢复命令。
- 查询发现 stale 时拒绝返回旧结果。

### Phase 5：Agent 工具接入

- 让 Pi / DSH / MCode 通过统一 SDK 或 CLI 调用检索能力。
- 复用 ResearchCTL 现有技能和 typed tool，不复制第二份检索逻辑。

## 6. 明确不做

- 不把 ResearchCTL 作为 `SearchBackend`。
- 不替换 DDG、OpenAlex、arXiv、Crossref 等已有搜索后端。
- 不将 SQLite 或 `INDEX.md` 作为事实真源。
- 不把 snippet、discovery 或搜索导航页纳入正式证据。
- 不在 `research-tool` 目录中伪造第二份 `CURRENT.md`。
- 不把 ResearchCTL 当前未提交工作树整体复制或合并进来。
- 不在第一阶段接入 definition revision、实验写入事务或向量数据库。

## 7. 验收标准

### Manifest

- 成功运行产生 manifest，且 run_id、阶段状态和输入指纹与运行状态一致。
- 失败、取消和预算停止保留状态，但没有虚假的成功输出声明。
- 来源记录只包含正文证据或明确标记的发现元数据。
- body hash 与完整 artifact hash 语义分离且可复算。
- 所有路径相对于主题根目录。

### 完整性

- 修改或删除 source artifact 后，读取方能识别 `HASH_MISMATCH` 或 `SOURCE_MISSING`。
- manifest 重建不修改 canonical report、sources、knowledge 和 runtime state。
- 删除派生索引后可以重建，结果不依赖旧 SQLite 内容。

### 统计

必须分开报告：

```text
理论搜索空间
实际请求尝试
成功命中
保留正文来源
抽取事实
报告事实
```

不得用一个数量替代其他数量。

### 回归

- 受影响 pytest 通过。
- `ruff check --no-cache research_tool/ pyproject.toml` 通过。
- `git diff --check` 通过。
- 不改变现有九段主链、失败语义和最终报告校验。

## 8. 回滚

本批次所有变更都在 `task/researchctl-integration` 分支。若 manifest 第一阶段不满足契约，删除新增 retrieval 模块和对应 pipeline 接线即可恢复到基线；不需要 reset、clean、stash 或覆盖任何用户文件。
