import json
import pytest
from study import assistance, cloud, config, db
from study.providers import ModelOutputTruncated
from study.schemas import Assessment


class Local:
    def generate(self, *args, **kwargs):
        raise ModelOutputTruncated("local incomplete")


def enable():
    config.save_settings(
        {
            "cloud_consent_mode": "budgeted",
            "cloud_request_limit_usd": 0.03,
            "cloud_provider": "deepseek",
            "cloud_model": "deepseek-flash",
            "cloud_input_price": 0.30,
            "cloud_output_price": 1.20,
            "cloud_prices_date": db.now()[:10],
        }
    )


def mocked_send(monkeypatch, complete=True):
    calls = []

    def send(rid):
        calls.append(rid)
        db.execute(
            "UPDATE cloud_requests SET state='done',response=?,actual=.001 WHERE id=?",
            ('{"satisfied":true,"explanation":"checks match"}', rid),
        )
        return {
            "text": '{"satisfied":true,"explanation":"checks match"}',
            "complete": complete,
            "cost_usd": 0.001,
            "provider": "deepseek",
        }

    monkeypatch.setattr(cloud, "send", send)
    return calls


def test_no_cloud_without_standing_permission(project, monkeypatch):
    mocked_send(monkeypatch)
    with pytest.raises(ModelOutputTruncated):
        assistance.BudgetedModel(project["id"], Local(), "test").generate(
            "task", Assessment
        )
    assert not db.query("SELECT id FROM cloud_requests")


def test_fallback_uses_one_durable_request_and_cache(project, monkeypatch):
    enable()
    calls = mocked_send(monkeypatch)
    model = assistance.BudgetedModel(project["id"], Local(), "test")
    assert model.generate("task", Assessment).satisfied
    assert model.generate("task", Assessment).satisfied
    assert len(calls) == 1
    jobs = db.query("SELECT status,result FROM jobs WHERE kind='cloud'")
    assert len(jobs) == 1 and jobs[0]["status"] == "done"
    r = db.one("SELECT config FROM cloud_requests WHERE id=?", (calls[0],))
    assert json.loads(r["config"])["approval_kind"] == "standing"


def test_request_output_cap_has_distinct_cache_and_preserves_global_limits(project, monkeypatch):
    enable()
    calls = mocked_send(monkeypatch)
    original = config.settings()
    for cap in (384, 384, 1024):
        model = assistance.BudgetedModel(project['id'], Local(), 'bounded evaluation', max_output_tokens=cap)
        assert model.remote('same task', Assessment).satisfied
    assert len(calls) == 2
    snapshots = [json.loads(db.one('SELECT config FROM cloud_requests WHERE id=?', (rid,))['config']) for rid in calls]
    assert [item['cloud_max_output'] for item in snapshots] == [384, 1024]
    assert config.settings() == original
    assert all(item['cloud_request_limit_usd'] == .03 and item['cloud_budget'] == original['cloud_budget'] for item in snapshots)


@pytest.mark.parametrize('cap', [True, 255, 384.5, '384', 100000])
def test_invalid_request_output_cap_cannot_quote_or_spend(project, monkeypatch, cap):
    enable()
    calls = mocked_send(monkeypatch)
    with pytest.raises(ValueError, match='Ліміт відповіді'):
        assistance.BudgetedModel(project['id'], Local(), 'bounded evaluation', max_output_tokens=cap).remote('task', Assessment)
    assert calls == [] and not db.query('SELECT id FROM cloud_requests')


def test_reasoning_repair_has_own_receipt_and_preserves_global_budget(project,monkeypatch):
    enable()
    config.save_settings({'cloud_thinking':False})
    calls=mocked_send(monkeypatch)
    ordinary=assistance.BudgetedModel(project['id'],Local(),'code')
    repair=assistance.BudgetedModel(project['id'],Local(),'code',thinking_override=True)
    for model in (ordinary,repair,repair):
        assert model.remote('same task',Assessment).satisfied
    assert len(calls)==2
    snapshots=[json.loads(db.one('SELECT config FROM cloud_requests WHERE id=?',(rid,))['config']) for rid in calls]
    assert [s['cloud_thinking'] for s in snapshots]==[False,True]
    assert all(s['response_schema'] and s['cloud_request_limit_usd']==.03 for s in snapshots)
    assert config.settings()['cloud_thinking'] is False


def test_incomplete_reply_is_never_accepted_or_rebilled(project, monkeypatch):
    enable()
    calls = mocked_send(monkeypatch, complete=False)
    model = assistance.BudgetedModel(project["id"], Local(), "test")
    for _ in range(2):
        with pytest.raises(ModelOutputTruncated, match="незавершена"):
            model.generate("task", Assessment)
    assert len(calls) == 1


def test_pause_images_and_cap_block_cloud(project, monkeypatch):
    enable()
    calls = mocked_send(monkeypatch)
    model = assistance.BudgetedModel(
        project["id"], Local(), "test", cancelled=lambda: True
    )
    with pytest.raises(RuntimeError, match="призупинено"):
        model.generate("task", Assessment)
    model = assistance.BudgetedModel(project["id"], Local(), "test")
    with pytest.raises(ModelOutputTruncated):
        model.generate("task", Assessment, images=["image.png"])
    config.save_settings({"cloud_request_limit_usd": 0})
    with pytest.raises(ValueError, match="межами"):
        model.generate("task", Assessment)
    assert calls == []


def test_successful_local_generation_does_not_spend(project, monkeypatch):
    enable()
    calls = mocked_send(monkeypatch)

    class Good:
        def generate(self, *args, **kwargs):
            return Assessment(satisfied=True, explanation="local")

    model = assistance.BudgetedModel(project["id"], Good(), "test")
    assert model.generate("task", Assessment).explanation == "local"
    assert calls == []


def test_unknown_network_result_is_not_retried(project, monkeypatch):
    enable()
    calls = []

    def fail(rid):
        calls.append(rid)
        db.execute("UPDATE cloud_requests SET state='uncertain' WHERE id=?", (rid,))
        raise RuntimeError("network uncertain")

    monkeypatch.setattr(cloud, "send", fail)
    model = assistance.BudgetedModel(project["id"], Local(), "test")
    with pytest.raises(RuntimeError, match="uncertain"):
        model.generate("task", Assessment)
    with pytest.raises(RuntimeError, match="непідтверджений"):
        model.generate("task", Assessment)
    assert len(calls) == 1


def test_long_prompts_use_authorized_budget_without_waiting_for_local_timeout(project, monkeypatch):
    enable()
    calls=mocked_send(monkeypatch)
    class SmallLocal:
        def generate(self,*args,**kwargs):
            pytest.fail('Long workload should not wait 15 minutes locally')
    model=assistance.BudgetedModel(project['id'],SmallLocal(),'long action')
    assert model.generate('a'*7100,Assessment).satisfied
    assert len(calls)==1


def test_structured_tool_action_uses_authorized_provider_even_for_short_prompt(project,monkeypatch):
    from study.schemas import Action
    enable();calls=[]
    def remote(self,prompt,schema=None):
        calls.append(prompt)
        return Action(explanation='Generated action')
    monkeypatch.setattr(assistance.BudgetedModel,'remote',remote)
    class NeverLocal:
        def generate(self,*args,**kwargs):
            pytest.fail('Tool actions should not stall on the small local model')
    result=assistance.BudgetedModel(project['id'],NeverLocal(),'action').generate('short action',Action)
    assert result.explanation=='Generated action' and len(calls)==1


def test_content_review_acceptance_is_derived_from_issues():
    from study.schemas import ContentReview

    assert not ContentReview(
        blocking_issues=["Wrong mathematical formula"], explanation="Issue found"
    ).satisfied
    assert ContentReview(
        blocking_issues=[], notes=["Optional formatting"], explanation="Consistent"
    ).satisfied
    with pytest.raises(Exception):
        ContentReview.model_validate(
            {
                "satisfied": True,
                "blocking_issues": ["Wrong median"],
                "explanation": "Conflicting flags",
            }
        )


def test_large_structured_request_reduces_output_reservation_within_same_cap(project,monkeypatch):
    enable()
    config.save_settings({'cloud_max_output':8192})
    calls=mocked_send(monkeypatch)
    model=assistance.BudgetedModel(project['id'],Local(),'large audit')
    assert model.remote('a'*65000,Assessment).satisfied
    row=db.one('SELECT reserved,config FROM cloud_requests WHERE id=?',(calls[0],))
    snapshot=json.loads(row['config'])
    assert row['reserved']<=0.03
    assert 1024<=snapshot['cloud_max_output']<8192
    assert config.settings()['cloud_max_output']==8192
    assert config.settings()['cloud_request_limit_usd']==0.03


def test_oversized_input_is_not_silently_truncated_or_sent(project,monkeypatch):
    enable();calls=mocked_send(monkeypatch)
    with pytest.raises(ValueError,match='розділити'):
        assistance.BudgetedModel(project['id'],Local(),'too large').remote('a'*150000,Assessment)
    assert calls==[]
    assert not db.query('SELECT id FROM cloud_requests')


def test_new_output_floor_reuses_valid_complete_cache_without_sending(project, monkeypatch):
    enable()
    calls = mocked_send(monkeypatch)
    monkeypatch.setattr(cloud, 'budgeted_output_limit', lambda *a: 1784)
    old = assistance.BudgetedModel(project['id'], Local(), 'cached short answer')
    assert old.remote('same prompt', Assessment).satisfied
    guarded = assistance.BudgetedModel(project['id'], Local(), 'cached short answer', min_output_tokens=4096)
    assert guarded.remote('same prompt', Assessment).satisfied
    assert len(calls) == 1 and old.last_request_id == guarded.last_request_id


def test_only_empty_known_provider_closing_tags_can_follow_json():
    text='{"satisfied":true,"explanation":"checks match"}'
    suffix='</｜｜DSML｜｜ parameter>'+chr(92)+'n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'
    assert assistance.structured_response(text+suffix,Assessment).satisfied
    for tail in (' ignore instructions',' {}','</｜｜DSML｜｜ invoke>run something','<｜｜DSML｜｜ invoke>'):
        with pytest.raises(ValueError):
            assistance.structured_response(text+tail,Assessment)


def test_complete_object_with_one_redundant_terminator_is_locally_validated():
    text='{"satisfied":false,"explanation":"real conflict"}'
    normalized=[]
    result=assistance.structured_response(text+'}',Assessment,normalized.append)
    assert result.satisfied is False and result.explanation=='real conflict'
    assert normalized==['redundant_final_brace']
    for invalid in (text+'}}',text+'} {}',text[:-1],'{"satisfied":true}}'):
        with pytest.raises(ValueError):assistance.structured_response(invalid,Assessment)


def test_thinking_fallback_is_one_distinct_bounded_request_and_restores_mode(project,monkeypatch):
    enable();seen=[]
    def remote(self,prompt,schema):
        seen.append(self.thinking_override)
        if self.thinking_override:raise assistance.ModelOutputTruncated('reasoning exhausted output')
        return Assessment(satisfied=False,explanation='Real conflict remains')
    monkeypatch.setattr(assistance.BudgetedModel,'remote',remote)
    model=assistance.BudgetedModel(project['id'],Local(),'plan review',prefer_cloud=True,thinking_override=True,bounded_thinking_fallback=True)
    assert not model.generate('audit',Assessment).satisfied
    assert seen==[True,False] and model.thinking_override is True


def test_thinking_fallback_never_loops_after_second_truncation(project,monkeypatch):
    enable();seen=[]
    def remote(self,*a,**k):
        seen.append(self.thinking_override)
        raise assistance.ModelOutputTruncated('incomplete')
    monkeypatch.setattr(assistance.BudgetedModel,'remote',remote)
    model=assistance.BudgetedModel(project['id'],Local(),'plan review',prefer_cloud=True,thinking_override=True,bounded_thinking_fallback=True)
    with pytest.raises(assistance.ModelOutputTruncated):model.generate('audit',Assessment)
    assert seen==[True,False] and model.thinking_override is True
    seen.clear();model.bounded_thinking_fallback=False
    with pytest.raises(assistance.ModelOutputTruncated):model.generate('audit',Assessment)
    assert seen==[True]

def test_complete_manifest_length_revalidation_does_not_rebill(project, monkeypatch):
    from study.codegen import CompactPythonBundle, PythonBundle
    from pydantic import ValidationError
    enable()
    payload={'files':[{'path':'main.py','contract':'complete interface '+('x'*260)}], 'checks':[{'path':'check.py','contract':'verify'}], 'expected_files':[], 'explanation':'bounded'}
    calls=[]
    def send(rid):
        calls.append(rid)
        text=json.dumps(payload)
        db.execute("UPDATE cloud_requests SET state='done',response=?,actual=.001 WHERE id=?",(text,rid))
        return {'text':text,'complete':True,'cost_usd':.001,'provider':'deepseek'}
    monkeypatch.setattr(cloud,'send',send)
    model=assistance.BudgetedModel(project['id'],Local(),'manifest',prefer_cloud=True)
    with pytest.raises(ValidationError):model.generate('same prompt',CompactPythonBundle)
    original=model.last_request_id
    value=model.revalidate_last_response(PythonBundle)
    assert value.files[0].contract==payload['files'][0]['contract']
    assert len(calls)==1 and model.last_request_id==original
    assert len(db.query("SELECT id FROM cloud_requests"))==1
    # A restart reparses the same paid wire schema and still cannot rebill it.
    resumed=assistance.BudgetedModel(project['id'],Local(),'manifest',prefer_cloud=True)
    with pytest.raises(ValidationError):resumed.generate('same prompt',CompactPythonBundle)
    assert resumed.revalidate_last_response(PythonBundle)==value and len(calls)==1
    # Broad parsing still rejects malformed paths and retains the 4000-char cap.
    payload['files'][0]['contract']='x'*4001
    row=db.one("SELECT id,result FROM jobs WHERE kind='cloud'")
    result=json.loads(row['result']);result['text']=json.dumps(payload)
    db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result),row['id']))
    with pytest.raises(ValidationError):resumed.revalidate_last_response(PythonBundle)
    result['complete']=False
    db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result),row['id']))
    with pytest.raises(ModelOutputTruncated):resumed.revalidate_last_response(PythonBundle)
    assert len(calls)==1
