"""Source-exact atomic reporting: mechanical guarantees, not unrestricted semantic proof.

A selected claim may be organized but cannot be rewritten into new medical facts.
Full-source dependencies are added through the ordinary exact-span ledger path.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from ..domain.errors import StageError
from ..infrastructure.stages.extractor import _verified_claims
from .evidence import verify_span

_DEPENDENCY = re.compile(
    r"\d|adjust|method|denominat|limitation|conflict|asymptomatic|prophyl|"
    r"pregnan|deliver|miscar|bleed|thrombo|table|figure|footnote|reference|"
    r"无症状|出血|分母|调整|局限|表格", re.IGNORECASE,
)


def validate_atomic_statement(item: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    """Reject all detached unit/effect metadata and every ungrounded rewrite.

    Numerical values/units/effects stay inside the source literal; the model may
    not label a patient denominator as events or OR as RR alongside a correct quote.
    """
    if set(item) - {"text", "claim_ids", "supported"}:
        raise StageError("report", "atomic metadata cannot override original units/effects")
    ids = item.get("claim_ids")
    facts = {f["claim_id"]: f for f in graph["facts"]}
    if not isinstance(ids, list) or len(ids) != 1 or ids[0] not in facts:
        raise StageError("report", "atomic statement requires exactly one immutable fact")
    fact = facts[ids[0]]
    if item.get("text") != fact["text"]:
        raise StageError("report", "atomic 原文论断不得被改写或添加全局否定")
    return {"claim_id": ids[0], "text": fact["text"], "status": "supported",
            "supporting": fact["citations"], "semantic_scope": "literal_source_only",
            "source_truth_certified": False, "new_inference_permitted": False}


def build_evidence_bundle(graph, blocks, selected_ids):
    """Validate the reader's full-source spans and retain dependent context verbatim."""
    sources = {s["source_id"]: s for s in graph["sources"]}
    dependencies = []
    for block in blocks:
        source = sources.get(block.get("source_id"))
        if source is None or not verify_span(source["text"], block):
            raise StageError("report", "full-source retrieval returned an invalid original span")
        if _DEPENDENCY.search(block["text"]):
            dependencies.append(block)
    return {"dependencies": dependencies, "selected_ids": selected_ids,
            "full_source_scope": True, "absence_inference_permitted": False,
            "scope_limit": "committed clean bodies; not missing supplements/PDF correctness"}


def dependency_facts(graph, blocks):
    """Use the existing strict quote binder before evidence enters CAS merge/inspection."""
    sources = {s["source_id"]: s for s in graph["sources"]}
    bundle = build_evidence_bundle(graph, blocks, [])
    old = {f["claim_id"] for f in graph["facts"]}
    added = {}
    for block in bundle["dependencies"]:
        source = sources[block["source_id"]]
        values = _verified_claims(source, block["text"],
                                 [{"quote": block["text"], "topic": "原文上下文与反证"}],
                                 chunk_offset=block["start_offset"])
        for fact in values:
            if fact["claim_id"] not in old:
                added[fact["claim_id"]] = fact
    return list(added.values())


def literal_report(graph, inspection, topic, audit, maximum):
    """Render a Chinese-framed evidence report with no model-authored medical assertions.

    This deliberately trades free-form synthesis for a narrow enforceable contract.
    Original values that conflict stay separate; users must not treat quotations as
    adjudicated truth or a clinical score.
    """
    from .research import _render_evidence_limits, validate_graph

    validate_graph(graph)
    sources = {s['source_id']: s for s in graph['sources']}
    indices = {sid: i for i, sid in enumerate(sorted(sources), 1)}
    facts = {f['claim_id']: f for f in graph['facts']}
    selected = audit['selected_claim_ids']
    claims, atomic = [], []
    lines = ['# ' + topic, '', '> 成文前完整来源已核验。本版为原文优先的证据分层报告；'
             '中文为固定导航，医学陈述逐字保留原文，不生成新的风险、因果或缺失断言。', '',
             '## 原始研究陈述（非独立医学结论）', '']
    for cid in selected:
        fact = facts[cid]
        checked = validate_atomic_statement({'text': fact['text'], 'claim_ids': [cid]}, graph)
        atomic.append(checked)
        refs = ' '.join(f'[{indices[c["source_id"]]}]' for c in fact['citations'])
        lines.extend([fact['text'] + ' ' + refs, ''])
        claims.append({'text': fact['text'], 'claim_ids': [cid], 'citations': fact['citations']})
    lines.extend(['## 正反证与冲突边界', '',
                  '以下附件逐条保留完整来源依赖原句，不因正文选材而删除。它们是待核对的上下文/反证候选，'
                  '不是自动判定每一对数据矛盾，也不证明原文数值真实。', '',
                  '[完整上下文依赖与来源冲突审计](grounding-audit.json)。'
                  '同源不一致保留原值，禁止猜测调和；未定位不等于论文未报告。', ''])
    lines.extend(_render_evidence_limits(audit, indices))
    lines.extend(['## 来源', ''])
    lines.extend(f'[{indices[sid]}] {s.get("title", "")} — {s["locator"]}'
                 for sid, s in sorted(sources.items()))
    markdown = '\n'.join(lines) + '\n'
    if len(markdown) > maximum:
        raise StageError('report', 'source-exact report exceeds budget; no truncation')
    return {'markdown': markdown, 'claims': claims, 'headings': [], 'evidence_audit': audit,
            'citation_coverage': 1.0,
            'content_sha256': hashlib.sha256(markdown.encode()).hexdigest(),
            'verification': 'source_exact_atomic/v1',
            'verification_limit': 'Original statements only; no clinical acceptance',
            'atomic_checks': atomic}
