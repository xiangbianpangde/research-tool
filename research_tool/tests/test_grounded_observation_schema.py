"""Do not ask the writer to retype numerical facts; bind them in literal evidence rows."""
import pytest
from pydantic import ValidationError
from research_tool.infrastructure.llm import MockLLMClient
from research_tool.nine_loop.grounded_synthesis import synthesize_grounded_report
from research_tool.tests.test_atomic_grounding import graph_for


@pytest.mark.asyncio
async def test_chinese_observation_schema_rejects_numeric_prose_and_report_shows_literal_values():
    graph = graph_for(['The adjusted OR was 5.8 (95% CI 1.2–28.0).'])
    seen = []
    def reply(prompt, schema):
        if schema.__name__ == 'ObservationPlan':
            with pytest.raises(ValidationError):
                schema.model_validate({'observations':[{'code':'study_observation','text':'OR为5.8。','claim_ids':['c0']}]})
            seen.append(schema.__name__)
            return schema(observations=[{'code':'adjusted_association','claim_ids':['c0']}])
        if schema.__name__ == 'AtomChecks':
            return schema(checks=[{'index':0,'status':'supported','supporting_ids':['c0'],
                                   'counterevidence_ids':[],'reason':'Faithful bounded interpretation.'}])
        raise AssertionError(schema.__name__)
    audit={'selected_claim_ids':['c0'],'known_limitations':[],'coverage':[],
           'total_claims':1,'unresolved_findings':[]}
    result=await synthesize_grounded_report(graph,MockLLMClient(structured_response=reply),'test',audit,
          {'source_blocks':[{'source_id':'source','text':graph['sources'][0]['text']}]},30000)
    assert seen
    assert '5.8 (95% CI 1.2–28.0)' in result['markdown']
    assert result['claims'][0]['text'].startswith('原文报告')
    assert result['claims'][1]['text'] == graph['facts'][0]['text']
    assert result['grounding_audit']['numerical_rendering'] == 'literal_source_only'
