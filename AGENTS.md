# research-tool — AI 项目上下文

先核对 Git 根目录、分支和未提交状态，再读 [STATUS.md](STATUS.md)。事实以源码、runtime 与验收产物为准。来源与裁剪边界见 [裁剪记录](docs/plan/NINE-STAGE-ONLY-TRIM-PLAN.md)，使用说明见 [README.md](README.md)。

## 开发约定

- 基线 `4ab1412`；开发分支 codex/nine-stage-only，独立工作区 /Users/xbpd/.codex/worktrees/nine-stage-only/research-tool。用户授权验收后提升为主分支，当前状态见 STATUS。
- 原目录 /Users/xbpd/Projects/research-tool 的旧分支及未提交工作必须保留；不得 reset、clean、stash、覆盖或将其后续实现整体合回。
- 用户取消了子 Agent 指定模型限制。默认单 Agent，独立任务才委派，明确所有权并复核。
- 本次代码迁移可编辑必要源码；执行正式调研时不得为运行方便改源码/生产配置，用 CLI、临时配置与环境变量。

## 唯一正式主链

ResearchPipeline.run/stream 固定 collect、clean、extract、knowledge、inspect、targeted、merge、qgate、report。初次知识构建后 inspect/qgate；CONTINUE 才 targeted → merge → inspect/qgate，STOP_SUCCESS 才报告。补搜正文必须清洗和抽取。无缺口的 targeted/merge 记录为不适用；取消、失败或预算停止不能伪装成功。

禁止恢复旧五/六段分派、brief/fast、阶段跳过、quick_collect 或 shadow/kill-switch。旧 Deepen 的实体拆分、画像、时间线、消歧和查询扩展属于 collect 内部 ResearchStrategies。PDF/视频保留为输入适配。

## 源码与状态

- application/pipeline.py：唯一编排、恢复、预算和摘要。
- nine_loop/evidence.py、runtime_state.py：正文/span/hash、运行锁、原子状态及 CAS。
- nine_loop/knowledge_min.py、merge_min.py、research.py：图谱、增量合并、检视/门控/报告。
- presentation/cli.py、webui.py：CLI/Gradio；infrastructure/export/wiki_stage.py：当前成果校验与不可变包。
- infrastructure/stages/：真实采集/抓取/清洗/抽取/内部策略；domain/models.py、config.py：配置校验。路径均相对 research_tool/。

本主链使用现有 URL guard 与 Python 正文哈希；零调用旧 Rust 适配器已删除，不能宣称仍执行或逐比特等价。

阶段以 Protocol v1 envelope 提交；runtime-state.json 记录输入指纹、run_id、阶段和 latest digest。只读状态查看不得创建锁文件。恢复只复用输入和文件摘要有效的提交。

产物隔离到指定 output：主题下有 artifacts/<run_id>/、runs/<run_id>/initial/、targeted-N/、final/。final 含报告、sources、知识树和图 JSON。run-summary.json 指向当前运行及完整 final 哈希。不得移动或覆盖根目录历史 report.md/tree/。

状态/WebUI/Wiki 必须验证 run_id、runtime 阶段、final 文件集合及哈希。引用覆盖 1.0 是原文跨度及模型支持检查的结构指标，不代表语义真值或 52.77 分复现。

## 验证

环境 /opt/anaconda3/envs/research-tool/bin/python。运行受影响测试、全量 `python -B -m pytest -q -p no:cacheprovider research_tool/tests`、`ruff check --no-cache research_tool/ pyproject.toml`、`git diff --check`，以及隔离目录 scripts/smoke_e2e.py。

保留有效的搜索/模型/摄入/安全测试，不能恢复旧分派或删除失败场景变绿。UI 通过真实浏览器验证，不新增 UI 单元测试；区分实现、自动测试、外部 fixture、本地交互、联网质量与用户验收。

当前进度与交接维护 STATUS 入口；详细证据放现有 plan/reports 边界。历史基线清单不可改；未经用户要求不写长期记忆或个人全局规则。
