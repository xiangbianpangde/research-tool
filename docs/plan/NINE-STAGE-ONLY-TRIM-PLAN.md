# 九段唯一主链裁剪计划

状态（2026-10-17）：唯一九段、证据引用、补搜闭环和版本化成果消费已实现。全量957项通过，Ruff与CLI/浏览器验收通过；核心独立审查问题已修复，提交收尾中。最终状态与证据以 STATUS.md 为入口。

基线：分支 codex/nine-stage-only，提交 4ab1412a40b0c15824a23a63175538ffc267b4dc。该提交从 94a5c09f 恢复 52.77 跑批基底，并重放了历史会话中记录的四个源码补丁。主工作区 /Users/xbpd/Projects/research-tool 保持未修改。

## 目标

让正式 run / SDK 入口只进入一条可验证的九段主链：

collect → clean → extract → knowledge → inspect → targeted → merge → qgate → report

上面列出九种规范阶段，不要求在没有缺口时无条件补搜。控制流先由 inspect 交给 qgate；CONTINUE 才触发 targeted → merge，再回到 inspect/qgate；STOP_SUCCESS 才允许发布正式 report。targeted 取得的新资料仍须经过清洗和抽取。预算耗尽、取消或错误保留失败/未完成记录，不伪装成质量通过的报告。

## 最初审计阶段的范围（实现已获用户后续授权）

最初先建立分支并只读审计；用户随后要求“现在开始”“继续”，已授权实施。开发全部在本分支推进，完成验收后按用户授权作为主分支；原 master 与未提交内容须完整保留。

## 恢复基线的审计事实（历史，不是当前实现）

- ResearchPipeline 当前仍按 config.stages 执行旧的 collect → deepen → clean → extract → organize → report；nine_loop 只解析 flag，没有驱动分派。
- nine_loop/e2e.py 当前只有 collect → network → inspect → gate → report 五段，并且 collect 是离线 seed + identity 适配，不是搜索采集。
- clean_min、extract_min、knowledge_min、inspect_min、targeted_min、merge_min、qgate_min、report_min 均有确定性 envelope 函数，但输入契约目前没有形成九段可运行链。clean_min 只做 scheme 和身份哈希去重；extract_min 生成的是 locator span；knowledge_min 仍直接消费 collect，report_min 生成的是来源保留模板。不能把它们原样串接后声称具有真实正文清洗、语义事实抽取和调研报告能力。
- targeted_min 只生成 query 请求，merge_min 只消费调用者传入的 response fixture；真实 Collector/Fetcher 与九段响应契约还未接通。
- qgate_min 能返回 CONTINUE，现有 e2e.py 不消费该 verdict，也没有 targeted/merge 回边。
- merge_min 有 prev_digest 校验和输出 digest，但没有持久化 CAS 提交；现有 e2e.py 的原子阶段提交只覆盖五段。
- 旧 CLI 模式、独立阶段命令、quick_collect、视频适配器和 PDF 直通入口可绕过九段；shadow/eval 设施是迁移/评估工具，不是主链。
- inspect_min 把同内容哈希的跨来源边视为 high contradiction 候选，把无边节点视为 gap；这只是结构规则，不能等同于事实矛盾或调研盲区。新增补搜资料本身也不会自动解决这些 findings，须定义解决证据和重检规则。

关键源码定位（相对本分支基线，实施后行号可能变化）：

| 事实 | 位置 |
|---|---|
| flag 只装配；正式运行仍进入旧循环 | research_tool/application/pipeline.py:46、:187、:337、:627 |
| 模式表和默认六阶段 | research_tool/domain/config.py:25；research_tool/domain/models.py:359 |
| 五节点离线链，knowledge 直接吃 collect | research_tool/nine_loop/e2e.py:161、:180、:206、:219 |
| clean/extract 缺少正文语义链路 | research_tool/nine_loop/clean_min.py:138；extract_min.py:149 |
| 结构 finding 和模板查询 | research_tool/nine_loop/inspect_min.py:148、:169；targeted_min.py:128 |
| 内存 digest 校验并非持久化 CAS | research_tool/nine_loop/merge_min.py:174 |
| report 模板及 CONTINUE 不出报告 | research_tool/nine_loop/report_min.py:200、:224 |
| CLI 自选阶段和视频分流 | research_tool/presentation/cli.py:961、:978 |
| SDK 和视频旁路 | research_tool/__init__.py:132；infrastructure/ingest/pipeline_adapter.py:207 |

静态引用核对：正式应用代码中，DeepenStage 由旧 pipeline 导入；e2e 只由 shadow_e2e 导入；model_adapter 只由 report_augment 导入。shadow_e2e、report_augment 在正式包内没有其他静态 import 调用方。该结果不排除外部使用、动态导入或命令行直接执行，因此不是单独的删除许可。

## 保留边界

先保留，直到新链有测试证明替代关系成立：

- 真实 Collector、Fetcher、搜索后端、Tavily 多 Key、URL 安全和来源审计；它们是 collect/targeted 的采集基础。
- Cleaner 的正文清洗、去重和相关性过滤；Extractor 的真实 LLM 抽取；Organizer 的知识树组织和 Reporter 的正文报告能力。当前恢复版缺少可靠的正文硬截断、引用正文证据校验等能力；这些是接入时需补齐的验收条件，不得写成已有保障。
- rt_identity_adapter、状态写入、原子替换、输入指纹、CAS 冲突检查和证据哈希。
- PDF/视频摄入先保留为输入适配，不把它们当独立研究调度器；是否缩减这些产品功能另行决定。本轮没有更改其行为。

## 明确裁剪候选

在九段主链接通并通过对外入口回归后再删除：

- brief/fast 等阶段跳过模式及 --skip/stages 任意覆盖。full/standard/deep 中的预算、搜索深度配置可按实际需要留在单一九段配置内，不能继续切换执行链；是否保留同名参数由实施时的最小接口决定。
- 独立旧阶段 CLI（collect、clean、extract、organize、report）若不再作为调试接口公开。
- quick_collect()，因为它绕过完整主链。
- 旧 deepen 作为独立 stage、旧 organize 作为九段 stage、旧 backward loop；其中可复用的查询扩展、实体拆分和缺口发现能力必须先迁入 collect/inspect/targeted。
- shadow.py、shadow_e2e.py 和旧回退 flags 在正式单链建立后删除其运行接线；eval_quality.py 是离线工具，不是额外阶段，先只从运行依赖中分离。与现有 LLMClient 重复的 model_adapter/report_augment，在确认无保留调用方后裁掉。不要为了文件数把评测工具和产品输入混为冗余阶段。
- pipeline_adapter 当前的旧五阶段触发逻辑；视频保留时改为调用九段入口。

## 实施顺序与验收

1. 先写能暴露缺口的验收用例：主题输入能到最终报告；补搜能带入真实返回的正文、清洗及抽取证据；CONTINUE/错误不能发布报告；恢复时输入变更或产物损坏不能误跳。只模拟搜索/模型/身份外部边界，不模拟被测内部阶段或硬编码 verdict。
2. 在现有 ResearchPipeline 入口实现单一九段调度，统一阶段标识、Protocol v1 envelope、run state 和 StageEvent；不额外叠加一个长期兼容调度器，不移植当前 master 的整个后续实现。
3. 复用 Collector/Fetcher、Cleaner、Extractor、Organizer/Reporter 的必要正文能力，接通 collect → clean → extract → knowledge，提供真实来源、正文哈希和可验证 span，统一 knowledge 阶段名。Deepen 的检索策略先按用途并入 collect/inspect/targeted，随后移除独立 DeepenStage。
4. 接通 inspect/qgate → targeted → merge → inspect/qgate；targeted 调用同一采集适配，补搜资料先清洗抽取再合并。记录 resolving evidence，不靠删除 finding 或放宽阈值强行 STOP_SUCCESS。
5. 用现有原子写骨架实现包含轮次和输入指纹的持久状态、CAS 冲突拒绝和有界终止；错误/预算耗尽不自动变成成功。原五节点 e2e 要被替换，不能留作第二条正式主链。
6. 接入 report：只综合可追溯正文事实，逐项验证引用，CONTINUE/错误不发布。存在引用标记或 span 字段不能替代对实际报告内容的校验。
7. 将 CLI、SDK、WebUI、PDF/视频适配统一到九段入口；删除旧旁路和旧配置。
8. 在现有测试工具下验证完整主链：至少一次 CONTINUE→补搜→合并→重检→STOP_SUCCESS、无缺口直接通过、无来源、坏引用、预算停止、CAS 冲突和输入/产物变化后的恢复；并检查 CLI/SDK 和保留的 UI/摄入入口均不能回退旧链。
9. 运行受影响测试、Ruff、完整离线测试及可复现的 CLI 操作，清楚区分外部 mock、真实本地执行和真实联网。必要的真实付费调研/DRB 另用明确批次执行，不把离线结果作为成绩复现。
10. 验收完成后提升为主分支：先保留旧 master 和未提交工作，再使主分支最终文件树与验收分支一致。不能简单合并 master 的后续提交而重新引入已裁掉的代码，也不在当前有未提交改动的目录切换或 reset。

## 审计阶段排除项（后续实施以用户授权为准）

- 初次审计未删除代码；用户后续授权实施后已裁掉无调用的旧执行路径。
- 不运行真实联网调研、DRB、付费 LLM 或修改生产配置。
- 不把历史 52.77 作为新九段链的行为证明。
- 不把旧 Organizer/Deepen 的同名能力直接判为冗余。

## 基线验证与复现

- 基线恢复包：/Users/xbpd/Projects/research-tool/research-output/version-recovery-52.77
- 固定基线与原始证据哈希清单：[NINE-STAGE-BASELINE.json](NINE-STAGE-BASELINE.json)。它只记录历史恢复快照，不作为后续开发状态的第二个来源；当前进度从 STATUS.md 进入本计划。
- 固定提交：4ab1412a40b0c15824a23a63175538ffc267b4dc；四个补丁以外的跟踪文件与 94a5c09f 相同。
- 原恢复清单的 407 个文件全部逐字节匹配；按相对路径排序的 sha256 清单文本摘要为 d0c9de87ab3b1879a863adb6df65a645efe4eb5b604e628e9530ba35676d3505。
- 基线相关的 13 个测试模块、126 项测试通过：配置、SDK、旧管线、恢复、旧六阶段 E2E、flag 装配、搜索适配和 LLM 错误处理。它们不是完整九段 E2E 证明。
- 全包 Ruff：46 项原有告警，19 个文件；包括 e2e.py 缺 sys、flags.py 缺 pathlib 等。保留历史基线，不在恢复提交中顺手修复；迁移完成必须消除保留生产代码中的阻断错误。
- git diff --check 通过；未执行真实 UI、联网搜索、LLM、DRB 或成绩复现，未声称全套测试通过。
- 三份审计报告的子 Agent 均由工具明确指定 CPA/gpt-6-luna（high）启动。阶段能力/调用链结论已由主 Agent 读取源码复核，子 Agent 的“先增加并行九段分派”等建议未采纳为长期方案。

复现条件：Python 3.11.15，pytest 9.1.1，现有 /opt/anaconda3/envs/research-tool 环境。在本独立工作区运行：

    /opt/anaconda3/envs/research-tool/bin/python -B -m pytest -q -p no:cacheprovider --basetemp=/private/tmp/research-tool-nine-stage-baseline-4ab1412 --junitxml=/private/tmp/research-tool-nine-stage-baseline-4ab1412.xml research_tool/tests/test_config.py research_tool/tests/test_config_sdk_coverage.py research_tool/tests/test_pipeline_mock.py research_tool/tests/test_pipeline_coverage.py research_tool/tests/test_pipeline_backward.py research_tool/tests/test_pipeline_six_stage_e2e.py research_tool/tests/test_pipeline_reliability_profiles.py research_tool/tests/test_nine_loop_assembly.py research_tool/tests/test_verify_flags.py research_tool/tests/test_run_modes.py research_tool/tests/test_search_backends.py research_tool/tests/test_llm_client_auth.py research_tool/tests/test_llm_fail_fast.py

    /Users/xbpd/.local/bin/ruff check --no-cache research_tool/ pyproject.toml

原始报告：/private/tmp/research-tool-nine-stage-baseline-4ab1412.xml；/private/tmp/research-tool-nine-stage-baseline-4ab1412-ruff.json。本次不新增测试或 UI 单元测试，报告中记录的验收场景供下一步行为实现使用。


## 实施结果（2026-10-17）

唯一 ResearchPipeline 已取代旧分派；CONTINUE 实际补搜、清洗、抽取、合并并重检。来源正文与引用跨度校验、报告段落支持检查、原子持久状态和CAS已接通。Deepen策略在collect内部保留。旧flags/shadow、五节点fixture运行器与重复模型适配代码已删除；离线质量评测工具仍独立保留。

成果位于 runs/<run_id>/final/，旧根目录报告和树不移动或覆盖。CLI、status、WebUI与Wiki打包验证当前运行身份、阶段记录和完整文件哈希。测试保留有效失败行为并迁移旧入口契约。验收方法及证据从 [STATUS.md](../../STATUS.md) 进入。

独立审查收尾：模型缺口在后续轮次中默认保留，关闭必须引用本轮新增fact并说明理由；报告章节标题参与支持核验；depth=1及snippet/discovery记录不能作为正式正文；超时记录停止阶段。Wiki导出增加正文内容寻址blob并验证幂等重试中的内容完整性。新增针对性失败回归先复现后修复，最终957通过/3跳过。
