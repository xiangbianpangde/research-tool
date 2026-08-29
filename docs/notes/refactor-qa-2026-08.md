## User

我打算重构这个项目，对于一个专业的企业级别的项目开发，正常的开发流程是什么，我应该考虑什么，请结合这个项目给我详细的回答 

## Assistant

先摸清这个项目的现状、架构和约束，再按企业级流程给出可落地的重构路径。

## Tools

- Read: /Users/xbpd/Projects/research-tool/STATUS.md
- Read: /Users/xbpd/Projects/research-tool/README.md (1-200)
- ListDir: .
- ListDir: worklogs/decisions
- ListDir: docs

## Assistant

项目里已经有一套规范与技术债清单。我会把它们和真实代码结构对上，再谈企业级流程该怎么落地。

## Tools

- Read: /Users/xbpd/Projects/research-tool/conventions/ai-workflow_AI协作开发流程/05-完整流程与核心原则.md
- Read: /Users/xbpd/Projects/research-tool/conventions/01-architecture_架构设计规范.md (1-147)
- Read: /Users/xbpd/Projects/research-tool/project_management/problems/technical_debt.md
- Read: /Users/xbpd/Projects/research-tool/project_management/reports/architecture_review.md (1-200)
- Read: /Users/xbpd/Projects/research-tool/project_management/planning/risk_register.md
- Read: /Users/xbpd/Projects/research-tool/worklogs/decisions/0001-六阶段管道文件系统通信.md

## Assistant

再核对测试、CI、公开 API 和当前未提交改动，避免流程建议脱离仓库现状。

## Tools

- Read: /Users/xbpd/Projects/research-tool/pyproject.toml
- Read: /Users/xbpd/Projects/research-tool/research_tool/__init__.py
- ListDir: .github
- Read: /Users/xbpd/Projects/research-tool/project_management/reports/future_roadmap.md (1-98)
- Read: /Users/xbpd/Projects/research-tool/conventions/05-testing_测试规范.md (1-80)
- Search: def (research|quick_collect|load_config)
- Read: /Users/xbpd/Projects/research-tool/research_tool/application/pipeline.py (1-80)
- Read: /Users/xbpd/Projects/research-tool/conventions/ai-workflow_AI协作开发流程/01-角色分工与文件体系.md (1-38)
- Search: import-linter|importlinter
- Read: /Users/xbpd/Projects/research-tool/research_tool/tests/test_arxiv_fulltext_and_originals.py (1-80)
- ListDir: research_tool/infrastructure
- Read: /Users/xbpd/Projects/research-tool/project_management/status/project_facts.md (1-80)

## Assistant

先给结论：**企业级开发不是再写一套更厚的文档，而是把「为什么改、改什么契约、怎么证明没坏、怎么可回滚」做成可重复的闸门。** 对 `research-tool` 来说，正确姿势是 **先冻结行为与产物契约，再按切片绞杀式重构，全程保持可发布**。不要推倒重写，也不要把 Netflix / 银行级流程原样套到这个单人、本地优先、约 1.9 万行源码的 CLI + SDK 上。

仓库里其实已经有相当完整的流程资产：`conventions/` 八份规范、AI 协作 0–3 步、ADR、`STATUS.md`、技术债表 TD-01～TD-25、VideoIngest 的八阶段设计包。缺口不在「没有流程」，而在 **流程写了但没自动化、文档和代码已经漂移、重构动机还没写成可验收的目标**。

---

## 1. 企业级流程到底在防什么

专业团队的流程不是为了看起来正规，而是控制这五件事：

| 要防的事 | 落到这个项目上 |
|----------|----------------|
| 改完不知道有没有坏 | 840 条测试可收集，但 **没有 pytest CI**，覆盖率门禁被注释掉 |
| 改了用户/调用方感知不到的契约 | `research()` / `quick_collect()`、Typer CLI、`config.yaml`、`raw/clean/extracted/tree/report.md` 都是对外契约 |
| 知识只在一个人脑子里 | bus factor = 1；`STATUS.md` 写 454 passed，架构审核写 417，事实表写 840 collect |
| 一次改太大，无法回滚 | 当前 `master` 上还有未提交的 arXiv 全文 / `_originals` 改动 |
| 安全与合规事故 | 风险表里 R-07 仍是密钥明文；CI 目前几乎只有 gitleaks |

所以：**流程的最小充分集 = 产品意图 + 契约清单 + 自动化闸门 + 增量交付 + 发布/回滚。** 再多的 Markdown 不会自动带来这五样。

---

## 2. 标准企业开发生命周期（以及你这里该怎么用）

下面是多数正经工程组织实际在跑的闭环。右边是结合本仓库的落地方式，而不是抽象教材。

```
发现 / 意图
  → 需求与验收（含非功能）
    → 架构与契约（ADR）
      → 切片计划与风险排序
        → 实现（TDD + 审查）
          → 验证（单测 / 契约测 / 冒烟）
            → 发布（版本、变更说明、可回滚）
              → 运行与观察
                → 回顾 / 还债
```

### 2.1 发现：先回答「为什么重构」

企业里这一步叫 Discovery / Problem framing。没写清楚就动手，后面全是范围膨胀。

你必须先书面回答，而且只能选一个主目标（可以有次目标，但不能五个并列第一）：

1. **更好扩展**：加 Stage / 搜索源 / 会议摄取太痛（TD-05 `_exec` 大 if-elif）
2. **更好维护**：`cli.py`、`transcriber.py`、`downloader.py`、`models.py` 单文件过重
3. **分层更干净**：CLI/WebUI/translate 跨层、ingest 上引 application
4. **更可发布**：CI、覆盖率、PyPI、分档安装体验
5. **更好用**：报告质量、arXiv 全文、原始资料落盘、视频/PDF 稳定性

这五个改法完全不同。例如「分层纯洁」几乎不增加用户价值；「Stage 注册表」是为 D6 会议摄取铺路；「arXiv 全文」是产品能力，不是结构重构。

**建议先写一页，不超过这些标题：**

- 用户是谁（你自己 / 实验室 / 要 `pip install` 的外人）
- 现在最痛的 3 个具体操作（用命令或文件路径描述）
- 重构成功长什么样（可观测：例如「新增 Stage 只改注册、不改 `pipeline.py` 的 if-elif」）
- 明确不做的事（微服务、消息队列、重写成 FastAPI 平台、再出一套 VideoIngest 级文档工厂）

仓库里已有 `project_management/reports/future_roadmap.md`，短期 D1/D2、中期 D3/D4/D8/D10 其实就是一张重构地图。缺的是 **你拍板：现在走哪一条，其余冻结**。

### 2.2 需求：把「想重构」变成可验收条款

企业里这一步是 PRD / 用户故事 + 验收标准。你们规范里对应的是 BDD + `STATUS.md` 功能点。

对重构，需求不是「代码更优雅」，而是：

| 类型 | 例子（针对本项目） |
|------|-------------------|
| 行为不变（characterization） | `research run` 在同一 fixture 下仍写出 `raw/*.md`、`sources.json`、`.stage-complete/*.json` |
| 行为故意变 | arXiv abs 有 HTML5 时抽全文；无 HTML5 时 PDF 进 `_originals` |
| 非功能 | 无网 CI 必须绿；密钥不进仓库；`minimal` 档不强迫装 MinerU |
| 兼容 | `__all__` 里的公开符号不删；`config.example.yaml` 字段只增不改语义 |

当前工作区已经在做后者（`fetcher` / collector / extractor / `test_arxiv_fulltext_and_originals.py`）。**未完成的功能分支上不要开结构重构。** 企业里这叫 change freeze：先合入或 stash 功能，再动骨架。

### 2.3 架构：先盘契约，再谈分层

你们已经有正确的核心 ADR（`worklogs/decisions/0001-六阶段管道文件系统通信.md`）：

> Stage 只通过文件系统通信，可中断、可恢复、可独立调试。

这是本项目最值钱的架构决策。企业级重构的第一条红线是：**磁盘上的阶段契约比五层目录更「公共」。**

需要冻结并写成「契约清单」的，至少有：

| 契约面 | 位置 | 为什么动它等于发 breaking change |
|--------|------|----------------------------------|
| 阶段目录 | `raw/` `clean/` `extracted/` `tree/` `report.md` | 用户会手改中间产物；`--resume` 依赖它 |
| 完成标记 | `.stage-complete/`、`.deepen_done` | 跳过/重跑逻辑 |
| 配置 | `PipelineConfig` + `docs/config.example.yaml` | 所有 run 的入口 |
| SDK | `research_tool/__init__.py` 的 `__all__` | 外部 `from research_tool import research` |
| CLI | `research = ...cli:app` 的命令与选项 | 脚本和 `setup` 向导 |
| 错误码 | VideoIngest 26 个码 + `resolve_exit_code` | 自动化调用方看退出码 |
| 搜索/LLM 工厂名 | `SearchEngine` Literal、provider 名 | 配置字符串是稳定 API |

五层（presentation → application → domain → infrastructure，common 横切）应当 **服务这些契约**，而不是反过来为分层而分层。架构审核里那 3～4 处「有意偏差」可以收，但那是中期卫生项（D10），不是第一刀。

规范要求 `import-linter`，仓库里 **没有落地 ini，CI 也不跑它**。企业做法是：规则必须可自动失败。写在 `conventions/01-architecture` 里但机器不执法，等于没有。

### 2.4 计划：按风险切片，不按「模块好看」切片

企业计划的单位是 **可独立合并、可回滚的增量**，不是「重构 application 层」。

结合已有债表，合理顺序是：

```
P0 安全网与卫生     →  结构缝     →  大文件拆分     →  能力演进
TD-01 CI/覆盖率       StageRegistry   cli / models     会议摄取
TD-02 密钥            解除循环依赖    transcriber      专家库
TD-04 静默吞错        表征测试补洞    downloader       可观测性分类
当前 arXiv 功能收口
```

每一刀都要满足：

- 有失败测试或契约测试先钉住行为
- 一次只改一类东西（结构 **或** 行为，不要同时改）
- 合并后主分支始终可 `research setup --check-only` + 无网 pytest
- 超出切片的发现记入债表，不顺手扩 scope

你们自己的 AI 流程写得很对：**不越界、不黑盒、不积压收束。** 重构时把「功能点」换成「行为不变的结构切片」即可。

### 2.5 实现：TDD 在本项目里要改一个习惯

规范要求红-绿-重构。对 **新行为**（arXiv HTML5、`_originals`）完全适用，你已经在写 `test_arxiv_fulltext_and_originals.py`，这是对的。

对 **结构重构**，企业常用的是另一条腿：**表征测试（characterization test）**——先把「现在实际做什么」录下来，再挪代码。因为：

- 34 个源模块缺专属测试（TD-20）
- LLM 阶段不可复现，不能拿「报告好不好看」当回归
- 真正稳定的是：目录布局、JSON schema、marker、配置校验、后端 URL 解析、错误码

本项目应 **只 mock 外部边界**（HTTP、LLM、文件系统时间），不要 mock `Collector` 去测 `ResearchPipeline` 的编排空壳。阶段之间已经是文件契约，集成测试的自然夹具就是一份最小 `topic_dir`。

### 2.6 验证：金字塔要按这个系统的物理形状来

标准比例 70/20/10 仍然有用，但层的含义要换：

| 层 | 本项目测什么 | 现在的缺口 |
|----|--------------|------------|
| 单元 | URL 解析、slug、SSRF、配置校验、MinHash、错误码仲裁 | 覆盖率未知 |
| 契约/阶段 | 给定 raw fixture → clean/extract 产物形状 | 六阶段并非都有厚测试 |
| 编排 | resume、marker、backward 失效、talk 重跑 | backward 有 8 例，正向编排偏薄 |
| 冒烟 | `scripts/smoke_e2e.py`、部署 preflight | 不能当 CI 主路径（要 LLM/网络） |
| 安全 | gitleaks | 有；密钥轮换流程仍是人肉 |

企业门禁最低配置（这个体量就够了）：

1. GitHub Actions：Python 3.11/3.12 × `ruff` × `pytest`（无网）
2. 覆盖率先 **量出来**，再设下限（先 60%，核心域再加）
3. `import-linter` 分层契约
4. commitlint + gitleaks（钩子已写，强制未落地）
5. 可选：一个「黄金 topic」夜间 live 任务，失败只告警不挡合并

没有这张网就做 D3/D4 那种跨文件重构，等于无保护攀岩。风险表 R-10 / R-13 说的就是这件事。

### 2.7 发布：本地工具也要有版本纪律

你们有 wheel（`0.1.1`），有交互式部署向导，这已经比很多内部工具强。还缺的是企业发布的后半截：

- SemVer：磁盘契约或 `__all__` 破了就升 minor/major，不能一直挂 0.1.1
- `CHANGELOG.md`：用户能看懂的变化，不是 worklog
- extras 边界：`[llm,search]` 必须永远可跑；`[pdf]` / `[video]` 失败不能污染核心
- 配置兼容：只加字段，改默认值要写迁移说明
- 回滚：git tag + 上一版 wheel 可装

`research setup` 的「计划与验收来自同一张档位表」是正确的发布哲学，重构时不要拆掉这条。

### 2.8 运行：这个产品的「生产环境」是用户磁盘

没有集群，但有运维：

- 静默 `except: pass`（TD-04）在企业里算事故级可观测性债
- 宽 `BLE001`（TD-17）会让「管道失败」无法分类
- `research-output/` 是用户数据，重构不得改写入位置除非做迁移
- SSRF、代理、密钥注入已经是安全面，重构 fetch/search 时要当安全变更审

### 2.9 回顾：收束节点你们写过，但文档熵已经超标

VideoIngest 那套 01–08 设计包，作为一次高风险子系统是合格的企业实践。作为 **日常开发模板** 过重。

当前更严重的问题是双份事实源：`STATUS.md`、`AGENTS.md`、`CLAUDE.md`、`project_management/` 数字互相打架。企业里这叫 configuration drift。重构前应指定：

- 进度权威：`STATUS.md`
- 架构权威：ADR + 代码
- 债权威：一张债表（STATUS 与 `technical_debt.md` 必须合并）
- 其余审核报告归档，不再当活文档改

**不要再为「重构」新建第三套管理体系。**

---

## 3. 重构专用流程（比「日常功能开发」多的四步）

功能开发是「加行为」。重构是「在行为不变的前提下改形状」。企业里成熟做法接近绞杀藤（Strangler Fig），不是 Big Bang。

### 第一步：完成或搁置当前工作区

未提交内容已经在动 `models.py`、collector、extractor、fetcher、`config.example.yaml`。这是 **行为变更**。

正确顺序：把 arXiv 全文 / originals 收成一个功能提交（测试绿）→ 再开 `refactor/...` 分支。不要在脏工作区上拆 `pipeline._exec`。

### 第二步：契约冻结（1～2 天，不写业务代码）

列出上一节的契约表，对每条标：冻结 / 允许兼容扩展 / 本季度允许 breaking。

建议默认：

- **冻结**：阶段目录名、marker、SDK `__all__`、CLI 主命令名、文件系统通信
- **允许扩展**：`raw/_originals`、新搜索后端、新 config 字段
- **本季度可动**：`_exec` 内部、cli 文件切分、presentation 不再直连 infrastructure

没有这张表，重构评审无法判断「这是内部移动还是破坏用户」。

### 第三步：先补网，再搬家具

最低安全网（对应你们自己的 D1 + D8，只是顺序更硬）：

1. 落地 pytest workflow（现在 `.github/workflows/` 只有 gitleaks）
2. 跑一次 coverage，记下基线，不先追求 80%
3. 给六阶段各补「最小产物契约测试」（有/无 marker、空输入、损坏 JSON）
4. 给 `ResearchPipeline._exec` / resume 补表征测试，**然后再** 改成注册表

「先重构再补测试」是这个仓库最危险的冲动，因为 TD-20 已经写明安全网不够。

### 第四步：一次一个缝（seam）

按依赖从外到内，不要先拆最大的文件：

| 顺序 | 切片 | 完成定义 |
|------|------|----------|
| 1 | 卫生 P0 | CI 绿、吞错打日志、路径不再写死绝对盘符 |
| 2 | `StageRegistry` 替换 `_exec` if-elif | 新增 no-op stage 只注册即可跑通 6 阶段 + backward |
| 3 | `pipeline_adapter` 不再上引 application | 视频入口改走 application service |
| 4 | CLI/WebUI 只依赖 application | `grep presentation → infrastructure` 趋近 0 |
| 5 | 拆 god file | 单文件 ≤ 约 400 行，行为测试全绿 |
| 6 | 再开能力项 | 会议摄取、专家库、更深的可观测性 |

每一刀单独 PR、单独收束。你们规范里的「到点必须收束」在重构上特别有用：拆到一半的 `cli.py` 比不拆更危险。

---

## 4. 结合本仓库，你必须提前拍板的决策

这些是架构决策，不是实现细节。不拍板就会在重构中反复返工。

**1. 产品形态**  
继续做「本地引擎 + CLI + Gradio + SDK」，还是要长成带 HTTP API 的服务？  
现在无 HTTP（规范 04 标 N/A）。为「企业范」加 API 网关 / 队列 / 多租户，是典型过度设计。文件系统管道已经给了可恢复性。

**2. 领域层要不要变厚**  
现在 `domain/` 几乎是 Pydantic 配置 + 错误类型，规则在 stage 里。这是「贫血模型」，对管道型工具可接受。不要为了教科书 DDD 把 Clean/Extract 规则搬进 domain，除非你真的要多套 UI 共享同一套规则引擎。

**3. 插件化做到哪一档**  
搜索后端 18 路 if-elif，够用。entry_points 插件是企业「生态」需求。在没有外部贡献者之前，注册表 + 测试比 setuptools 插件更划算。

**4. LLM 不确定性如何验收**  
企业产品会对模型输出做 eval harness（黄金集、引用是否还在、树节点数 4–7）。重构结构时不要把「报告更好」和「代码更好」绑在同一个 PR。质量是产品迭代，不是结构重构的完成定义。

**5. 文档策略**  
选一种：  
- A. 维持 AI 工作流，但 **只更新 STATUS + ADR + 债表**  
- B. 再写一套 enterprise PMP  

选 A。VideoIngest 那种 150+ 设计文件证明你们能做重设计；它不能证明每个重构都该那样做。

**6. 兼容策略**  
若已有真实使用者（`research-output/` 里大量历史 run），磁盘布局 breaking 需要迁移脚本。若只有你自己，仍建议当公共契约，否则 resume 和手工改 raw 会在重构后集体失效。

---

## 5. 和「日常功能开发」对齐的操作手册

把企业流程压成你下周就能用的节奏：

**每个功能（含 arXiv 这类）：**

1. 一句话意图 + 验收（BDD 或测试名）
2. 先写失败测试
3. 最小实现
4. 无网 pytest + ruff
5. 更新 `STATUS.md` 和 config example（若动配置）
6. 小提交，conventional commit
7. 不顺手重构邻接模块

**每个结构切片：**

1. 先有表征测试
2. 只移动代码，不改产物
3. 同一 PR 不改 CLI 语义
4. 收束：测试绿、分层 grep、STATUS 债项勾销

**每个季度：**

1. 重评债表（很多 7 月条目可能已过时，以代码为准）
2. 覆盖率与分层契约是否仍在 CI 里
3. 最多开一个大结构主题

**人的闸门（你只需要看这些）：**

- 这个切片的用户可见契约变了吗？
- 失败时用户能看见原因吗？
- 能否一键回到上一 tag？
- 是否又新增长篇文档而代码闸门没加？

---

## 6. 这个项目上，企业级最容易走歪的路

- **推倒重写「更干净的引擎」。** 六阶段 + 文件契约已经是差异化；重写会丢掉 resume、backward、部署档位这些已付过学费的行为。
- **为分层而分层。** 表现层直连 LLM 工厂是味道，不是用户故障。先 CI 和吞错。
- **在脏工作区上重构。** 现在就处于这种状态。
- **用文档数量代替闸门。** 规范写了 import-linter / 覆盖率 / commitlint，机器都没跑。
- **把 live 调研当回归。** LLM + 外网搜索会 flaky，CI 会变成「重跑就过」。
- **一次拆 `transcriber.py`（1100+ 行）和管道注册表。** 交叉失败时无法归因。
- **假装这是 30 人团队。** 你需要的是小团队工程卫生：CI、契约、SemVer、ADR、债表，不是立项委员会。

---

## 7. 若现在就要开工：第一周只做这些

1. **收口或暂存** 当前 arXiv / originals 功能，让 `master` 干净。  
2. **写半页重构意图**（一个主目标 + Out of Scope）。  
3. **列契约冻结表**（阶段目录、SDK、CLI、config）。  
4. **加 pytest CI**，测一次真实 collect 数，把 `STATUS.md` / `AGENTS.md` 的 417/454/840 对齐。  
5. **给 pipeline resume + 六阶段产物** 补最小契约测试。  
6. 以上没完成之前，不动 `_exec`、不拆 `cli.py`、不开会议摄取。

---

一句话：**企业级不是把这个项目做成「大厂架构」，而是用大厂的风险控制方式，守住「主题进、可恢复的知识树和报告出」这条价值流。** 你们已经有设计能力和规范语言；下一步是把闸门接到机器上，并让每一次重构都小到可以证明没坏。

如果你愿意，下一步我可以直接帮你做其中一件，且只做一件：写「重构意图 + 契约冻结表 + 第一刀切片计划」，或先把 pytest CI 和事实源数字对齐。你更想先要哪件？

## User

“请先不要回答我的问题。  在给出答案之前，请先完成以下分析： 
1、指出我在这个问题中没有明确说出、但已经默认成立的假设；
 2、告诉我还缺少哪些关键信息，以及这些信息可能如何改变你的答案； 
3、指出人们在处理这类问题时最常犯的一个错误。 然后，请只向我提出一个最关键的问题。这个问题应该能够帮助你理解我的真实目标和具体情况，让最终答案真正对我有用，而不是一份谁都能套用的通用建议。  
等我回答以后，你再给出最终输出。  
我的问题是：[这样，你基于上述内容，你分点创建对应的文件，提供文件和提问与回答的方式帮助我走一遍标准的企业开发流程，同时我通过这个模式，在回答这些问题的同时，学会这个标准的企业开发流程。同时在这个阶段需要你根据我的回答，然后对我进行引导，你认为如何？]”

## Assistant

先不做方案，也不建文件。只做你要求的三步拆解，然后只留一个问题。

---

## 1. 你没说出口、但已经默认成立的假设

1. **存在一份可线性走完的「标准企业开发流程」。** 你默认 Discovery → 需求 → 架构 → 计划 → 实现 → 验证 → 发布 → 运行 是唯一正统顺序，而且适合用「分点建文件 + 问答」完整走一遍。实际上流程随产品形态、团队规模、是否受监管而变；上一轮我已经把 `research-tool` 判成单人、本地优先的引擎，不是典型企业 SaaS。

2. **建文件 = 在走流程，填文件 = 学会流程。** 你默认产出物（意图书、契约表、计划、检查单）既是教学载体，也是能力证明。这把「做决策、承担后果、被闸门拦住」偷换成了「完成作业」。

3. **上一轮的分点就是课程大纲，现在要把大纲落成仓库里的一套新文件。** 「基于上述内容，分点创建对应的文件」默认：那些阶段都要教、都要有对应文档、而且应该写进当前项目。

4. **这个项目适合再加一层流程文件。** 默认再开一套学习用文档不会加重你们已经存在的问题：规范、ADR、`STATUS`、`project_management/`、VideoIngest 设计包并存，数字还对不上。

5. **你一个人要同时扮演出资人、产品、架构、开发、QA、发布经理。** 默认「企业流程」里那些分工、评审、签字，都可以改成我提问、你回答，而不失真。

6. **现在就该进入「走流程」阶段，而不是先收口手里的活。** 工作区里还有未提交的 arXiv 全文 / `_originals`。你默认教学流程可以和这条行为变更并行，或可以忽略它。

7. **我的角色是教练：根据你的回答继续往下问、纠偏、引导。** 默认你要的是持续辅导，不是一次性模板包，也不是我替你写完后你只点头。

8. **「引导」可以既严格又友好：我会拦质量，但不会让你卡死。** 默认我们还没约定：答得空、答得自相矛盾、或范围膨胀时，是停下来重写，还是我降低标准往下走。

9. **学会流程的标志是「能讲清楚每一步为什么存在」。** 默认成功标准是理解，而不是仓库里出现 CI、契约测试、一次可回滚的真实切片。

这些假设里，只要有几条不成立，上一问里「建文件走一遍」就不该按字面做。

---

## 2. 还缺的关键信息，以及它们会怎样改答案

| 缺少的信息 | 它会怎样改变做法 |
|------------|------------------|
| **主目标是「学会判断」还是「把 research-tool 下一刀拍板并执行」** | 前者：可以用独立练习纸，甚至不进这个仓库。后者：不应新建平行体系，只复用 `STATUS` / ADR / 债表，围着一个真切片走。 |
| **时间盒**（一次 90 分钟 / 一周每晚 / 数周） | 决定走完整 SDLC，还是只走「意图 → 契约 → 一个闸门」——后者往往才是真学会。 |
| **你要的「企业」是哪一种** | 互联网产品（双轨敏捷 / Shape Up）、受监管交付（阶段门、审计迹）、还是你们自己的 AI 工作流 0–3 步。三种对应的文件和提问完全不同。 |
| **练习载体** | 用当前 arXiv 改动当活案例、用「重构」当案例、还是虚构一个小功能。活案例能教会验证/发布；虚构案例更容易空转。 |
| **文件放哪、算不算项目文档** | 进仓库会加剧文档漂移；放练习目录或只存在对话里，才更像教学而不像又一套管理体系。 |
| **闸门硬度** | 答得不够能不能进入下一阶段。软：像教程。硬：才像企业（质量不够就打回）。 |
| **是否接受「这个仓库先禁止再长一套流程文件」** | 接受：我应拒绝「分点建文件」，改成在旧权威文件上打补丁。不接受：才按你字面去建大纲文件。 |

没有这些，任何「文件清单 + 提问清单」都只是通用课件。

---

## 3. 处理这类问题时最常犯的一个错误

**把企业开发学成「按阶段生产文档」，以为模板填完就等于会做专业工程。**

真正被企业流程保护的是：**在不确定时做选择、留下证据、让机器或下一关有权说不、并能回滚。**  
最常见的失败形态，正好是这个仓库已经出现过的：规范、设计包、审核报告很全，但 CI、覆盖率、分层契约、提交闸门并没在跑。再为「学习」建一套问答文件，会把这个错误再演一遍，只是这次演得更认真。

---

下面只问一个问题。你答完后，我再决定：是否建文件、建哪些、建在哪、问到哪一层就停、以及我怎样根据你的回答往下带。

**这个练习结束时，你只选一个主成功标准：A）你能在下一个陌生项目里自己走流程（判断力，research-tool 只是教具）；还是 B）research-tool 的下一阶段已经拍板，并且马上按闸门开工（交付，学习是副产品）？**