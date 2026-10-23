"""Atomic Chinese summaries with exact evidence and independent full-source checks.

Mechanical checks reject known invalid transformations; semantic model checks
remain fallible and never represent human clinical acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..domain.errors import StageError, LLMError, find_llm_diagnostic
from ..domain.models import LLMFailureDiagnostic


class Atom(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["description", "count", "fraction", "effect", "source_conflict"]
    text: str
    claim_ids: list[str] = Field(min_length=1)
    quantity: str = ""
    unit: Literal["unknown", "women", "patients", "pregnancies", "deliveries",
                  "live_births", "miscarriages", "bleeding_events", "procedures"] = "unknown"
    effect_type: Literal["unknown", "OR", "HR", "RR"] = "unknown"


class ChineseObservation(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal['description'] = 'description'
    text: str = Field(pattern=r'^[^0-9]+$', min_length=1, max_length=400)
    claim_ids: list[str] = Field(min_length=1)


class AtomDraft(BaseModel):
    atoms: list[ChineseObservation] = Field(min_length=1, max_length=6)


class AtomCheck(BaseModel):
    index: int
    status: Literal["supported", "contradicted", "insufficient", "source_conflict"]
    supporting_ids: list[str] = Field(default_factory=list)
    counterevidence_ids: list[str] = Field(default_factory=list)
    reason: str


class AtomChecks(BaseModel):
    checks: list[AtomCheck] = Field(default_factory=list)


_UNITS = {
    "women": (r"women|woman|females?", r"(?:名|位|人)?(?:女性|妇女)"),
    "patients": (r"patients?|subjects?|cases|probands?", r"(?:名|例|位)?(?:患者|受试者|人)"),
    "pregnancies": (r"pregnanc(?:y|ies)", r"(?:次|例)?(?:妊娠|孕次)"),
    "deliveries": (r"deliver(?:y|ies)", r"(?:次|例)?分娩"),
    "live_births": (r"live[ -]?births?", r"(?:次|例)?活产"),
    "miscarriages": (r"miscarriages?|abortions?", r"(?:次|例)?流产"),
    "bleeding_events": (r"bleed(?:ing|s)?|hemorrhag\w*", r"(?:次|例)?出血"),
    "procedures": (r"procedures?|surger(?:y|ies)|operations?", r"(?:次|例)?(?:手术|操作)"),
}
_NUM = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
_ABSENCE = re.compile(
    r"未报告|未给出|没有报告|未提供|无分母|无调整|缺乏.*效应|did not report|not reported", re.I,
)
_CAUSAL = re.compile(r"终身|永久|必然|保证|导致|预防有效|应当治疗|建议用药|风险.*\d+(?:\.\d+)?倍")
_CI_MARKER = re.compile(r'CI|置信区间|confidence interval', re.I)
_CI = re.compile(r'95\s*%?\s*(?:CI|置信区间|confidence interval)\s*[,，:：]?\s*'
                 r'[（(\[［]?\s*([\d.]+)\s*(?:to|[–−-])\s*([\d.]+)', re.I)


def check_atom(item, graph):  # noqa: PLR0915 - one source-bound atomic validation boundary
    atom = Atom.model_validate(item)
    facts = {f['claim_id']: f for f in graph['facts']}
    if len(set(atom.claim_ids)) != len(atom.claim_ids) or not set(atom.claim_ids) <= set(facts):
        raise StageError('report', 'atomic assertion has unknown or duplicate claim identities')
    evidence = '\n'.join(facts[cid]['text'] for cid in atom.claim_ids)
    safety_text = re.sub(r'不能识别终身不出血|不能判定终身安全|不代表终身安全', '', atom.text)
    if _ABSENCE.search(atom.text) or _CAUSAL.search(safety_text):
        raise StageError('report', 'global absence/causal assertion is not mechanically supported')
    numbers = set(_NUM.findall(atom.text))
    if not numbers <= set(_NUM.findall(evidence)):
        raise StageError('report', 'atomic numerical value is not in its exact evidence')
    for label, pattern in [('OR', r'\bOR\b|odds ratio'), ('HR', r'\bHR\b|hazard ratio'),
                           ('RR', r'\bRR\b|risk ratio|relative risk')]:
        if (atom.effect_type == label or re.search(r'\b' + label + r'\b', atom.text)):
            if not re.search(pattern, evidence, re.I):
                raise StageError('report', 'atomic effect type differs from original evidence')
    if atom.kind == 'effect':
        patterns = {'OR': r'OR|odds ratio', 'HR': r'HR|hazard ratio',
                    'RR': r'RR|risk ratio|relative risk'}
        if atom.effect_type == 'unknown' or not atom.quantity:
            raise StageError('report', 'effect assertion must register its type and value')
        relation = (r'\b(?:' + patterns[atom.effect_type]
                    + r')\b(?:\s+for\s+[^.;()]{1,100}?)?\s*[,=:]?\s*(?:was\s*)?'
                    + re.escape(atom.quantity) + r'(?!\d|\.\d)')
        matches = list(re.finditer(relation, evidence, re.I))
        if not matches:
            raise StageError('report', 'effect point estimate is not bound to its source type')
        if not re.search(r'(?<![A-Za-z])' + atom.effect_type + r'\s*(?:为|是|=|：|:)\s*'
                         + re.escape(atom.quantity) + r'(?!\d|\.\d)', atom.text):
            raise StageError('report', 'effect text differs from registered point estimate')
        clauses = []
        effect_token = re.compile(
            r'\b(?:(?:unadjusted|adjusted|crude|multivariable)\s+)?'
            r'(?:OR|HR|RR|odds ratio|hazard ratio|relative risk|risk ratio)\b', re.I,
        )
        for fact in [facts[cid] for cid in atom.claim_ids]:
            positions = list(effect_token.finditer(fact['text']))
            for index, token in enumerate(positions):
                end = (positions[index + 1].start()
                       if index + 1 < len(positions) else len(fact['text']))
                segment = fact['text'][token.start():end]
                if re.search(relation, segment, re.I):
                    clauses.append(segment)
        intervals = _CI.findall(atom.text)
        if _CI_MARKER.search(atom.text):
            if len(intervals) != 1:
                raise StageError('report', 'CI marker is unparsed or ambiguous')
            clauses = [c for c in clauses if intervals[0] in _CI.findall(c)]
            if not clauses:
                raise StageError('report', 'CI is not bound to the same source point estimate')
        unadjusted = re.search(r'未(?:经)?调整|\bunadjusted\b|粗OR', atom.text, re.I)
        if unadjusted:
            if not any(re.search(r'\bunadjusted\b|\bunivaria\w*\b|\bcrude\b', c, re.I)
                       for c in clauses):
                raise StageError('report', 'unadjusted estimate binding is ambiguous')
        elif re.search(r'调整|\badjusted\b', atom.text, re.I):
            if not any(re.search(r'\badjusted\b|\bmultivaria\w*\b', c, re.I)
                       and not re.search(r'\bunadjusted\b|\bunivaria\w*\b|\bcrude\b', c, re.I)
                       for c in clauses):
                raise StageError('report', 'adjusted estimate binding is ambiguous')
    if atom.kind == 'count':
        if atom.unit == 'unknown' or not atom.quantity or atom.quantity not in numbers:
            raise StageError('report', 'count requires a registered source-bound quantity and unit')
        source_unit, chinese_unit = _UNITS[atom.unit]
        if not re.search(r'\b' + re.escape(atom.quantity) + r'\s*(?:' + source_unit + ')',
                         evidence, re.I):
            raise StageError('report', 'atomic quantity/unit binding differs from original')
        occurrences = list(_NUM.finditer(atom.text))
        if len(occurrences) != 1 or not re.search(
                re.escape(atom.quantity) + r'\s*' + chinese_unit, atom.text):
            raise StageError('report', 'count text must contain one source-bound quantity/unit')
    if atom.kind == 'fraction':
        if atom.unit == 'unknown' or not re.fullmatch(r'\d+/\d+', atom.quantity):
            raise StageError('report', 'fraction requires exact n/N and denominator unit')
        source_unit, chinese_unit = _UNITS[atom.unit]
        if not re.search(re.escape(atom.quantity) + r'\s*(?:' + source_unit + ')', evidence, re.I):
            raise StageError('report', 'fraction does not match original denominator and unit')
        if (not re.search(re.escape(atom.quantity) + r'\s*' + chinese_unit, atom.text)
                or len(_NUM.findall(atom.text)) != 2):
            raise StageError('report', 'fraction text differs from registered original n/N')
    if atom.kind == 'description' and numbers:
        raise StageError('report', 'numbers must be registered as a count/effect/conflict atom')
    if atom.kind == 'source_conflict':
        competing = [set(_NUM.findall(facts[cid]['text'])) for cid in atom.claim_ids]
        if (len(atom.claim_ids) < 2 or not re.search(r'冲突|不一致|待澄清|矛盾', atom.text)
                or any(not values <= numbers for values in competing)
                or re.search(r'采用|正确|实际为|子组|因此', atom.text)):
            raise StageError('report', 'source conflict must retain both original values')
    return {'status': 'source_conflict' if atom.kind == 'source_conflict' else 'supported',
            'atom': atom.model_dump(), 'supporting': [c for cid in atom.claim_ids
                                                     for c in facts[cid]['citations']],
            'absence_inference_permitted': False, 'clinical_truth_certified': False}


async def synthesize_grounded_report(  # noqa: PLR0915 - bounded draft/review contract
    graph, llm, topic, audit, bundle, maximum, *, audit_callback=None,
):
    from .research import _render_evidence_limits

    facts = {f['claim_id']: f for f in graph['facts']}
    # Selection controls the displayed outline, never the verifier's evidence universe.
    candidate_ids = list(facts)
    # Each source's complete dependency packet is a separate review scope.
    by_source = {}
    for cid in candidate_ids:
        sid = facts[cid]['citations'][0]['source_id']
        by_source.setdefault(sid, []).append(facts[cid])
    atoms, traces, attempts = [], [], []
    for sid, candidates in sorted(by_source.items()):
        compact = [{'claim_id': f['claim_id'], 'text': f['text']} for f in candidates]
        complete_source = [b for b in bundle['source_blocks'] if b['source_id'] == sid]
        prompt = (
            '为本研究写最多6条中文原子解读，每条只陈述一件事，必须带claim_ids。'
            '紧扣谁在何种挑战条件下出血、阴性挑战、已调整关联和反证；'
            '不要用常规人口学或实验室描述占掉核心条件性风险证据。'
            'kind只能description；text禁止任何阿拉伯数字，schema会拒绝。'
            '不要重打OR/CI/人数/分数：程序将直接在每条解读下附上所引完整原文，保留数值和单位。'
            '中文只解释本来源与主题有关的现象、调整关联和局限，不用中文数字绕过数值限制。'
            '无症状、挑战阴性、治疗保护、出血和流产须区别；选择对应原文claim_id。'
            '遇源文冲突明确写冲突未解、同时引用两处，不用子组猜测或选定正确值。'
            '禁止全文缺失、治疗、终身安全、因果结论；不猜图文冲突。'
            '保留调整项、CI、分母、治疗状态；已有局限只是审查线索不是事实。'
            '所有事实均已精确入账，不能从未选事实推断来源未报告。'
            '\n主题：' + topic + '\nLIMITATIONS:'
            + json.dumps(audit['known_limitations'], ensure_ascii=False)
            + '\nFACTS:' + json.dumps(compact, ensure_ascii=False)
            + '\nFULL_SOURCE:' + json.dumps(complete_source, ensure_ascii=False)
        )
        last_error = None
        for attempt in range(2):
            row = {'source_id': sid, 'attempt': attempt, 'status': 'started'}
            attempts.append(row)
            if audit_callback:
                audit_callback(attempts)
            try:
                draft = await llm.chat_structured(prompt, AtomDraft)
                row['atoms'] = [a.model_dump() for a in draft.atoms]
                scope_ids = {f['claim_id'] for f in candidates}
                if any(not set(a.claim_ids) <= scope_ids for a in draft.atoms):
                    raise StageError('report', 'draft references sources outside this review scope')
                mechanical, errors = [], []
                for index, atom in enumerate(draft.atoms):
                    try:
                        mechanical.append(check_atom(atom.model_dump(), graph))
                    except StageError as exc:
                        errors.append({'index': index, 'reason': str(exc)})
                if errors:
                    row['mechanical_errors'] = errors
                    raise StageError('report', 'atomic mechanical checks rejected specific items')
                verification = await llm.chat_structured(
                    '逐条回查全部原文依赖，判断中文陈述是否准确，特别核对单位/分母/调整项/'
                    '治疗条件/来源冲突。不能仅相信writer的引用；原文未支持或存在反证则'
                    'contradicted/insufficient。返回每项index、status、supporting_ids、'
                    'counterevidence_ids、reason。source_conflict只有如实并列原值才可接受。'
                    '\nATOMS:' + json.dumps(
                        [a.model_dump() for a in draft.atoms], ensure_ascii=False,
                    )
                    + '\nCOMPLETE_SOURCE_DEPENDENCIES:' + json.dumps(compact, ensure_ascii=False)
                    + '\nFULL_SOURCE:' + json.dumps(complete_source, ensure_ascii=False),
                    AtomChecks,
                )
                row['checks'] = verification.model_dump()
                if (len(verification.checks) != len(draft.atoms)
                        or {c.index for c in verification.checks} != set(range(len(draft.atoms)))):
                    raise StageError('report', 'atomic verifier omitted assertion checks')
                verified = {check.index: check for check in verification.checks}
                for check in verification.checks:
                    expected_status = ('source_conflict'
                                       if re.search(r'冲突|不一致|待澄清|矛盾',
                                                    draft.atoms[check.index].text)
                                       else 'supported')
                    if (check.status != expected_status
                            or not check.supporting_ids
                            or not set(draft.atoms[check.index].claim_ids)
                            <= set(check.supporting_ids)
                            or not check.reason.strip()
                            or not set(check.supporting_ids + check.counterevidence_ids)
                            <= scope_ids):
                        raise StageError('report', 'atomic statement lacks verified support')
                row.update(status='accepted', checks=verification.model_dump())
                if audit_callback:
                    audit_callback(attempts)
                atoms.extend(draft.atoms)
                traces.extend({'source_id': sid, 'attempt': attempt, 'mechanical': m,
                               'semantic': verified[i].model_dump()}
                              for i, m in enumerate(mechanical))
                break
            except (StageError, ValidationError, LLMError) as exc:
                detail = find_llm_diagnostic(exc)
                if (isinstance(exc, LLMError)
                        and (detail is None or detail.reason != 'schema_invalid')):
                    row.update(status='failed', failure_type=type(exc).__name__)
                    if detail is not None:
                        row['diagnostic'] = detail.model_dump(exclude_none=True)
                    if audit_callback:
                        audit_callback(attempts)
                    raise
                last_error = exc
                row.update(status='rejected', failure_type=(
                    'schema_invalid' if isinstance(exc, (ValidationError, LLMError))
                    else 'atomic_invalid'),
                    reason=(str(exc) if isinstance(exc, StageError)
                            else 'structured schema invalid'))
                if audit_callback:
                    audit_callback(attempts)
                if attempt:
                    terminal = StageError('report', 'atomic draft/review correction exhausted')
                    terminal.diagnostic = LLMFailureDiagnostic(reason='selection_invalid')
                    raise terminal from None
                prompt += ('\n保守修复一次：仅保留可核实原子事实；修正schema/单位/效应/冲突。'
                           + '\n失败类别：' + row['reason']
                           + '\n逐项机械检查：' + json.dumps(
                               row.get('mechanical_errors', []), ensure_ascii=False,
                           )
                           + '\n只能输出description，无阿拉伯数字；数值由程序原文附证，'
                           '不要用中文数字改写数值或猜测缺失单位。'
                           + '\n上次待修正原子项：' + json.dumps(
                               row.get('atoms', []), ensure_ascii=False,
                           ))
        else:
            raise last_error or StageError('report', 'atomic synthesis failed')
    sources = {s['source_id']: s for s in graph['sources']}
    indices = {sid: i for i, sid in enumerate(sorted(sources), 1)}
    lines = ['# ' + topic, '', '> 中文逐论断附精确原文和完整来源反查记录；'
             '模型语义检查仍可能出错，不代表临床或人类专家验收。', '']
    claims = []
    for sid, source in sorted(sources.items()):
        lines.extend(['## 原始研究：' + source.get('title', sid), ''])
        for atom in atoms:
            if facts[atom.claim_ids[0]]['citations'][0]['source_id'] != sid:
                continue
            citations = [c for cid in atom.claim_ids for c in facts[cid]['citations']]
            refs = ' '.join(f'[{i}]' for i in sorted({indices[c['source_id']] for c in citations}))
            lines.extend([atom.text + ' ' + refs, ''])
            claims.append({'text': atom.text, 'claim_ids': atom.claim_ids, 'citations': citations})
            for cid in atom.claim_ids:
                literal = facts[cid]['text']
                lines.extend(['**数值与范围原文证据：**', '', literal + ' ' + refs, ''])
                claims.append({'text': literal, 'claim_ids': [cid],
                               'citations': facts[cid]['citations']})
    used = sorted({cid for c in claims for cid in c['claim_ids']})
    audit['selected_claim_ids'] = used
    audit['unselected_claim_ids'] = sorted(set(facts) - set(used))
    lines.extend(_render_evidence_limits(audit, indices))
    lines.extend(['## 来源', ''])
    lines.extend(f'[{indices[sid]}] {s.get("title", sid)} — {s["locator"]}'
                 for sid, s in sorted(sources.items()))
    markdown = '\n'.join(lines) + '\n'
    if len(markdown) > maximum:
        raise StageError('report', 'grounded report exceeds budget; no truncation')
    return {'markdown': markdown, 'claims': claims, 'headings': [], 'evidence_audit': audit,
            'citation_coverage': 1.0,
            'content_sha256': hashlib.sha256(markdown.encode()).hexdigest(),
            'verification': 'atomic-full-source/v1',
            'verification_limit': 'Not clinical acceptance',
            'grounding_audit': {**bundle, 'contract': 'atomic-full-source/v1',
                                'atomic_checks': traces, 'attempts': attempts,
                                'numerical_rendering': 'literal_source_only',
                                'clinical_truth_certified': False}}
