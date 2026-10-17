# research-tool 当前状态

更新：2026-10-17。历史基线为 4ab1412，审计提交为 60881ca，九段实现提交为 47f5888。已从 codex/nine-stage-only 建立 main、推送并设为 GitHub 默认分支。后续开发工作区 /Users/xbpd/.codex/worktrees/nine-stage-only/research-tool 位于 main；原目录仍在 master / ab2b0d6，29 tracked 与 10 untracked 文件哈希保持相同。

## 已实现

ResearchPipeline 是唯一正式九段调度：collect、clean、extract、knowledge、inspect、targeted、merge、qgate、report。inspect/qgate 先判断质量，CONTINUE 才触发 targeted/merge 并重检，STOP_SUCCESS 才报告。补搜正文再次清洗和抽取。旧分派、brief/fast、旧独立阶段 CLI、quick_collect、shadow/flags 及旧 deepen 配置均已移除或拒绝。

Deepen 有价值的实体拆分、画像、时间线、消歧和查询扩展迁入 collect 内部 ResearchStrategies。PDF/视频保留为输入适配。来源正文哈希、精确跨度、增量合并、运行锁、原子状态和 CAS 已接入。

最终产物位于主题下 runs/<run_id>/final/。CLI 成功出口、status、WebUI 和 wiki-stage 统一校验当前 run_id、runtime 阶段、文件集合和 SHA-256。旧根目录报告/知识树保持原样。

## 验证与证据

| 检查 | 结果 | 证据 |
|---|---|---|
| 全量 pytest | 957 passed / 3 skipped | /private/tmp/research-tool-nine-reviewed-all.xml |
| Ruff | 通过 | ruff check --no-cache research_tool/ pyproject.toml |
| 差异空白 | 通过 | git diff --check |
| 本地九段冒烟 | 补搜闭环和零模型调用恢复通过 | /private/tmp/research-tool-nine-accepted-smoke/smoke-receipt.json |
| CLI run / resume | 完整链、补搜和已校验缓存复用通过 | /private/tmp/research-tool-nine-ui-acceptance/cli-reviewed.log、cli-resume.log |
| CLI 成果损坏 | 运行结束后损坏报告会退出失败 | test_cli_validates_committed_report_before_success |
| 真实浏览器成功 | 完整九段、补搜和下载通过 | /private/tmp/research-tool-nine-ui-acceptance/reviewed-success.snapshot.txt、reviewed-success.download.txt |
| 真实浏览器失败 | 鉴权失败后清空报告与下载 | /private/tmp/research-tool-nine-ui-acceptance/reviewed-failure.json.txt |
| status / Wiki | 当前成果校验和不可变打包通过 | /private/tmp/research-tool-nine-ui-acceptance/cli-reviewed-status.log、cli-reviewed-wiki.log |
| wheel | 构建及关键模块/旧调度缺席检查通过 | /private/tmp/research-tool-nine-wheel-accepted/research_tool-0.1.1-py3-none-any.whl |
| 原工作区保护 | 29 tracked、10 untracked 哈希及 HEAD 相同 | /private/tmp/research-tool-original-workspace-20261017.json |

浏览器下载报告 HTTP 200，SHA-256 为 8cc44cc5a88b27b3f19ed3488c0fc2a37ddac14c43750d4e813ae876ea8480c1，与当前成功运行摘要相同。失败运行 STOP_ERROR、pipeline_complete=false，无 final 指针，旧文件没有变化。

三个 skip 为两项 Windows-only cookie 测试和未在 dist 放置 wheel 的检查；本轮已单独构建 wheel 并验证同一打包内容要求。Ruff 使用 /Users/xbpd/.local/bin/ruff，Python 使用 /opt/anaconda3/envs/research-tool/bin/python。

Pi /review 对入口进行了审查：CLI 成功出口缺少再次校验的告警已复现并修复，另一个坏 runtime schema 告警经加载器校验及四种异常 JSON 形状复现判为误报。原始报告在 /private/tmp/research-tool-nine-entry-review.md。

## 验证边界

冒烟、CLI 和 UI 验收只替换搜索、抓取和模型外部 I/O；本地生产管线、文件状态、报告及浏览器交互实际执行。它们不代表联网调研质量。模型支持关系核验也可能出错，不是语义真值证明。

本轮未调用真实研究 API、未运行 DeepResearch Bench，未复现历史 52.77。恢复基线及限制见 [NINE-STAGE-BASELINE.json](docs/plan/NINE-STAGE-BASELINE.json)；裁剪依据见 [实施记录](docs/plan/NINE-STAGE-ONLY-TRIM-PLAN.md)。

独立核心审查发现的四项问题已修复：旧模型缺口只有绑定本轮新增事实和解决理由才能关闭；正式主链拒绝 depth=1，snippet/discovery 元数据不进入事实账本；报告标题一并做支持核验；墙钟超时记录阶段及失败指标。Wiki 来源正文另按内容哈希归档，archiveRef 可在包内取回，幂等重试检查篡改。运行契约版本进入输入指纹，避免误用收紧前缓存。

本轮已完成实现、验收、独立审查修复、提交、main 推送与默认分支切换，并独立回读确认。旧 master 与恢复开发分支保留。后续从上述工作区的 main 继续开发；真实联网质量和历史 52.77 复现仍未验证。

Git 推送钩子在正确的 research-tool Python 3.11 环境下另行重跑，结果为 957 passed / 3 skipped。首次误用系统 Python 3.12 因缺依赖失败；没有绕过提交或推送钩子。
