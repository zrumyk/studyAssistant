import json
import httpx
import pytest
from study import cloud,config,db
from study.schemas import Plan,PythonAction,ContentReview,PythonRepair,RepairAdvice
from study.codegen import PythonBundle,ModuleSource
from study.structured import deepseek_schema


@pytest.mark.parametrize('model',[Plan,PythonAction,ContentReview,PythonBundle,ModuleSource,PythonRepair,RepairAdvice])
def test_deepseek_schema_is_finite_closed_and_all_fields_required(model):
    schema=deepseek_schema(model.model_json_schema())
    def check(node):
        assert not any(k in node for k in ('$ref','$defs','minLength','maxLength','minItems','maxItems','default'))
        if node.get('type')=='object':
            assert node['additionalProperties'] is False
            assert set(node['required'])==set(node['properties'])
            for value in node['properties'].values():check(value)
        if node.get('type')=='array':check(node['items'])
        for value in node.get('anyOf',[]):check(value)
    check(schema)


@pytest.mark.parametrize('thinking',[False,True])
def test_strict_response_uses_one_forced_function_and_counts_schema_in_reservation(project,monkeypatch,thinking):
    config.save_settings({'cloud_provider':'deepseek','cloud_model':'deepseek-flash','cloud_thinking':False})
    monkeypatch.setattr(cloud,'key',lambda *a:'fake-test-key')
    schema=deepseek_schema(ModuleSource.model_json_schema())
    quote=cloud.quote(project['id'],'Return data','strict test',response_schema=schema,thinking_override=thinking)
    assert quote['maximum_estimate_usd']>cloud.quote(project['id'],'Return data','text test')['maximum_estimate_usd']
    cloud.approve_and_enqueue(quote['id'])
    def handler(request):
        assert request.url.path=='/beta/chat/completions'
        body=json.loads(request.content)
        function=body['tools'][0]['function']
        assert function['strict'] is True and function['parameters']==schema
        assert body['tool_choice']==('auto' if thinking else {'type':'function','function':{'name':'submit_result'}})
        assert 'response_format' not in body
        assert body['thinking']=={'type':'enabled' if thinking else 'disabled'}
        if thinking:
            assert body['reasoning_effort']=='low' and 'temperature' not in body
        else:
            assert body['temperature']==.15
        return httpx.Response(200,json={'choices':[{'message':{'content':None,'tool_calls':[{'type':'function','function':{'name':'submit_result','arguments':'{"content":"print(1)"}'}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':200,'completion_tokens':20}})
    original=httpx.Client
    monkeypatch.setattr(cloud.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler)))
    result=cloud.send(quote['id'])
    assert result['complete']
    assert ModuleSource.model_validate_json(result['text']).content=='print(1)'
    assert db.one('SELECT state FROM cloud_requests WHERE id=?',(quote['id'],))['state']=='done'


@pytest.mark.parametrize('calls',[
    [],[{'type':'function','function':{'name':'execute_shell','arguments':'{}'}}],
    [{'type':'function','function':{'name':'submit_result','arguments':'{}'}}]*2,
])
def test_unknown_missing_or_multiple_tool_responses_are_not_accepted(calls):
    payload={'choices':[{'message':{'content':'text','tool_calls':calls},'finish_reason':'tool_calls'}]}
    text,_,_=cloud.response_text_and_usage('deepseek',payload,expected_tool='submit_result')
    assert text==''


def test_thinking_auto_may_return_plain_json_but_not_unexpected_tools_or_extra_payloads():
    payload={'choices':[{'message':{'content':'{"content":"print(1)"}'},'finish_reason':'stop'}]}
    assert cloud.response_text_and_usage('deepseek',payload,expected_tool='submit_result')[0]==''
    text,_,_=cloud.response_text_and_usage('deepseek',payload,expected_tool='submit_result',allow_json_fallback=True)
    assert ModuleSource.model_validate_json(text).content=='print(1)'
    payload['choices'][0]['message']['tool_calls']=[{'type':'function','function':{'name':'execute_shell','arguments':'{}'}}]
    assert cloud.response_text_and_usage('deepseek',payload,expected_tool='submit_result',allow_json_fallback=True)[0]==''
    del payload['choices'][0]['message']['tool_calls']
    for invalid in ('[]','{"content":"ok"} extra','plain commentary'):
        payload['choices'][0]['message']['content']=invalid
        assert cloud.response_text_and_usage('deepseek',payload,expected_tool='submit_result',allow_json_fallback=True)[0]==''
