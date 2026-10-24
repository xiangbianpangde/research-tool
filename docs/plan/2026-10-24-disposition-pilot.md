# 首批真实问题裁决与知识网络修复：方案与执行合同

2026-10-24。用户授权“设计方案＋执行”，承接[机制实现](2026-10-23-finding-disposition-implementation.md)。本批不是再造工具或批量批准135条，而是用真实原文检验机制是否能修正网络与误报，同时保留科学未知。

## 1. 范围、权威与产物

冻结语料仍为已验收run `d672f7fb457b4d4eaa00ba4f8c60847a` 的10篇全文、2070facts、119nodes、107finding＋28局限。原report SHA `1952b47401b0b7113aaddaed9dea83531844097dd7d4c722b0bfc950660ce585`。沿用现有唯一registry：医学工作区`runs/finding-disposition-implementation-20261023/issue-dispositions/`，起始HEAD `e4d3653d69b1d421ccb6491a64550a1eb8e41668b2bef4c2048e2c2ecb36c546`；只追加事件，不复制第二权威账本。历史预演回执固定描述其当时HEAD，不冒充当前状态。

医学新产物集中在`/Users/xbpd/Projects/纤维蛋白预测/runs/disposition-pilot-20261024/`，含原文锚点/范围审核、请求和回执、before/after/对照、可复跑验证及结果说明。工具方案/状态仅写本仓docs，脚本和日志写忽略的`research-output/disposition-pilot-20261024/`。不移动旧文件、不公开提交论文原句或患者资料。

非目标：新联网系统检索、重生成整篇医学报告、患者数据库、作者邮件、人类临床批准、全135关单、消解原论文算术矛盾、合main/tag/version变更。只有实际暴露的实现缺陷才最小补修并先红后绿。

## 2. 六条代表原观察与拟处置（最终以原文review为准）

| 原观察 | 原audit指针 | 本批目标与不能声称的结果 |
|---|---|---|
| coverage:topic:伦理 | /unresolved_findings/28 | 两条原研究伦理/同意声明分别映到source-specific元信息，取消对其临床复现的三来源误要求；不证明研究无偏或治疗有效 |
| coverage:topic:研究设计 | /unresolved_findings/69 | Sun回顾性设计/75女性/三中心/日期是研究身份；不要求三篇重复这一身份，也不推广混合CFD至特定亚型 |
| coverage:topic:出血风险 | /unresolved_findings/32 | 2/53、11/53、40/53的原句仅是53名出血者中的亚型构成；改成描述性composition导航和上下文依赖，不反转为各亚型发生率/未来风险 |
| model:gap-002 | /unresolved_findings/98 | 先按原文字面无遗漏拆三部分：跨研究效应/混杂异质性仍partial；“需要明确调整后效应与未调整比例的区分”作为报告表述任务可据已分列原文纠正；手术独立贡献继续awaiting_data |
| model:gap-005 | /unresolved_findings/101 | 原句不是“全部论文都合并”，不能凭PRO反例全关。按分号拆两部分：总体“常合并/未充分分列”的范围判断保留partial；TEG复合历史终点/分别风险不可识别继续awaiting_data |
| L22 Shapiro冲突 | /known_limitations/21 | 分类source_conflict、awaiting_material；图10/35、正文15/35、摘要20/35原值并列，不猜子组、不resolved |

原atomic requirement不重写。拆分coverage_text按顺序逐字符拼回父句；先拆未分类父问题，再对各child分别分类，不以同一种scientific_gap限制错误吞掉报告纠错任务。未回答子项继承required/priority，原135观察及其原值/hash保持。首批外129条分类提案不审核、不隐式处理。

## 3. 网络先于问题裁决

1. 回验原173文件清单、旧报告/审校采用和registry HEAD；保存只读before投影。
2. 回读本批全部claim及上下文，记录source/content hash、offset、原句、分母单位/治疗条件/时间与未知项。
3. 拒绝旧238成员广泛自动映射提案（只表示不采纳未核范围，不删除历史），提交首批小映射，独立Agent原文review后生效。
4. 映射包含source-specific伦理/设计、53出血者亚型构成、PRO流产与PPH不同单位/分母、Casini调整OR与Fibrinogest转引、PRO2024/2025同库可能重叠、TEG历史复合结局、Shapiro冲突候选；只允许精确引用支持的context_of/requires_context/secondary_citation_of/possible_overlap_with/conflict_candidate边。
5. 每用途逐requirements标met/unmet/unknown。独立队列数仍null，未知重叠不能宣称独立，组织变化科学增益0。
6. 网络固定后，拒绝本批6条旧预演分类提案，按新的精确范围重提并review；已核方法/标签纠错单独处置，partial/awaiting_data/冲突不关。

所有审核均为自述Agent原文审核，不是人类医学签名。网络只修表示与适用规则，不改旧knowledge.json或借重新命名提高来源数。科学回答与新科学证据本批目标均0。

## 4. 新reviewed对照，不冒充新自动医学报告

在新目录生成本批固定HEAD的`issue-evaluation/v1`及before/after逐项差异；基于同一原graph、107finding、28limits计算raw与reviewed门禁。对照不写旧final，不生成新的九段STOP_SUCCESS，不用模型自述制造医学答案。

真实Reader验证包括：公开evidence CLI逐来源正文/graph反查，独立read-only ledger verifier完整replay/原audit/证据/当前inputs计数校验；public `research issues view/snapshot`只读回读。固定HEAD恢复：两次从原证据和当前HEAD重建投影，零模型调用、规范化hash一致；请求幂等重试不得重复处置。原报告独立采用/人类fail_closed状态不变。

验收以“修复什么、为什么成立、哪些仍缺证”为准，不能以总数下降为目标。raw finding数仍107、known limits仍28；原135观察完整，拆分后atomic问题数可增，required科学子问题必须继续阻塞strict。layered旧成功不代表本批strict完整。

## 5. 必需验收与版本管理

- 所有6条原身份精确，引用逐字/offset/hash回验，拆分全句无遗漏。
- 网络map/policy经显式提案/原文review生效，裁决锁其版本；无974裸模型关系升级。
- 每条已处置/仍open/partial/等待材料均有理由与证据；129条非首批保持原pending。
- 135原观察、原173文件/report/graph/audit/review不变；唯一registry仅追加，旧HEAD可验证。
- 对照与独立Reader/CLI、零模型恢复/幂等、硬失败/缺证仍阻塞通过。
- 必要现有回归、文档链接/差异及Pi现有review通过；代码修复若需要则相关红绿/全量/双仓合同通过。
- 分支`feat/disposition-pilot-20261024`，小批次Conventional Commits并推送，不绕hooks、不推main；医学私有运行材料不提交Git。

以上为执行前方案，下面按实际结果追加，不反写原观察或旧报告。

## 6. 实际执行结果（2026-10-24）

- 方案review approve0、提交2212b16/非main推送。医学独立Agent审核v1/v2 request_changes，修正方法角色、子组分母与Shapiro补充scope后v3 approve；具体拒绝证据留医学目录。
- 唯一registry追加49事件，固定HEAD `6a1bba0691bef71841c3ee02461024fb185aa7e2104eb8a2363ae440f242f88a`：24精确claim、24映射、14用途组、9边。不是974无span关系的自动升级。
- 原观察135/原finding107/原局限28保持；140原子Issue、134 required/high99未决，4 corrected＋2 decomposed仅行政，科学回答/新证据0；其余129逐字段原pending不变。gap005未因PRO反例全关，Shapiro冲突未解。
- 新对照raw/reviewed gate均CONTINUE，不是新自动报告成功。独立Reader全链/真实公开evidence与CLI、固定HEAD0模型两次重建/幂等无新增事件、四个拒绝反例通过。原173文件/report/graph/audit/review hash不变，旧Agent采用和human fail_closed保持。
- 本批未修改生产代码或reader；现有72项disposition回归通过。医学结果见`runs/disposition-pilot-20261024/首批裁决与对照结果.md`及verification.json；工具证据`research-output/disposition-pilot-20261024/`。独立执行复核approve/findings[]，固定HEAD验证脚本重复运行通过；交付文档Pi review approve0，版本回执从STATUS及本批acceptance进入。
