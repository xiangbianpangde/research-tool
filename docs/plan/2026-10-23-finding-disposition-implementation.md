# 问题处置与知识网络实现合同

2026-10-23。实现tool `b1c25a5` / reader `1602bd4`已推送非main；真实pre-push 1381 passed/3 skipped，分范围Pi复审均approve0。承接[设计v1](2026-10-23-finding-disposition-design.md)。实现边界为同一research-tool包及ResearchCTL只读reader；不建立第二调度器、可编辑SQLite真源或临床预测器。当前验收进度以[STATUS](../../STATUS.md)为准，历史设计文档保留“当时未实施”的原文。

## 1. 数据与权威边界

`Registry`位于`research_tool.infrastructure.disposition`。每个显式项目只配置一个registry：`HEAD.json`指向不可变`revisions/<sha>.json`，每revision含previous、事件、replay state SHA；派生view不写回。root白名单由create固定，拒绝symlink/特殊文件/traversal；原run-record、已提交stage、clean正文、graph、claim/span/locator逐项回验。run-record canonical字段不扩展。

Observation身份使用root_id、run_id、artifact路径/SHA、JSON pointer、原值SHA、run-record SHA；finding_id不作跨run身份。导入原值不重写，初始Issue为required/unclassified/open；分类需提案和审核，kind/purpose一致。可用kind：report_wording、representation、scientific_gap、source_conflict、material_task、persistent_constraint、history。scope完整字段：purpose、population、challenge、intervention_condition、outcome、time_window、denominator_unit、target_requirement。unknown可保留，但不能伪装已知临床范围。

本机actor为自述Agent标签，只支持agent_source_review；不能自声明human_clinical或独立人类认证。账本证明“作出了什么裁决”，不证明医学语义真实。

## 2. CLI与SDK

```bash
research issues create --registry /authorized/output/issue-dispositions --request create.json
research issues import --registry /authorized/output/issue-dispositions --request import.json
research issues view --registry /authorized/output/issue-dispositions
research issues propose --registry /authorized/output/issue-dispositions --request proposal.json
research issues review --registry /authorized/output/issue-dispositions --request review.json
```

create请求为`{"registry_id":"project-v1","roots":{"corpus":"/authorized/output/topic"}}`。import请求为root_id、run_id、expected_audit_sha256、expected_head、idempotency_key、actor。view无请求且不初始化；snapshot要求expected_head。claim-reference接受root_id、run_id、claim_id，返回已提交最终graph的精确引用。

HistoryLink使用非关闭操作`link_history`，payload为rationale、relation(related_to|superseded_for_use)、target_ref及包含该目标的evidence_refs；审核只记录用途/历史关系，不重写旧verdict/review。superseded_for_use不是临床采用许可。

SDK：`Registry.create(path, registry_id=..., roots=...)`、`head()`、`view()`、`snapshot(expected_head=...)`、`export(expected_head=...)`、`import_audit(...)`、`claim_reference(...)`、`propose(...)`、`review(...)`、`propose_network(...)`、`review_network(...)`。

propose请求：issue_id、operation、expected_issue_revision、payload、expected_head、idempotency_key、actor。payload包含rationale以及操作所需字段；未知字段拒绝。review请求：proposal_id、expected_proposal_sha256、approve(bool)、reason、expected_head、idempotency_key、actor，可选authority只能agent_source_review。

每次写入锁稳定writer lock，先HEAD/issue revision CAS、回验来源和proposal，再fsync不可变revision/目录，原子发布HEAD并fsync父目录。HEAD前SIGKILL孤立revision不生效；HEAD后重复key返回原回执；同key改请求拒绝。不是分布式事务，也不宣称可防拥有磁盘写权限的攻击者重新伪造整本历史；使用者必须固定可信HEAD。

## 3. 合法状态与科学边界

lifecycle为open/working/pending_review/closed；assessment为unassessed/partially_answered/awaiting_material/awaiting_data/not_identifiable_under_scope。classify/assess不关闭；reopen保留历史并撤销旧科学flags，约束投影变superseded。拒绝过期proposal可回到working，新审核必须重绑上下文；来源损坏时仅可记录无证据metadata-only reopen/assess，采用仍fail-closed，不能用重绑mtime恢复证据。

- corrected只在已分类wording/representation等允许范围使用已有精确claim；不增加科学证据。
- scientific_gap仅resolved可设scientific_answered，需要所有scope已知、acceptance_met和原子target_requirement到精确evidence_ref的验收映射；waiting/unidentifiable不得解决。Agent审核不是自动科学证明。
- new_scientific_evidence_added额外要求另一已提交run的被验收claim包含原语料没有的新source body/原句；重跑、改topic/claim ID、转载同原句和补上下文不算新证据。此计数表示经审查的新内容贡献，不是独立队列数量。
- decomposed逐字coverage_text完整覆盖父要求，children继承required和临床scope；父closed不能隐藏open child；已有children父项不得重复split。
- duplicate/superseded必须完整已分类同kind/scope、存在目标与精确证据，无循环；unknown或同词label不能合并。目标保留全部未满足要求。
- 核心required不能out_of_scope；constraint重分类只对明确persistent_constraint/history，并始终披露原观察、约束和raw finding；不会自动让strict成功。分类后不能把科学问题降级为wording关掉。

network/policy/scope变化使旧决定needs_revalidation，有效open、科学当前计数不继承。原生closed字段仍保留历史，effective_lifecycle才是当前评估依据。

## 4. 经审核网络与用途评估

`network-evidence-map/v1`包含evidence_refs、assignments、studies、edges，可选assessments。assignment将精确claim多归属concept/role/scope；study记录source/study/cohort和支持claim，cohort unknown不是独立性证明。允许边requires_context/context_of/conflict_candidate/secondary_citation_of/possible_overlap_with/reports_on；拒绝causes/confidence裸关系。context_of要求同source版本且真实span包含；requires_context只表示需原文上下文审核，不证明因果。

`assessment-policy/v1`固定purpose规则，不允许通过改阈值造科学充分：study_metadata检查original_identity；descriptive检查population/unit/denominator/intervention/time/missingness/repeated_events；cross_study检查independence/comparability/bias/precision。assessments逐requirement标met/unmet/unknown，met必须精确claim与reason；整个network/policy先proposal再review才生效。评估展示未满足项，独立cohort数量保持null、scientific_sufficiency不自动推断、自动关单和网络产生新科学证据数恒0。

## 5. 九段、报告与Reader

默认`disposition.policy=raw`不改变旧runtime19指纹/门禁；YAML与SDK均受校验。reviewed要求registry_root、expected_head_sha256和显式retrieval.module_root；固定reader代码/解释器身份并握手issue-evaluation/v1，旧/未知reader不回退。HEAD或reader变化进入研究身份或拒绝运行。

reviewed并列raw verdict与reviewed verdict。required原观察、未决children/局限及当前新增finding/limitation作为底线；旧同ID不继承关闭。已裁决引用必须仍在当前graph并逐原句/范围/版本一致，不能借旧scope删坏引用。STOP_ERROR/STOP_BUDGET/STOP_CANCELLED硬终态不可豁免；strict真实缺口继续STOP_BUDGET，layered只提供披露式报告。

`final/issue-evaluation.json`嵌固定HEAD的完整决策链、state hash、按原artifact SHA固定的audit/graph原字节、network/policy digest、raw findings及active required/constraints/科学计数；与全部final共同受report envelope和summary SHA保护。summary与retrieval manifest声明required_contracts；仅reviewed manifest使用schema_version=2，raw仍v1，真实旧v1 reader会拒绝而非忽略新字段；reader核验链hash/提案审核、独立只读完整replay每个revision state SHA并重算活动必需计数/原finding投影，不维护独立可编辑账本。report-review另外绑定sidecar/head/network/policy，仍不改变执行verdict或原报告。

## 6. 医学135条迁移预演

成果仅在授权医学工作区`runs/finding-disposition-implementation-20261023/`：107 finding＋28 limitation均以原JSON pointer/hash导入，135分类proposal和238主题成员映射候选未审核；22模型parts保留为未应用设计候选。原135仍required unresolved，科学回答/新证据均0，原run173文件/report/graph/audit/review保持SHA。CLI只读回读不改registry。迁移预演不等于正式医学裁决、科学缺口清零或临床专家验收。

## 7. 验证与回退

A01–A15映射、红绿、真实进程CAS/SIGKILL、两仓reader、九段/零模型resume和全量证据位于忽略目录`research-output/finding-disposition-implementation-20261023/`。A01–A15映射见acceptance-matrix.json；真实迁移135全对账，处置反例71项（随后增加strict/source-grounded不支持组合拒绝回归），Reader11＋legacy443。实现pre-push全量1381/3，最终文档补充的通过数量/版本以STATUS与实际提交为准，不把旧1310基线当本轮结果。source_grounded仍要求layered，不把strict不支持组合当新保证。

关闭显式reviewed配置可回到旧raw阅读；需要代码回退时使用审查后的新revert提交，不reset/clean、不删除账本或改旧医学结果。未授权合main、release/tag或包版本变化。
