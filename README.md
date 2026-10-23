# research-tool

面向 AI Agent 的 macOS 命令行调研工具：给定主题，通过多源检索、正文清洗、事实抽取、证据检视与补搜，生成可追溯的报告和知识树。CLI 与 Python SDK 共用 `ResearchPipeline` 九阶段管线。

历史恢复基线为 `4ab1412`，开发来自 `codex/nine-stage-only`。当前验证与分支见 [STATUS.md](STATUS.md)；来源与裁剪边界见 [九段裁剪记录](docs/plan/NINE-STAGE-ONLY-TRIM-PLAN.md)。历史 52.77 分尚未在本实现上复现。

本机唯一开发入口为 `/Users/xbpd/Projects/research-tool`。默认分支 `main` 已通过 PR #2 完成根目录重组；其它旧版已归档，不再作为候选项目打开。路径映射、归档索引与恢复流程见 [版本与归档制度](docs/plan/PROJECT-VERSION-POLICY.md)，当前 AI Agent 入口为 [Agent.md](Agent.md)。

## 根目录结构

根目录只保留运行入口、项目配置和公开说明；当前文档、模板、记录和历史材料分别位于 `docs/` 与 `archive/`。

```text
config/          # 配置示例与专家库
docs/            # 架构、计划、规范、模板、记录、元信息、历史
archive/         # 可恢复的历史批次
research_tool/   # Python 包与测试
scripts/         # 可复用脚本
```

| 根文件 | 保留原因 |
|---|---|
| `.gitignore` | Git 从根约束本机配置、缓存和研究输出；不忽略当前文档或归档 |
| `.env.example` | 无真实凭据的环境变量模板；实际 `.env` 与 `.env.*` 为本地文件 |
| `.pre-commit-config.yaml` | 本地 Ruff/格式化/密钥检查配置；不会自动替换本机已有 Git hooks |
| `pyproject.toml`、`uv.lock` | Python 构建、CLI 入口、工具选项和依赖锁；锁文件由 uv 校验/生成 |
| `Agent.md`、`README.md`、`STATUS.md` | 分别作为 AI 工作入口、使用说明和当前状态，不互相复制正文 |

`.github/` 保留远程 CI；本机 `.venv/`、缓存和 `research-output/` 不进入 GitHub。当前 GitHub 工作流只执行 gitleaks，不能把其成功等同于全量 pytest。当前开发检查命令见下方“SDK 与开发”。

## 九阶段

| 阶段 | 职责 |
|---|---|
| ① collect | 多源采集或摄入 Markdown/PDF；内部保留实体拆分、画像、时间线、消歧与查询扩展 |
| ② clean | 正文降噪、近似去重、默认50,000字符上限；保留科学比较符与表格边界，可配置LLM相关性过滤 |
| ③ extract | 抽取事实，绑定原文、来源、正文哈希与偏移 |
| ④ knowledge | 聚合事实、来源和主题节点，拒绝无效引用 |
| ⑤ inspect | 检查独立来源覆盖，检视证据缺口、冲突与限制 |
| ⑥ targeted | 依据缺口实际补搜，新正文再清洗与抽取 |
| ⑦ merge | 保留原图并融合增量证据，检查摘要及身份冲突 |
| ⑧ qgate | 按 high / total findings 阈值确定 CONTINUE 或 STOP_SUCCESS |
| ⑨ report | 章节规划、正文综合、逐段模型支持核验，生成报告与知识树 |

初次知识构建后先 inspect/qgate；CONTINUE 才 targeted → merge 并重新检视。补搜查询先按high/medium/low排序，在同优先级的模型缺口和覆盖缺口间交替分配有限查询槽，避免大量细主题覆盖告警挤掉实质证据问题；查询选择不会关闭或降级finding。无缺口时记录补搜、合并为不适用。默认 `reporter.evidence_policy: strict` 只有缺口满足原阈值才出报告；预算耗尽、取消或错误不发布成功成果。显式 `layered` 模式在有界补搜结束后允许生成“证据与局限分层报告”，不关闭缺口、不提高阈值，且仍须通过事实/标题/段落支持核验。既有模型缺口只有在记录新增事实 ID 及解决理由后才可关闭，不能靠下一轮省略而消失。

阶段使用 Protocol v1 JSON envelope；runtime-state.json 记录输入指纹、run_id、阶段产物摘要与 CAS。`--resume` 只复用输入和已提交文件哈希均有效的缓存。损坏缓存使恢复失败；输入变化建立新运行。输入支持单个 PDF 或 PDF 目录，不能同时指定 input_dir 和 pdf_dir；指纹只包含实际摄入文件。

所有成果位于指定 output 的主题目录：`artifacts/<run_id>/` 保存阶段 envelope，`runs/<run_id>/initial/` 保存首次 raw/clean/extracted，`targeted-N/` 保存补搜资料。最终 `runs/<run_id>/final/` 包含 report.md、sources.json、tree/00-主表.md、tree/N*.md 和 tree/knowledge.json。根目录 run-summary.json 是当前索引；消费者校验运行身份、runtime 与全部 final 哈希。历史根目录报告和知识树保留原样。

引用覆盖率 1.0 表示报告段落绑定了通过原文跨度校验的事实并通过模型支持检查。标题和正文均接受支持核验。模型可能判断错误；这不是语义正确性的形式化证明或调研质量评分。

当前knowledge产物是可核验原文事实＋topic分桶，不是完整临床关系/因果网络。107finding（97coverage＋10模型问题）与28局限的只读审计已发现主题碎片、关系未接线和统一三源规则失配。[知识网络与问题处置设计](docs/plan/2026-10-23-finding-disposition-design.md)提出双线修正；现有显式处置API/CLI、单HEAD审核账本与用途评估保持原观察不变，具体实现与验收状态见[实施合同](docs/plan/2026-10-23-finding-disposition-implementation.md)。

真实医学回读曾发现“未选入报告的证据被误称原文未报告”。[根因与修复计划](docs/plan/2026-10-23-researchctl-grounded-report-repair.md)记录原始失败。显式`reporter.grounding_policy: source_grounded`现接入成文前已提交证据快照、ResearchCTL全量facts/完整正文反查、精确上下文入账后CAS合并/重检，以及中文原子论断的正反证检查；它仍不是任意医学语义正确或人类临床验收保证。默认model_support不宣称拥有新保证。

## 安装与配置

本版本面向 **macOS**，需要 Python 3.11 或更新版本，运行状态使用 macOS/POSIX 的 fcntl 文件锁。

安装核心：`pip install -e ".[llm,search]"`；PDF/视频等使用对应 optional extra 和本地工具。`research setup` 提供 minimal/recommended/full 档位，`research setup --check-only` 检查环境。

在 macOS 终端中进入本项目目录后，建立隔离环境并启动：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[llm,search]"
research run "你的研究主题" --dry-run
```

需要配置密钥和可选工具时，在同一环境运行 `bash scripts/setup.sh`。PDF 路径使用 `/Users/<用户名>/Documents/papers` 或具体的 .pdf 文件；MinerU 命令路径如 `.venv/bin/mineru`。

CLI overrides > 环境变量 > YAML > 默认值。完整模板见 [config/example.yaml](config/example.yaml)。密钥放本地 .env 或环境变量，不提交 Git；`research config` 显示脱敏配置。

OpenAI 兼容网关使用 `LLM_PROVIDER=openai_compatible`，同时明确指定 `LLM_MODEL`、`LLM_BASE_URL` 和 `LLM_API_KEY`。模型名和端点可以随时更换，无需修改源码；该 provider 不回退使用 OpenAI/MiniMax 等厂商密钥。已有 provider（openai/deepseek/minimax/anthropic/ollama）仍可用。切换 provider 或端点时，旧 YAML 密钥会被丢弃，只有新配置的凭据会生效。

```bash
# 密钥应已通过用户管理的环境注入，不要放入命令历史或报告
export LLM_PROVIDER=openai_compatible
export LLM_MODEL=your-model-id
export LLM_BASE_URL=https://your-gateway.example/v1
research config
research run "你的研究主题" --output ./research-output
```

需要额外 provider 参数时，可在 YAML 的 `llm.request_options.extra_body` 中明确配置，例如 `thinking: {type: disabled}`；仅适用于该端点实际支持的选项。该映射不能覆盖 model/messages/stream/response_format 等客户端字段，不用于存放凭据。`research config` 会递归脱敏嵌套敏感键。结构化请求仅在端点明确拒绝 response_format 功能时尝试一次提示词 JSON 兼容路径，仍校验 JSON/schema，并共享原请求超时预算；鉴权、额度、限流和超时不会触发这条兼容重试。

结构化响应会检查结束状态及可见正文：`length` 截断、内容过滤、缺少 choices、空正文或仅有推理内容会明确失败，不再把空正文替成 `{}`。明确 `content_filter` 在抽取及管线层均立即终止，不作为瞬时失败重试或通过改写请求规避。合法 JSON 仍须通过 schema 和正文逐字引用检查；正式抽取块两次返回全空结果时停止，不写成功缓存。通用 schema 本身允许的空列表（如没有检视缺口）不受影响。

正式抽取提示提供 `EVIDENCE_UNITS` 原文片段及带块哈希作用域的 `evidence_id`，模型返回 `claims:[{evidence_id,topic}]`，程序从原文生成事实、引文及偏移，不再要求模型重新抄写或重排PDF文本。未知ID、跨块ID和不匹配原文均拒绝，整块不写成功缓存；即使模型附加改写的text/quote，也不会进入账本。既有精确quote输入仍逐字核验，不做NFKC/空白模糊匹配。`selection_invalid`/`quote_mismatch`诊断含source/chunk及selection索引；它们是语义错误，不按瞬时网络故障盲重试。

输入应是有实质正文的阅读材料。作者机构名单等纯文前块可能合法地没有事实；工具不会为通过抽取而强造事实。需要去掉文前元数据时，请在原件之外生成有裁剪标记/哈希的正文副本，保留原始来源与全部摘要、方法、结果、讨论和限制。PDF多栏应先核对阅读顺序，不可把按物理行拼接的左右列直接当事实句。

HTML清洗只删除合法标签名形式的标记，保留 `<0.7`、`P < .05`、`>50` 等科学比较式，避免跨段误删。HTML表格转为有单元格分隔符和行界的文本；短表格不会因多行较短被当作导航删除。引文精确匹配只证明其匹配清洗后正文，不能替代原PDF/表格的身份、阅读顺序和医学语义核验。

失败摘要的 `stage_metrics[].diagnostic` 及 `diagnostic_path` 指向的 `runs/<run_id>/failure-diagnostic.json` 保留安全诊断：`reason`、结束原因、正文长度/SHA-256、可得的 token/推理 token 数、JSON位置或声明字段路径、来源ID、块及选择条目索引。不会保存响应正文、推理正文、headers 或密钥；`run-record.json` 的严格外部字段契约不变。新运行指纹/块缓存契约会隔离修复前的阶段产物，旧运行原件保留。

推理模型的输出预算可能被推理 token 耗尽，降低 temperature 不保证能限制推理。若诊断为 `truncated` 且 `reasoning_tokens` 接近 `completion_tokens`，应先核对端点文档，在单次运行配置中显式控制推理，或自行调整预算；工具不按模型名猜参数，也不自动增支出。当前 OpenCode Go 的 `deepseek-v4.1-flash` 已用同一2800字符公开原文块实测以下配置有效（其他端点不能据此推定兼容）：

```yaml
llm:
  max_tokens: 4096
  request_options:
    extra_body:
      thinking: {type: disabled}
```

将其放入不含密钥的本机 YAML，并用 `research --config /path/to/run.yaml run ...`；凭据继续由环境或本机忽略 `.env` 注入。不控制推理时同块出现4096个推理token、0正文并截断；显式关闭后一次输出13条通过逐字核验的事实。这证明单块抽取路径，不等于完整九段研究或医学结论已通过验收。

同一个 ResearchPipeline 实例同时只允许一个运行。健康检查、阶段执行和重试等待共同遵守总墙钟预算。正式输出固定 Markdown，reporter.format=html 会报配置错误。

full、standard、deep 执行同一九段链，collector.depth 必须至少为 2；搜索摘要和未转写的视频发现记录不能作为正文证据。brief/fast、--skip、阶段重排、关闭 extract、旧 deepen/max_backward_rounds 和 legacy/shadow 开关报错。画像策略使用 `collector.strategies`；补搜上限与时限使用 `nine_loop.max_rounds`、`nine_loop.wall_s_max`。

## 证据与局限分层报告（显式选择）

```yaml
reporter:
  evidence_policy: layered     # 默认strict；不静默修改旧合同
  max_selected_claims: 40      # 核心综述事实数上限；全量账本仍保留
  max_length: 45000
  known_limitations:
    - 缺乏患者×挑战完整分母，不能识别终身不出血或独立因果贡献。
```

`layered` 先执行现有inspect与有界targeted/merge，最后一轮可转入报告。qgate保留原 `strict_verdict`、high/total与全部finding；报告摘要额外标记 `report_contract=evidence_with_limitations/v1`、`strict_evidence_complete` 和 `unresolved_finding_count`。`STOP_SUCCESS` 在此只表示分层交付合同完成，**不表示科学缺口清零、临床模型有效或用户验收**。旧strict失败回执不会变成成功。

正文先有界选择核心证据（模型可见maxItems且最多一次纠正，耗尽不可由阶段重试重开），再分配章节并核验。模型交互短编号最终映回原始不可变claim_id；正文所选事实须全部进入支持核验。未选事实ID在 `final/evidence-audit.json`逐项列出，全量事实仍在`tree/knowledge.json`。报告直接可见来源内容家族覆盖/证据强度、所有未解决finding及显式输入的既有局限；`known_limitations`不因本轮模型未提及而消失。内容家族仅识别近似转载，**不等于独立研究/队列**；单家族/多家族不是GRADE等级。医学分母、亚型/共病、重复队列和原论文错误仍需原文审核。机器生成局限附录与模型核验的医学正文分开标注。分层模式下邻近原文上下文和显式限制用于反向核对表头/范围、否定与分母，不可扩充所引事实或把限制说明当真值；限制本身也可能错误，必须回原PDF裁定，不能凭猜测解释源文矛盾。

`evidence-audit.json`与报告、来源、树一并进入summary文件清单/SHA校验，canonical `run-record.json`严格字段不变。未通过schema、引用、支持或文件完整性仍失败；本模式不是关闭校验，也不把未知改成结论。

## 成文前证据回查与审校采用

```yaml
reporter:
  evidence_policy: layered
  grounding_policy: source_grounded
retrieval:
  module_root: /path/to/pinned-researchctl-checkout
```

reader必须握手支持`evidence-snapshot/v1`，旧CLI/legacy不回退。全部来源的raw/clean/hash与knowledge/merge已提交envelope先被固定，未选方法、分母和未抽表格仍可查；缺附件、分页截断与范围单列。初始/最终快照准备使用已有qgate提交与CAS，未提交中断可恢复，已提交文件丢失/变化硬拒绝。

`source_grounded`在同一九段report内生成中文原子陈述；verifier读全部本来源facts与完整正文，而非writer选材子集。生成接口只允许选择`code+claim_ids`，禁止模型自由输出医学结论；程序按明确的挑战/治疗/关联/冲突类型生成中文解读，每项旁逐字渲染账本数值/效应/分母原句，避免模型重标单位；全局否定和冲突仍有机械约束与一次有界修复。逐论断结果写入`final/grounding-audit.json`，失败时`runs/<id>/grounding-attempts.json`保留已生成原子项/具体失败索引/安全诊断，过滤立即停止。`source_exact`只输出原文陈述，是独立窄合同，不能冒充中文医学综合已通过。

任务可显式指定`reporter.required_source_evidence`（locator、精确quotes、kind=required/source_conflict），每句必须唯一绑定现有事实；缺失、重复或歧义硬拒绝。必带原句及任务方标记的冲突候选始终直接可见，不交模型裁掉；配置解释不自动成为真值。

原run-record字段不变；summary列全部已提交final，ResearchCTL核验实际目录与运行envelope集合。审校单独append-only绑定run/report/evidence哈希，不改执行verdict。采用查询使用`research retrieval query --entity CURRENT --require-review agent_source_review ...`；未审/拒绝/失败当前run不能回退旧机器success。Agent审校不匹配human_clinical要求。

## 问题处置（raw默认，reviewed显式）

`research issues <action> --registry <path> --request <json>`复用本机CLI；`view`只读、不自动初始化，`create/import/propose/review/propose-network/review-network`使用严格JSON请求。每次写入需要actor、idempotency_key、expected_head；提案和审核分开，审核锁定proposal hash与scope/network/policy，未认证actor只标self-asserted Agent。`Registry` SDK及字段示例见[实施合同](docs/plan/2026-10-23-finding-disposition-implementation.md)。

```yaml
disposition:
  policy: raw  # 默认旧门禁；不改变既有runtime19输入指纹
  registry_root: null
  expected_head_sha256: null
# reviewed需显式指定registry_root、expected_head_sha256，且retrieval.module_root必须支持issue-evaluation/v1
```

只有显式reviewed运行消费固定HEAD；来源/跨度/运行硬失败不可豁免。`final/issue-evaluation.json`同时显示raw findings、活动必需问题、约束、科学回答及新证据计数，嵌入可核验决策链和network/policy版本；summary/manifest声明required contract；仅reviewed manifest升为v2，raw仍v1，旧reader明确拒绝新合同；报告审校绑定sidecar。canonical run-record仍仅四个主final文件；全部实际final受summary/envelope哈希保护。分类、拆分、同义归并不增加独立研究；等待数据、不明分母、同库/转引不自动resolved。135条医学迁移仅预演：分类/网络提案均未审核，原107＋28仍未决、科学增益0。

## CLI

```bash
research run "固态电池商业化现状" -s tavily --mode full --output ./research-output
research run "Graph Neural Networks" -s openalex -s arxiv --from-year 2022 --to-year 2026
research run "电池循环寿命" --input-dir ./materials/raw
research run "长上下文技术" --pdf-dir ./papers --translate
research run "单篇论文" --pdf-dir ./papers/paper.pdf
research run "论文与演讲" -s arxiv --with-talks
research run "Transformer 原理" --video-url "https://www.bilibili.com/video/BV1xx411c7mD"
research run "电池循环寿命" --dry-run
research status ./research-output/topic-slug
research wiki-stage ./research-output/topic-slug --build --package-output ./wiki-packages
```

搜索保留 DuckDuckGo、Tavily、OpenAlex、Crossref、arXiv、Semantic Scholar、PubMed、Wikipedia、GitHub、Google News、CVPR/DBLP、Bilibili、YouTube、X 和 OpenCLI。`--official-url` 指定先抓取的官方来源，成功后扩展学术检索；其余配置见 `research run --help`。

talk 关联在 Collect 内部执行；发现笔记只供定位，不进入事实账本。设置 talk.ingest=true 后，成功转写的 Markdown 纳入当前运行的清洗与抽取。

独立 collect/clean/extract/organize/report 命令和 quick_collect 已移除。ingest-pdf 与 ocr-engines 是输入准备工具，正式报告使用 run。wiki-stage 只打包当前成功 final；旧 publish-wiki 不支持九段结果。

AI Agent 应使用 `research run`、`research status` 和 `research wiki-stage`；WebUI 与 `research ui` 命令已移除。视频/PDF 摄入继续由 CLI 驱动。

调用方应同时检查进程退出码和主题目录中的 `run-summary.json`：只有 `verdict=STOP_SUCCESS` 且 `pipeline_complete=true` 才能采用 `output_dir` 指向的报告。其余 verdict 表示本次任务未完成，不应采用历史报告。`research config` 返回脱敏 JSON，`run-summary.json` 提供运行身份、阶段记录和 final 文件哈希，适合 AI 读取和核验。

## SDK 与开发

```python
from research_tool import ResearchPipeline, load_config

config = load_config(overrides={
    "topic": "具身智能感知与规划",
    "work_dir": "./research-output",
    "collector": {"search_engines": ["tavily", "openalex"]},
})
# 在 async 函数中：
result = await ResearchPipeline(config).run()
if result.verdict == "STOP_SUCCESS" and result.report_result:
    print(result.report_result.report_path)
```

便捷入口 `research(topic, work_dir=..., llm_provider=..., llm_model=..., config_path=...)`；流式进度使用 ResearchPipeline.stream()。编排在 application/pipeline.py，证据、图谱、合并、报告和状态在 nine_loop/，外部能力在 infrastructure/。旧 Organizer/Reporter 底层工具仍有有效测试，但不是第二条正式执行链。

```bash
python -B -m pytest -q -p no:cacheprovider research_tool/tests
ruff check --no-cache research_tool/ pyproject.toml
python -B scripts/smoke_e2e.py --output /tmp/research-nine-smoke
```

冒烟仅模拟搜索、抓取和模型外部 I/O，实际执行生产管线，含一轮补搜和无模型调用的恢复。它不验证联网表现、报告质量或历史分数。完整回归与 CLI 验证证据见 STATUS。

[pyproject.toml](pyproject.toml) 中声明使用 MIT 许可；当前仓库尚未提供独立的 `LICENSE` 文件。
