# research-tool 当前状态

## 本轮实施：唯一项目入口、Provider 可替换与联网可靠性（2026-10-17～18）

唯一日常开发目录为 `/Users/xbpd/Projects/research-tool`，分支 `fix/runtime-input-reliability`。本轮修改已提交并推送到 `fix/runtime-input-reliability`（核心实现 commit `e419672`）；旧仓库和历史未提交工作保存于 `/Users/xbpd/Projects/archives/research_tool_archive/versions/2026-10-17T1840+0800/`；[版本制度与历史路径映射](docs/plan/PROJECT-VERSION-POLICY.md) 为入口。旧临时入口 `/Users/xbpd/Projects/research-tool-old` 已移入可恢复垃圾箱；Projects 下不再保留第二个日常 `research-tool*` 目录。

用户已确认 [实施计划](docs/plan/2026-10-17-runtime-portability-reliability.md)。Task 1/2/3/4 已通过专项与独立复审；Task 3 还覆盖了公平预算、专家 scoped deadline、exact-primary official gate、primary/secondary sibling 清理和 Collector 异步 DNS guard。最终全量回归为 **1136 passed / 3 skipped**；测试回执位于 `research-output/portability-reliability-20261017/final-validation-20261018/pytest-final3.xml`。targeted/证据/报告专项回归 **58 passed**。Ruff、`git diff --check`、`bash -n scripts/setup.sh`、`uv lock --check --offline` 通过。最终离线九阶段复验通过 `STOP_SUCCESS`、targeted/merge 和零模型 resume，回执位于 `research-output/portability-reliability-20261017/final-validation-20261018/offline-smoke-final/smoke-receipt.json`。本轮新增 OpenAI-compatible 原生 `LLM_SESSION_ID -> x-opencode-session`、流式 `choices=[]` 尾块兼容、仅对生产 OpenAI-compatible 客户端启用的抽取 chunk 级瞬态 JSON/空结果重试、空缓存保护、报告失败后一次受限证据 repair，以及 targeted 无正文补搜的空增量闭环。只用 `deepseek-v4.1-flash` 的 OpenCode Go 真实验收已完成：原生 session header、文本/结构化/流式探针通过；collect/clean/extract/knowledge/inspect/qgate/targeted/merge 完成，但 targeted 命中仅有 PubMed 页面、没有可核验正文，缺口保持开放，最终 `STOP_BUDGET`、`pipeline_complete=false`、CLI 退出码 1，拒绝生成不完整报告。完整回执位于 `research-output/portability-reliability-20261017/live-deepseek-v41-fixed/live-acceptance-receipt.json`；运行摘要位于其主题目录。没有把摘要/导航页写入证据账本，没有写入凭据。

医学补证已独立完成：Casini 2015 全文、Fibrinogest 公开 Supplementary Tables 1–5、风险报告、来源索引和可查看索引页位于 `research-output/medical-evidence-20261017/`；Fibrinogest 正文仍受访问限制，未报告效应量/有效分母保持不可估计。

## 上一轮交付：macOS CLI/SDK，供 AI Agent 使用

以下为 `96c18be` 历史交付记录。原工作区 `.codex/worktrees/nine-stage-only/research-tool` 已归档；当前使用上方唯一目录。分支 fix/runtime-input-reliability，PR #1 在最近回读时为草稿、未合并。按用户要求删除 WebUI 源码、research ui 命令、Gradio extra、部署档位和预检中的 UI 能力，以及旧 Windows 启动脚本。CLI/SDK 共用完整九段引擎，PDF、视频及浏览器搜索仍是输入/采集适配。

依赖锁文件从165项缩减为150项，移除Gradio相关15项，其他包版本没有变化。安装和打包不再包含WebUI。运行锁使用macOS/POSIX fcntl；原先新增的Windows锁模拟测试与实现已撤回。

macOS 27.0 / arm64 / Python 3.11.15 实跑：971 passed / 3 skipped，报告/private/tmp/research-cli-only-all.xml。Ruff、git diff --check和bash -n scripts/setup.sh通过。九段补搜/合并/重检/恢复冒烟证据在/private/tmp/research-cli-only-smoke/smoke-receipt.json。

CLI实际执行run、status、resume、鉴权失败和wiki-stage，日志与可复现外部I/O fixture在/private/tmp/research-cli-only-acceptance/。resume没有再次调用模型；失败不发布成功报告。wheel在/private/tmp/research-cli-only-wheel/，包内没有webui.py、Gradio依赖或ui extra，保留setup与MacOS分类。以上测试没有执行真实联网调研或复现历史52.77分。

干净环境/private/tmp/research-cli-only-env从wheel独立安装，未安装Gradio。使用python -I排除源码路径后，help/run/resume/status/wiki-stage成功退出0，鉴权失败退出1，已删除的ui命令退出2，pip check退出0。验收回执为/private/tmp/research-cli-only-acceptance/packaged-receipt.json。生产改动的Pi /review结果为approve/0 findings，报告/private/tmp/research-cli-only-review.md。

三个skip分别为两项Windows-only cookie测试和需要单独构建wheel的内容检查，wheel已在独立打包验证中核对。当前共享Python环境额外安装的arxiv与lxml仍存在此前披露的版本冲突；本轮不修改共享环境。

下列章节保留前几轮工作历史，不代表当前产品仍有界面，也不替代本节CLI-only验收。

更新：2026-10-17。历史基线为 4ab1412，审计提交为 60881ca，九段实现提交为 47f5888。已从 codex/nine-stage-only 建立 main、推送并设为 GitHub 默认分支。本轮优化工作区 /Users/xbpd/.codex/worktrees/nine-stage-only/research-tool 位于 fix/runtime-input-reliability，基于 main / 488ccea；原目录仍在 master / ab2b0d6，29 tracked 与 10 untracked 文件哈希保持相同。

## 已实现

ResearchPipeline 是唯一正式九段调度：collect、clean、extract、knowledge、inspect、targeted、merge、qgate、report。inspect/qgate 先判断质量，CONTINUE 才触发 targeted/merge 并重检，STOP_SUCCESS 才报告。补搜正文再次清洗和抽取。旧分派、brief/fast、旧独立阶段 CLI、quick_collect、shadow/flags 及旧 deepen 配置均已移除或拒绝。

Deepen 有价值的实体拆分、画像、时间线、消歧和查询扩展迁入 collect 内部 ResearchStrategies。PDF/视频保留为输入适配。来源正文哈希、精确跨度、增量合并、运行锁、原子状态和 CAS 已接入。

最终产物位于主题下 runs/<run_id>/final/。CLI 成功出口、status 和 wiki-stage 统一校验当前 run_id、runtime 阶段、文件集合和 SHA-256。旧根目录报告/知识树保持原样。

## 九段主线提升时的历史验收（47f5888）

以下表格记录主线提升时的历史结果；其中的界面验证不表示当前产品仍有 WebUI。当前 CLI-only 验收见文首。

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


## macOS 收敛时的历史记录（2026-10-17）

当前优化分支 fix/runtime-input-reliability，基于488ccea。用户要求面向 macOS 使用；已移除上一版新增的 Windows/msvcrt 锁分支与对应模拟测试，使用 macOS/POSIX fcntl。九段正式主链以及输入、并发、预算、talk、视频接力修复保留。本轮修复：

| 问题 | 改进 |
|---|---|
| 单PDF路径在目录检查时失败 | 与PdfIngestor共用文件枚举，支持单PDF及内容变化的新运行 |
| 输入指纹读取无关文件 | 只哈希实际摄入的Markdown/PDF，避免附件变化导致重复研究 |
| 同实例并发重入会污染主题和结果 | 实例非阻塞锁；关闭stream记取消并释放锁 |
| 重试等待和健康检查可超预算 | 同一剩余墙钟预算覆盖健康检查、执行与退避 |
| talk参数被静默忽略 | 接入collect；discovery明确分类，转写正文回收至本轮raw |
| 视频适配器load_config关键字错误 | 真实配置加载回归，修正path参数；异步重试用asyncio.sleep |
| macOS运行与路径 | 使用fcntl真实进程锁；CLI使用macOS路径；安装文档与包元数据声明macOS |
| HTML输出、双输入被静默忽略 | 正式主链明确拒绝html格式及同时设置input_dir/pdf_dir |

macOS 27.0 / arm64 / Python 3.11.15 实跑：967 passed / 3 skipped，报告/private/tmp/research-macos-all.xml；移除的是仅验证Windows模拟分支的1项测试，其余回归保留。Ruff、git diff --check、bash -n scripts/setup.sh通过。离线九段冒烟/private/tmp/research-macos-smoke/smoke-receipt.json通过，包含补搜与零模型恢复调用。uv lock --check --offline通过。macOS版本wheel在/private/tmp/research-macos-wheel/。

真实浏览器执行单PDF文件输入，报告下载HTTP200，SHA-256为09ae31e2bd336f473e0e15a841e5a1fb52e986b38ccc30c0fea4d46922bd8783，与运行摘要一致。复现脚本及证据在/private/tmp/research-opt-ui/server.py、success.snapshot.txt、success.download.txt；只模拟OCR/模型外部边界，管线和浏览器交互真实执行。Pi /review对五个生产文件给出approve/0 findings，原报告/private/tmp/research-opt-review.md。

平台范围：当前以macOS为验收目标，不再维护Windows锁适配或声称支持Windows；已验证本机真实跨进程排斥、CAS竞争、异常释放。项目原有的两项Windows-only cookie测试在macOS跳过，历史测试记录仅说明当时结果。当前共享Python环境pip check仍发现额外安装的arxiv4.0.0要求lxml>=6.1,<7，但当前lxml5.4.0；该arxiv包不是本项目arXiv后端依赖，uv.lock已锁定lxml6.1.1。没有擅自改共享环境，联网检索与研究质量/DRB仍未验证。原工作区39个既有文件哈希和HEAD复核未变。
