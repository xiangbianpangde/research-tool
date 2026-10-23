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


class Observation(BaseModel):
    model_config = {'extra': 'forbid'}
    code: Literal['untreated_negative_challenge', 'treated_negative_challenge',
                  'challenge_bleeding', 'adjusted_association', 'unadjusted_association',
                  'null_association', 'source_conflict', 'subtype_limitation', 'study_observation']
    claim_ids: list[str] = Field(min_length=1, max_length=4)


class ObservationPlan(BaseModel):
    model_config = {'extra': 'forbid'}
    observations: list[Observation] = Field(min_length=1, max_length=6)


_TEMPLATES = {
    'untreated_negative_challenge': '原文记载未经预防而已挑战未异常出血者；'
        '这证明阴性挑战存在，但不等于其他挑战或终身安全，原文计数不自动成为下一次挑战概率。',
    'treated_negative_challenge': '原文记载预防治疗条件下的阴性挑战；'
        '不能据此确定未治疗时的天然风险，也不能与未经预防的阴性挑战混为一类。',
    'challenge_bleeding': '原文在下列明确暴露或结局口径中报告出血；'
        '数值、分母、重复事件与治疗条件按所附原句解释，不外推个人终身风险。',
    'adjusted_association': '原文报告调整分析中的关联；'
        '效应类型、调整项及区间见原句，调整关联不等于因果贡献或可部署的个体风险系数。',
    'unadjusted_association': '原文提供描述性或未调整比较；'
        '它可提出待研究信号，不能由此单独确定独立效应或治疗因果作用。',
    'null_association': '原文在本样本与结局口径下报告未达显著或未见关联；'
        '这不证明风险相等或排除真实作用，精度与适用条件仍受原研究限制。',
    'source_conflict': '所引原文存在未解的口径或数值冲突；'
        '保留各处原值，不选择正确版本或据此重建绝对风险。',
    'subtype_limitation': '原文涉及的亚型或混合人群范围见所附证据；'
        '不能把混合结果直接当作某一亚型的专属风险。',
    'study_observation': '原文报告下列研究内观察；'
        '其人群、结局、治疗与时间口径仅限原研究，不能直接转成个人绝对风险。',
}


def render_observation(observation, graph):
    observation = Observation.model_validate(observation)
    facts = {f['claim_id']: f for f in graph['facts']}
    if not set(observation.claim_ids) <= set(facts):
        raise StageError('report', 'observation references unknown evidence')
    # Event predicates must all hold in one cited evidence unit, never across events.
    primary_units = [facts[cid]['text'] for cid in observation.claim_ids]
    evidence = '\n'.join(primary_units)
    code = observation.code
    patterns = {
        'untreated_negative_challenge': (
            r'without\s+(?:preoperative\s+)?(?:prophy\w*|prevent\w*|advanced actions|'
            r'pro[- ]haemostatic|replacement)|no\s+prophy\w*|\buntreated\b',
            r'no\s+(?:abnormal\s+)?bleeding|uneventful',
            r'surger|childbirth|dental|extract|challenge',
        ),
        'treated_negative_challenge': (r'preop|prophy|infusion|replacement',
                                       r'no\s+(?:abnormal\s+|postoperative\s+)?bleeding|'
                                       r'without\s+(?:abnormal\s+)?bleeding|uneventful'),
        'challenge_bleeding': (r'bleed|hemorrhag|PPH',),
        'adjusted_association': (r'\badjusted\b|multivaria',),
        'unadjusted_association': (r'\bunadjusted\b|\bcrude\b|%|proportion|incidence',),
        'null_association': (r'not associated|no signi(?:fi|ﬁ)cant|not signi(?:fi|ﬁ)c|P\s*>\s*',),
        'subtype_limitation': (r'hypodys|dysfibrin|hypofibrin|subtype|classified',),
    }
    if code == 'source_conflict' and len(set(observation.claim_ids)) < 2:
        raise StageError('report', 'source conflict requires competing original evidence')
    required = patterns.get(code, ())
    matches = [unit for unit in primary_units
               if all(re.search(p, unit, re.I) for p in required)]
    if code in {'treated_negative_challenge', 'untreated_negative_challenge'}:
        absence = (r'\buneventful\b|\bno\s+(?:abnormal\s+|postoperative\s+)?bleeding'
                   r'\s*(?:[.;,)]|$|occurred\b|was\s+observed\b|after\b|following\b)|'
                   r'\bwithout\s+(?:abnormal\s+)?bleeding\s*(?:[.;,)]|$|after\b)')
        matches = [unit for unit in matches if re.search(absence, unit, re.I)
                   and not re.search(r'\b(?:not|never|possibly|uncertain)\b[^.;\n]*'
                                     r'\buneventful\b', unit, re.I)]
    if required and not matches:
        raise StageError('report', 'observation predicates do not share a source evidence unit')
    treatment = r'(?:prophy\w*|replacement|prevent\w*|infusions?|pro[- ]haemostatic)'
    negative_treatment = r'\b(?:without|no|not|never)\b[^.;\n]{0,50}\b' + treatment
    if code == 'treated_negative_challenge':
        positive = r'\b(?:received|receiving|administered|given|treated with)\b[^.;\n]{0,80}'
        if not any(re.search(positive + treatment, unit, re.I)
                   and not re.search(negative_treatment, unit, re.I)
                   for unit in matches):
            raise StageError('report', 'protected challenge requires affirmative treatment')
    if code == 'challenge_bleeding':
        negative_outcome = (r'\b(?:no|without|not|never)\b[^.;\n]{0,50}'
                            r'\b(?:bleed\w*|ha?emorrhag\w*|PPH)\b|'
                            r'\b(?:bleed\w*|ha?emorrhag\w*|PPH)\s+'
                            r'(?:(?:was|were)\s+(?:absent|not\s+(?:observed|reported))|'
                            r'(?:did|does)\s+not\s+occur)\b')
        affirmative = (r'(?:bleed\w*|ha?emorrhag\w*|PPH)\s+(?:occurred|was\s+observed)|'
                       r'bleeding was not severe|bleeding was not associated|'
                       r'(?:experienced|suffered|complicated by|had|presented with)'
                       r'[^.;\n]{0,80}(?:bleed\w*|ha?emorrhag\w*|PPH)')
        excluded = (r'none\s+had|avoided|no\s+signi(?:fi|ﬁ)cant|not\s+observed|'
                    r'\b(?:would|could|might|may|whether|unclear|uncertain|possibly|hypothetical|if)\b')
        if not any(re.search(affirmative, unit, re.I)
                   and not re.search(negative_outcome, unit, re.I)
                   and not re.search(excluded, unit, re.I) for unit in matches):
            raise StageError('report', 'positive bleeding event needs an affirmative occurrence')
    if code == 'adjusted_association' and not re.search(r'\badjusted\b|multivaria', evidence, re.I):
        raise StageError('report', 'adjusted type cannot use unadjusted wording')
    return {'kind': 'description', 'text': _TEMPLATES[code],
            'claim_ids': observation.claim_ids, 'code': code}


def resolve_required_evidence(graph, specs):
    """Pin required source literals; the caller's interpretation never becomes truth."""
    resolved = []
    for spec in specs:
        if spec.get('kind') not in {'required', 'source_conflict'} or not spec.get('quotes'):
            raise StageError('report', 'invalid required source-evidence specification')
        ids = []
        for quote in spec['quotes']:
            hits = [f['claim_id'] for f in graph['facts'] if f['text'] == quote
                    and any(c['locator'] == spec['locator'] for c in f['citations'])]
            if len(hits) != 1:
                raise StageError('report', 'required original evidence missing or ambiguous')
            ids.append(hits[0])
        if len(ids) != len(set(ids)):
            raise StageError('report', 'duplicate required evidence cannot establish a conflict')
        if spec['kind'] == 'source_conflict' and len(set(ids)) < 2:
            raise StageError('report', 'source conflict requires multiple distinct originals')
        resolved.append({'kind': spec['kind'], 'claim_ids': ids, 'locator': spec['locator']})
    return resolved


class ObservationChoice(BaseModel):
    model_config = {'extra': 'forbid'}
    choice_ids: list[str] = Field(min_length=1, max_length=6)


def build_observation_choices(candidates, graph):
    """Expose only mechanically admissible source/type bindings, never arbitrary pairings."""
    result = []
    for fact in candidates:
        for code in _TEMPLATES:
            if code == 'source_conflict':
                continue
            try:
                render_observation({'code': code, 'claim_ids': [fact['claim_id']]}, graph)
            except StageError:
                continue
            result.append({'choice_id': f'C{len(result) + 1}', 'code': code,
                           'claim_ids': [fact['claim_id']], 'evidence_text': fact['text']})
    return result


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
        choices = build_observation_choices(candidates, graph)
        choice_map = {c['choice_id']: c for c in choices}
        complete_source = [b for b in bundle['source_blocks'] if b['source_id'] == sid]
        prompt = (
            '为本研究选择最多6个关键观察，只返回choice_ids，必须来自CHOICES。'
            '不得另配code/claim_ids，不生成text或新事实。'
            '程序按固定中文模板解释类型并逐字附上原证据数值/分母/CI。'
            '紧扣谁在何种挑战条件下出血、未经预防的阴性挑战、保护下阴性、调整关联和反证。'
            '代码含义：untreated_negative_challenge仅明确未预防且挑战后无出血；'
            'treated_negative_challenge为预防后无出血；challenge_bleeding为挑战出血；'
            'adjusted_association为明确调整分析，unadjusted_association为粗比较；'
            'null_association为阴性关联；source_conflict须同时引用两处冲突；'
            'subtype_limitation为亚型范围；无法精确定型则study_observation。'
            '不要用常规人口学占掉核心证据，不混淆流产和出血，不猜子组。'
            '原句/上下文均已入账；既有限制是待核对线索不是事实真值。'
            '\n主题：' + topic + '\nLIMITATIONS:'
            + json.dumps(audit['known_limitations'], ensure_ascii=False)
            + '\nFACTS:' + json.dumps(compact, ensure_ascii=False)
            + '\nCHOICES:' + json.dumps(
                [{k: v for k, v in c.items() if k != 'evidence_text'} for c in choices],
                ensure_ascii=False,
            )
            + '\nFULL_SOURCE:' + json.dumps(complete_source, ensure_ascii=False)
        )
        last_error = None
        for attempt in range(2):
            row = {'source_id': sid, 'attempt': attempt, 'status': 'started'}
            attempts.append(row)
            if audit_callback:
                audit_callback(attempts)
            try:
                selection = await llm.chat_structured(prompt, ObservationChoice)
                if (len(set(selection.choice_ids)) != len(selection.choice_ids)
                        or not set(selection.choice_ids) <= set(choice_map)):
                    raise StageError('report', 'unknown or duplicate closed observation choice')
                plan = ObservationPlan(observations=[
                    {'code': choice_map[cid]['code'], 'claim_ids': choice_map[cid]['claim_ids']}
                    for cid in selection.choice_ids
                ])
                if attempt and attempts[-2].get('plan'):
                    prior_ids = {cid for o in attempts[-2]['plan']['observations']
                                 for cid in o['claim_ids']}
                    now_ids = {cid for o in plan.observations for cid in o.claim_ids}
                    if prior_ids != now_ids:
                        raise StageError('report', 'repair cannot replace selected evidence')
                row['plan'] = plan.model_dump()
                row['choice_ids'] = selection.choice_ids
                rendered, plan_errors = [], []
                for index, observation in enumerate(plan.observations):
                    try:
                        rendered.append(render_observation(observation, graph))
                    except StageError as exc:
                        plan_errors.append({'index': index, 'code': observation.code,
                                            'reason': str(exc)})
                if plan_errors:
                    row['mechanical_errors'] = plan_errors
                    raise StageError('report', 'observation plan has unsupported type bindings')
                draft = AtomDraft(atoms=[{k: value for k, value in a.items() if k != 'code'}
                                         for a in rendered])
                row['atoms'] = [a.model_dump() for a in draft.atoms]
                scope_ids = {f['claim_id'] for f in candidates}
                if any(not set(a.claim_ids) <= scope_ids for a in draft.atoms):
                    raise StageError('report', 'draft references sources outside this review scope')
                mechanical, errors = [], []
                for index, atom in enumerate(draft.atoms):
                    try:
                        # Medical prose is an allowlisted template, never model free text.
                        mechanical.append({'status': 'template_bound',
                                           'template': rendered[index]['code'],
                                           'supporting': [c for cid in atom.claim_ids
                                                          for c in facts[cid]['citations']]})
                    except StageError as exc:
                        errors.append({'index': index, 'reason': str(exc)})
                if errors:
                    row['mechanical_errors'] = errors
                    raise StageError('report', 'atomic mechanical checks rejected specific items')
                verification = await llm.chat_structured(
                    '逐条回查全部原文依赖，判断固定中文类型模板是否适用，模板中的不可推断'
                    '边界为程序固定规则，不作为额外论文事实。特别核对单位/分母/调整项/'
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
                           + '\n只能输出choice_ids，从CHOICES选择合法ID，不得另配类型/原句。'
                           '保留上次选中全部claim_ids，仅可调整同一证据的类型，不删换证据。'
                           + '\n语义复核结果：' + json.dumps(
                               row.get('checks', {}), ensure_ascii=False,
                           )
                           + '\n上次待修正原子项：' + json.dumps(
                               row.get('plan', row.get('atoms', [])), ensure_ascii=False,
                           ))
        else:
            raise last_error or StageError('report', 'atomic synthesis failed')
    sources = {s['source_id']: s for s in graph['sources']}
    indices = {sid: i for i, sid in enumerate(sorted(sources), 1)}
    lines = ['# ' + topic, '', '> 中文逐论断附精确原文和完整来源反查记录；'
             '模型语义检查仍可能出错，不代表临床或人类专家验收。', '']
    claims = []
    required = bundle.get('required_evidence', [])
    if required:
        lines.extend(['## 必需原文证据与未解冲突', '',
                      '以下由任务规格指定原句，身份/跨度经程序回验；'
                      '它们不是作者或配置提供者的解释自动成为真值。', ''])
        for item in required:
            lines.extend(['### ' + ('原文冲突未解' if item['kind'] == 'source_conflict'
                                   else '必需证据锚点'), ''])
            if item['kind'] == 'source_conflict':
                lines.extend(['此为任务方标记、经原文身份核验的冲突候选，并非程序证明矛盾；'
                              '并列原值供审校，不自行调和或重建绝对风险。', ''])
            for cid in item['claim_ids']:
                fact = facts[cid]
                refs = ' '.join(f'[{indices[c["source_id"]]}]' for c in fact['citations'])
                lines.extend([fact['text'] + ' ' + refs, ''])
                claims.append({'text': fact['text'], 'claim_ids': [cid],
                               'citations': fact['citations']})
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
