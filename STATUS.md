# research-tool 当前状态

## 当前：自动报告根因、修复方案与版本检查点（2026-10-23）

用户要求追查医学解读错误与本地文字Agent检索系统的关系，并提供方案、及时提交推送。已完成[根因与分阶段修复方案](docs/plan/2026-10-23-researchctl-grounded-report-repair.md)，含PNG流程图、证据案例及10项反例验收；**P0–P3尚未实施，自动医学终验仍失败**。

根因已由真实run原件/源码核验：Casini年龄产次调整、PRO-RBDD5女性10妊娠均在全量账本却未进入40条核心选材；Sun部分调整OR只在clean表格，未抽成facts。起草与支持核验共用所选局部证据，不能据此证明“原论文未报告”。ResearchCTL现有接线在_summary末尾，未被成文前消费；本次配置未启用refresh。用户指定文字系统主树bbbe00b无research-tool layout，adapter在独立553b3ab工作树；真实生产manifest缺consumer要求的title，summary123文件又与consumer要求等于record4文件的断言不符。既有双方fixture各自通过不能替代生产输出互操作验收。

方案先修P0双边合同/审校采用状态，再P1成文前证据snapshot与全量原文查询、P2原子论断正反证及冲突、P3新run独立验收；不新增搜索引擎或向量库，不猜解原论文矛盾，不用索引成功冒充医学正确。外部两文字系统与医学运行仅只读，170个原成功run文件前后SHA一致；未修改被过滤端点配置或再调用。

前期16文件修补已提交`fe7677960cdb0efac360dc93d9a8ffd0a9c6321d`。首次push被OCR取消测试读取空PID文件拦住，已用延迟发布探针复现；最小修复仅fixture写完pid.pending后rename，原取消/reap/无活子进程断言不变，提交`5917608`。18项相关及真实pre-push全量 **1214 passed / 3 skipped** 通过，两提交已推送`origin/fix/structured-extraction-diagnostics-20261020`；没有绕过hook或推main。Ruff、差异、锁文件与shell检查通过；未发布新包版本/tag，Git提交为版本身份。

本轮诊断与验证：`research-output/report-grounding-20261022/`（目录沿用任务开始名，实际核验2026-10-23），含omission-trace、adapter probes、合成错误verifier反例、PID红绿/推送日志。未提交论文、运行原件、凭据、锁/CI/归档；未改外部dirty工作树。下一步是方案P0真实producer→consumer合同验收，不是再加提示词或重复付费生成。

以下2026-10-22及更早状态保留为历史快照；其中“未提交/推送”描述不覆盖上述当前版本记录。

## 历史：分层报告合同与医学回读（2026-10-22）

用户明确批准“证据与局限分层报告”，已新增显式`reporter.evidence_policy=strict|layered`（strict默认），核心选择maxItems硬上限/一次纠正、短模型编号映回原ledger ID、完整selected/unselected审计、来源内容家族覆盖、全部finding与known_limitations直接可见。最后有界补搜轮允许layered报告，保留strict_verdict/high/total；未解问题不关闭，STOP_SUCCESS不等于科学未知或人类验收清零。canonical run-record严格字段不变，evidence-audit.json与全部final文件进入summary/SHA检查。

新真实10源运行`53a9c17d04834a2696ac0819f9e36348`（原8篇+用户只读库新增Shapiro35例/Miesbach37例）：9阶段completed，1558facts、118nodes、40核心选用/1518未选、24正文段、105finding+21known limits，机器STOP_SUCCESS，123final hashes/引用span通过。但医学回读发现无症状人数冲突/以选材遗漏误称论文无调整或无分母，**此自动版未作为最终医学报告采用，原件哈希保留**。

此前本会话把Shapiro图1“10/35”解释为子组，**已由原PDF印刷p222推翻并撤回**：图注为全部35受试者，图1A10/35、同页正文15/35、摘要20/35均应并列作口径冲突，不能用猜测消解。25/35无促止血的阴性挑战原句仍直接成立。纠正记录在医学工作区source-page-checks/，没有偷偷改历史config或自动final。

加入原文上下文和反向约束后的运行`58cc82a47cfc4bbe80f506842e67e92e`，report端点明确`content_filter`、0正文/0completion，终态STOP_ERROR，无final；不绕过滤、不再调用该端点。已离线复现并修复报告/抽取阶段重试过滤请求的缺陷，新增测试保证仅一次请求立即终止；选材纠正耗尽也不被stage重试放大次数。

科研成果另存为Agent原文审校版`/Users/xbpd/Projects/纤维蛋白预测/runs/layered-report-20261021/审校分层报告.md`，连同105问题完整附录、26原句锚点、补件更新及运行边界，**不是新成功自动pipeline final或人类临床验收**。不得用机器支持检查替代原PDF/分母/独立性核对。最终回读另确认Miesbach原PDFp36的全体9人/子组12人无症状及血栓9/37与19%两处冲突，单列纠错与页图，不改原21项历史限制或自动审计。

最终全量 **1214 passed / 3 skipped**，Ruff、git diff、uv lock离线、shell语法通过；Pi review限定production scope最终approve/0（untracked测试不入diff限制明示），content-filter即停新增回归由红绿/全量实际验证。代码仍本地`fix/structured-extraction-diagnostics-20261020`未提交/推送；未改密钥/锁文件/CI/归档/两处只读源库。证据`research-output/layered-reporting-20261021/`；DAUG无ledger不可用，人工同步README/config/CODE_MAP。

## 当前恢复与证据完整性补修（2026-10-21，后续）

最终prompt历史运行`602fdd07c4344cc2a851c9bfb3601cf7`在限流约8小时后按原配置实际resume：85块缓存身份/原句先回验、collect/clean持久哈希通过，97块抽取完成，1150条选择记录去重1131facts/8源；后续补搜无新正文、qgate94项（86coverage+8model），仍STOP_BUDGET。旧失败summary/runtime/cache已先复制到医学工作区`resume-20261021/`，没有把启动或缓存存在当成功。

逐原文复核发现新的数据损坏根因：旧HTML正则`<[^>]+>`将`<0.7`至后文`>50`跨段当标签删掉；去标签粘连表格数字，短表又被导航过滤。已先写4项医学清洗回归（分别先红再绿），最小修复保留合法比较符、单元格/行边界及短表格；runtime输入契约8隔离旧clean提交。PDF两栏`pdftotext -layout`混行另改为新工作区默认阅读序副本并记来源hash/范围/单换行合并规则，不改原PDF或历史输入。

补搜planner另有可复现的预算饥饿：按finding字典序取前8，模型缺口全部被细主题coverage挤掉。新增3项回归旧实现2失败1通过；现在优先级排序后同级model/coverage交替选，保留全部finding和门控。实际独立补搜验证选4model/4coverage，落盘5页；回读含不相关/导航/摘要，不作为医学补证。PubMed简短关键词可命中46，旧超长查询零命中，不能说数据库无资料。

最新完整阅读输入8篇267937字符在新`evidence-integrity-20261021/`运行：99块extract、1247facts/103nodes/8源，所有span/hash/locator通过，32/102阴性挑战、22人手术阴性、80%/12%和比较阈值原句完整恢复。终态仍STOP_BUDGET，仅87coverage findings；新模型未报告旧8缺口不代表科学未知已解决，另建逐条原文锚定audit并明确`closed_in_pipeline=false`。未生成自动报告、未改质量阈值；是否允许单源局限型报告属于新契约，不能静默绕门控。

本轮全量 **1197 passed / 3 skipped**，Ruff、git diff --check、uv lock离线、shell语法通过；离线九段补搜/合并/零模型resume通过。继续保留本地分支未提交改动，不改锁文件/CI/归档/凭据/只读源库/旧报告。运行及审计：`/Users/xbpd/Projects/纤维蛋白预测/runs/evidence-integrity-20261021/`；回归证据：`research-output/targeted-query-fix-20261021/`。

## 当前修复续作：可验证原文片段选择（2026-10-21）

继续在 `fix/structured-extraction-diagnostics-20261020` 本地分支，保留上一轮全部未提交改动；本轮仍未提交/推送/建PR。将自由生成quote的提示改为从 `EVIDENCE_UNITS` 选择带chunk哈希作用域的 `evidence_id`，事实/引用/偏移由程序从原文生成；不做空白/NFKC模糊匹配，不接受跨块/未知ID，模型附加的改写text/quote不进入账本。精确quote输入兼容路径仍逐字校验。缓存契约`extract-evidence-v4-selection`、运行输入契约7隔离旧输入/提示缓存。

新增 `selection_invalid` / `quote_mismatch` 安全diagnostic含source、chunk索引/偏移/长度和selection索引；语义错误在抽取及阶段重试处显式拒绝盲重试。先写12项回归在旧实现全失败，后增加near-max块“提示不重复发送正文”回归；最终13项新增、受影响66项通过，全量 **1190 passed / 3 skipped**，离线九段含1轮补搜/零模型resume通过。Ruff、差异空白、锁文件离线校验和shell语法通过。

真实验证：同一原失败区域2800字符块，最终prompt一次生成16条事实、全部精确跨度通过，6.099秒。8篇正文版真实整批已完成extract与knowledge：97块、1175条选择记录去重1159条facts、8个来源，全部graph/span/hash/locator回读通过。原输入Fibrinogest首块纯作者/机构，模型正确返回空，原运行停止保留；随后只在独立正文副本移除明确文前元数据7210字符，保留原8篇305218字符及全部后续正文，记录每篇裁剪偏移/哈希，不改原件或放宽空结果检查。

整批后续执行inspect/qgate、targeted/merge及重检，但补搜无新增正文，质量门控仍有来源覆盖及真实证据缺口，终态`STOP_BUDGET`、`pipeline_complete=false`；**抽取修复成功不等于完整自动调研成功**。最终不重复正文的prompt独立同8篇重跑在85/97块后遇HTTP429停止，1099条部分选择记录全部回读通过，缓存保留；`STOP_ERROR`不是成功，不能采用旧报告冒充。限流后未密集盲重试。

现有Pi `/review`两项告警（语义失败隐式上下文重试识别、提示重复正文）均已修复，最终复审approve/0 findings；其报告明确两份untracked测试未包含diff，测试已实际执行红绿/全量。DAUG因仓库无ledger不可用，未初始化额外设施，现行README/CODE_MAP与本页人工同步。

工具修复证据：`research-output/evidence-selection-fix-20261020/`。医学重跑及文前裁剪证据：`/Users/xbpd/Projects/纤维蛋白预测/runs/rerun-evidence-selection-20261021/`。本轮没有覆盖历史报告/失败、两处只读源库、`.env`、`uv.lock`、CI或归档，也不声称临床模型/医学语义已验收。

## 当前修复：结构化抽取与安全诊断（2026-10-20）

分支 `fix/structured-extraction-diagnostics-20261020`，基于已合并 `origin/main`；尚未提交、推送或建PR。此前根目录同步PR #3已合入main，下面2026-10-19快照保持历史原文。

- 已修复：OpenAI-compatible结构化调用拒绝空正文、仅推理、无choices、截断/过滤/非正常结束；JSON/schema异常保留typed安全诊断，不再将空正文替成`{}`。正式抽取反复全空则停止，不写成功缓存；原文逐字引用检查与质量门控不放宽。
- `run-summary.stage_metrics[].diagnostic`、失败事件及`runs/<run_id>/failure-diagnostic.json`保留reason、结束原因、长度/SHA、可得token、JSON位置/schema字段与来源块；不保存响应/推理原文、headers、密钥。canonical `run-record.json`字段契约不变。runtime输入契约5、chunk契约`extract-evidence-v2`隔离旧缓存，旧运行原件保留。
- 已证实真实失败机制：同2800字符原研究清洗块、4096输出预算，实测`finish_reason=length`、正文0字符、completion_tokens=reasoning_tokens=4096；不能据此断言以前未保存响应全部同因。
- 已查DeepSeek官方thinking-mode参数，并实际验证本OpenCode Go模型支持显式`thinking: disabled`：同SHA输入、同预算，一次正常结束，reasoning_tokens=0、completion_tokens=2024、13条事实逐字引文全部通过，约10.841秒。另430字符原文真实抽取3条通过，约13.421秒。默认不猜模型参数、不自动加预算；显式本机示例位于忽略运行目录`nonthinking-run.yaml`。
- 验证：新增17项回归先在旧实现16 failed/1 passed，再全通过；受影响192 passed；全量隔离环境 **1177 passed / 3 skipped**；Ruff、差异空白、`uv lock --check --offline`、shell语法与示例配置解析通过。离线九段生产冒烟PASS，含一轮补搜合并、报告SHA核对与resume_model_calls=0；它仍模拟外部I/O，不是完整真实联网研究验收。
- 初次扩展回归暴露canonical run-record不可直接加字段，已改sidecar并复验检索契约。首次全量命令因显式禁用dotenv导致一项dotenv加载测试失败；改用隔离空RESEARCH_HOME保留dotenv行为后通过，不修改断言。原始失败日志均保留。
- 现有Pi `/review`已实际运行：生产补丁approve/0 findings；其报告明确新增untracked测试未进入diff，新增测试由红绿和全量验证。DAUG因未初始化ledger不可用，未擅自初始化；README、配置例和CODE_MAP按真实代码人工同步。

证据：`research-output/structured-extraction-fix-20261020/`，含red/affected/full日志XML、live-initial/live-original-block/live-nonthinking回执、offline-smoke、review.md及本地无密钥配置。未改 `.env`、`uv.lock`、CI/归档或两处只读医学资料库；未重跑整批医学调研，未声称完整九段或医学语义已验证。

## 当前快照（2026-10-19）

- 默认分支为 `main`。根目录重组已由 [PR #2](https://github.com/xiangbianpangde/research-tool/pull/2) 合入，merge commit `56f370e`；当前根文件同步工作在 `chore/sync-root-files`，本地实际分支以 `git status` 为准。
- 唯一 AI 工作入口为 `Agent.md`。`research_tool/`、`scripts/`、`.github/`、`config/`、`docs/`、`archive/` 保持原职责边界；Git/Python/工具要求的根配置不再移动。
- 本轮同步 `.gitignore`、`.env.example`、pre-commit/Ruff 配置和当前文档引用；`uv.lock`、已发布归档、CI 工作流及生产执行逻辑不变。README 中不存在的 `LICENSE` 链接已改为如实说明，未擅自新增许可证授权。
- 旧超时/README 抓取测试仍 mock 已不调用的同步 URL guard，导致真实 DNS 或跨测试 DNS 池状态影响验证。本轮只将两处 fixture 指向实际异步入口，保持原断言及生产 SSRF 检查不变；不再使用外部 shim 作为验收条件。
- 当前原始全量命令结果为 **1160 passed / 3 skipped**；Ruff、`uv lock --check --offline`、pre-commit schema 校验、shell 语法、当前 Markdown 链接和历史快照哈希检查通过。离线九段冒烟通过补搜闭环及零模型 resume。证据位于 `research-output/root-files-sync-20261019/`（本机忽略目录）。已推送并创建 [PR #3](https://github.com/xiangbianpangde/research-tool/pull/3)，目标为 `main`；首次远程 gitleaks 检查通过。本轮仅交付 PR，尚未合并。

以下保留历史实施与验收记录，不代表当前分支、测试数或产品接口仍停留在对应历史版本。

## 历史实施：唯一项目入口、Provider 可替换与联网可靠性（2026-10-17～18）

唯一日常开发目录为 `/Users/xbpd/Projects/research-tool`，当前分支 `main`。本轮修改已通过 PR #1 以 merge commit `cebbb1f` 合入；核心实现 commit `e419672`，历史修复分支已由归档标签 `archive/runtime-input-reliability-20261018` 保留；旧仓库和历史未提交工作保存于 `/Users/xbpd/Projects/archives/research_tool_archive/versions/2026-10-17T1840+0800/`；[版本制度与历史路径映射](docs/plan/PROJECT-VERSION-POLICY.md) 为入口。旧临时入口 `/Users/xbpd/Projects/research-tool-old` 已移入可恢复垃圾箱；Projects 下不再保留第二个日常 `research-tool*` 目录。

用户已确认 [实施计划](docs/plan/2026-10-17-runtime-portability-reliability.md)。Task 1/2/3/4 已通过专项与独立复审；Task 3 还覆盖了公平预算、专家 scoped deadline、exact-primary official gate、primary/secondary sibling 清理和 Collector 异步 DNS guard。最终全量回归为 **1136 passed / 3 skipped**；测试回执位于 `research-output/portability-reliability-20261017/final-validation-20261018/pytest-final3.xml`。targeted/证据/报告专项回归 **58 passed**。Ruff、`git diff --check`、`bash -n scripts/setup.sh`、`uv lock --check --offline` 通过。最终离线九阶段复验通过 `STOP_SUCCESS`、targeted/merge 和零模型 resume，回执位于 `research-output/portability-reliability-20261017/final-validation-20261018/offline-smoke-final/smoke-receipt.json`。本轮新增 OpenAI-compatible 原生 `LLM_SESSION_ID -> x-opencode-session`、流式 `choices=[]` 尾块兼容、仅对生产 OpenAI-compatible 客户端启用的抽取 chunk 级瞬态 JSON/空结果重试、空缓存保护、报告失败后一次受限证据 repair，以及 targeted 无正文补搜的空增量闭环。只用 `deepseek-v4.1-flash` 的 OpenCode Go 真实验收已完成：原生 session header、文本/结构化/流式探针通过；collect/clean/extract/knowledge/inspect/qgate/targeted/merge 完成，但 targeted 命中仅有 PubMed 页面、没有可核验正文，缺口保持开放，最终 `STOP_BUDGET`、`pipeline_complete=false`、CLI 退出码 1，拒绝生成不完整报告。完整回执位于 `research-output/portability-reliability-20261017/live-deepseek-v41-fixed/live-acceptance-receipt.json`；运行摘要位于其主题目录。没有把摘要/导航页写入证据账本，没有写入凭据。

医学补证已独立完成：Casini 2015 全文、Fibrinogest 公开 Supplementary Tables 1–5、风险报告、来源索引和可查看索引页位于 `research-output/medical-evidence-20261017/`；Fibrinogest 正文仍受访问限制，未报告效应量/有效分母保持不可估计。

## 上一轮交付：macOS CLI/SDK，供 AI Agent 使用

以下为 `96c18be` 历史交付记录。原工作区 `.codex/worktrees/nine-stage-only/research-tool` 已归档；当前使用上方唯一目录。历史修复分支和 PR #1 的旧状态仅作审计记录。按用户要求删除 WebUI 源码、research ui 命令、Gradio extra、部署档位和预检中的 UI 能力，以及旧 Windows 启动脚本。CLI/SDK 共用完整九段引擎，PDF、视频及浏览器搜索仍是输入/采集适配。

依赖锁文件从165项缩减为150项，移除Gradio相关15项，其他包版本没有变化。安装和打包不再包含WebUI。运行锁使用macOS/POSIX fcntl；原先新增的Windows锁模拟测试与实现已撤回。

macOS 27.0 / arm64 / Python 3.11.15 实跑：971 passed / 3 skipped，报告/private/tmp/research-cli-only-all.xml。Ruff、git diff --check和bash -n scripts/setup.sh通过。九段补搜/合并/重检/恢复冒烟证据在/private/tmp/research-cli-only-smoke/smoke-receipt.json。

CLI实际执行run、status、resume、鉴权失败和wiki-stage，日志与可复现外部I/O fixture在/private/tmp/research-cli-only-acceptance/。resume没有再次调用模型；失败不发布成功报告。wheel在/private/tmp/research-cli-only-wheel/，包内没有webui.py、Gradio依赖或ui extra，保留setup与MacOS分类。以上测试没有执行真实联网调研或复现历史52.77分。

干净环境/private/tmp/research-cli-only-env从wheel独立安装，未安装Gradio。使用python -I排除源码路径后，help/run/resume/status/wiki-stage成功退出0，鉴权失败退出1，已删除的ui命令退出2，pip check退出0。验收回执为/private/tmp/research-cli-only-acceptance/packaged-receipt.json。生产改动的Pi /review结果为approve/0 findings，报告/private/tmp/research-cli-only-review.md。

三个skip分别为两项Windows-only cookie测试和需要单独构建wheel的内容检查，wheel已在独立打包验证中核对。当前共享Python环境额外安装的arxiv与lxml仍存在此前披露的版本冲突；本轮不修改共享环境。

下列章节保留前几轮工作历史，不代表当前产品仍有界面，也不替代本节CLI-only验收。

更新：2026-10-17。历史基线为 4ab1412，审计提交为 60881ca，九段实现提交为 47f5888。已从 codex/nine-stage-only 建立 main、推送并设为 GitHub 默认分支。本轮优化工作区 /Users/xbpd/.codex/worktrees/nine-stage-only/research-tool 位于 fix/runtime-input-reliability，基于 main / 488ccea；原目录仍在 master / ab2b0d6，29 tracked 与 10 untracked 文件哈希保持相同。

## 本轮 ResearchCTL 接入（2026-10-18）

已在分支 `task/researchctl-integration` 完成 ResearchCTL 接入主链：

- 每个已初始化九段 run 生成 canonical `runs/<run_id>/run-record.json`，失败/预算/取消运行也保留快照；
- 生成严格 `retrieval-manifest.json`，区分 body/artifact hash、stage envelope、citation source、搜索计划/实际请求/保留正文；
- ResearchCTL 在隔离分支 `task/research-tool-layout` 提供严格 `research-tool` layout reader、current/history、sources/trace、reconcile、原子派生索引与路径/symlink/只读 DB 门禁；
- `research-tool retrieval` SDK/CLI、ResearchCTL Pi typed tool、DSH 插件支持 `--layout research-tool`；canonical 写入/事务动作在该 layout 下拒绝；
- 可选 `retrieval.refresh_on_run` 在九段结束后刷新派生索引，刷新失败写入 `.index/retrieval-refresh/<topic>.json` 为 `stale`，不改变研究 verdict；刷新设置不进入研究 run identity；
- 搜索审计使用调用级 journal，记录 planned/actual/success/failure/cancelled，不用 raw 文件数量冒充请求次数。

检查证据：research-tool 全量 `1157 passed / 3 skipped`；Ruff 和 `git diff --check` 通过；严格 ResearchCTL layout `7 passed`；DSH/Pi Node 测试 `8 passed / 7 skipped`。随后严格契约增强后 research-tool 全量为 `1160 passed / 3 skipped`，严格 layout 仍 `7 passed`，DSH/Pi 仍 `8 passed / 7 skipped`。真实临时 fixture 已完成跨仓库 `refresh/query/sources/trace/history/reconcile`，篡改后返回 `INDEX_STALE`；重建后 reconcile 返回 `HASH_MISMATCH`。DSH 的 wiki/真实 fixture 相关测试在隔离 worktree 因主工作树未提交依赖缺失而 skip；未进行真实联网科研质量验收。

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
