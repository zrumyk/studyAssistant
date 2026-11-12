import json
import pytest
from study import preflight,db,config
from study.schemas import Action,FileWrite


def setup(project,plan,monkeypatch,review):
    db.execute('UPDATE projects SET plan=? WHERE id=?',(json.dumps(plan),project['id']))
    monkeypatch.setattr(preflight,'task_passages',lambda pid:'Use 500 to 1000 images.')
    monkeypatch.setattr(config,'settings',lambda:{'cloud_consent_mode':'budgeted'})
    calls=[]
    class Model:
        def __init__(self,*a,**kw):pass
        def generate(self,prompt,schema):
            calls.append(prompt)
            if schema is preflight.PreflightAdjudication:
                return schema(decisions=[{
                    'conflict_index':index,'disposition':'confirmed','reason':issue['explanation'],
                    'required_behavior':issue['requirement_quote'],'actual_behavior':issue['code_quote'],
                    'evidence':[{'path':issue['path'],'quote':issue['code_quote']}],
                } for index,issue in enumerate(review['conflicts'])])
            return schema.model_validate(review)
    monkeypatch.setattr(preflight,'BudgetedModel',Model)
    action=Action(files=[FileWrite(path='main.py',content='count = 3264\n')],checks=[],expected_files=[],entrypoint='main.py',explanation='',requires_user=False,manual_instructions='',url='')
    spec={**plan['steps'][0],'timeout_seconds':3600}
    return spec,action,calls


def test_evidenced_conflict_blocks_before_execution_and_is_cached(project,plan,monkeypatch):
    review={'conflicts':[{'requirement_quote':'Use 500 to 1000 images.','path':'main.py','code_quote':'count = 3264','explanation':'The code selects too many images.'}]}
    spec,action,calls=setup(project,plan,monkeypatch,review)
    for _ in range(2):
        with pytest.raises(ValueError,match='preflight'):preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert len(calls)==2
    action.files[0].content='count = 800\n'
    with pytest.raises(ValueError,match='дослівних'):preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert len(calls)==3 # Changed code is reviewed again; the old result is not reused.


@pytest.mark.parametrize('field,value',[('requirement_quote','Imaginary mandatory ImageNet'),('path','other.py'),('code_quote','not in actual code')])
def test_invented_evidence_never_passes_validation(project,plan,monkeypatch,field,value):
    issue={'requirement_quote':'Use 500 to 1000 images.','path':'main.py','code_quote':'count = 3264','explanation':'Conflict'};issue[field]=value
    spec,action,calls=setup(project,plan,monkeypatch,{'conflicts':[issue]})
    with pytest.raises(ValueError,match='дослівних'):preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert not [p for p in (config.project_dir(project['id'])/'codegen').rglob('preflight-*.json') if p.name!='preflight-pending.json']


def test_short_actions_are_not_billed_and_clear_review_does_not_claim_success(project,plan,monkeypatch):
    spec,action,calls=setup(project,plan,monkeypatch,{'conflicts':[]})
    preflight.check_expensive_action(project['id'],'s1',{**spec,'timeout_seconds':30},action)
    assert not calls
    preflight.check_expensive_action(project['id'],'s1',spec,action)
    preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert len(calls)==1
    assert db.project(project['id'])['status']!='completed'


def test_interrupted_review_reuses_only_same_written_action(project,plan,monkeypatch):
    from study.codegen import save_candidate
    from study.schemas import ActionCheckpoint
    spec,action,calls=setup(project,plan,monkeypatch,{'conflicts':[]})
    pid=project['id'];revision=db.project(pid)['revision'];sid='s1'
    save_candidate(pid,sid,revision,'cloud',action)
    folder=config.project_dir(pid)/'codegen'/sid
    cp=folder/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(cp.read_text('utf-8'));saved.written_to_workspace=True
    cp.write_text(saved.model_dump_json(),'utf-8')
    workspace=config.project_dir(pid)/'workspace';workspace.mkdir()
    (workspace/'main.py').write_text(action.files[0].content,'utf-8')
    monkeypatch.setattr(preflight.BudgetedModel,'generate',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('transport unavailable')))
    with pytest.raises(RuntimeError):preflight.check_expensive_action(pid,sid,spec,action)
    assert preflight.pending_action(pid,sid,revision,workspace)==action
    assert preflight.pending_action(pid,sid,revision+1,workspace) is None
    (workspace/'main.py').write_text('count=800\n','utf-8')
    assert preflight.pending_action(pid,sid,revision,workspace) is None


def test_preflight_sees_recursive_local_imports_and_invalidates_cache(project,plan,monkeypatch):
    spec,action,calls=setup(project,plan,monkeypatch,{'conflicts':[]})
    root=config.project_dir(project['id'])/'workspace';root.mkdir()
    (root/'common.py').write_text('from validation import validate_split\ndef load():\n    return validate_split()\n','utf-8')
    (root/'validation.py').write_text('def validate_split():\n    assert 54000 + 6000 == 60000\n','utf-8')
    (root/'unrelated.py').write_text('UNRELATED_PRIVATE_DATA','utf-8')
    action.files[0].content='from common import load\nload()\n'
    preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert 'assert 54000 + 6000 == 60000' in calls[0]
    assert 'UNRELATED_PRIVATE_DATA' not in calls[0]
    (root/'validation.py').write_text('def validate_split():\n    assert 50000 + 10000 == 60000\n','utf-8')
    preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert len(calls)==2 and 'assert 50000 + 10000 == 60000' in calls[1]


def test_proposed_imported_module_overrides_unexecuted_workspace_copy(project):
    root=config.project_dir(project['id'])/'workspace';root.mkdir()
    (root/'common.py').write_text('STALE_COPY','utf-8')
    action=Action(files=[{'path':'main.py','content':'import common\n'}, {'path':'common.py','content':'PROPOSED_COPY'}],explanation='')
    assert preflight.review_sources(project['id'],action)['common.py']=='PROPOSED_COPY'


def decision(index,disposition,reason,evidence=()):
    return {'conflict_index':index,'disposition':disposition,'reason':reason,
            'required_behavior':'Use the agreed data and comparison format.',
            'actual_behavior':reason,'evidence':list(evidence)}


def adjudication_model(monkeypatch,review,decisions,calls):
    def generate(self,prompt,schema):
        calls.append((schema.__name__,prompt))
        return schema.model_validate({'decisions':decisions} if schema is preflight.PreflightAdjudication else review)
    monkeypatch.setattr(preflight.BudgetedModel,'generate',generate)


def test_valid_split_and_uncertainty_are_advisories_but_json_mismatch_still_blocks(project,plan,monkeypatch):
    requirement='60000 examples; reserve 10% for validation. Save a comparison table and verify it.'
    issues=[
        {'requirement_quote':requirement,'path':'main.py','code_quote':'TRAIN, VAL = 54000, 6000',
         'explanation':'54000 differs from 60000; not a proven contradiction.'},
        {'requirement_quote':requirement,'path':'check_metrics.py','code_quote':'row = table.get(tag)',
         'explanation':'The producer writes rows, but the checker reads top-level tags.'},
        {'requirement_quote':requirement,'path':'main.py','code_quote':'TRAIN, VAL = 54000, 6000',
         'explanation':'Provenance is not established by these constants.'},
    ]
    review={'conflicts':issues}
    spec,action,_=setup(project,plan,monkeypatch,review)
    monkeypatch.setattr(preflight,'task_passages',lambda pid:requirement)
    action.files[0].content='TRAIN, VAL = 54000, 6000\n'
    action.files.append(FileWrite(path='report.py',content='def table(rows):\n    return {"rows": rows}\n'))
    action.checks.append(FileWrite(path='check_metrics.py',content='row = table.get(tag)\nassert isinstance(row, dict)\n'))
    decisions=[decision(0,'not_a_conflict','54000 + 6000 = 60000; validation is 10%.'),
               decision(1,'confirmed','The consumer cannot find A/B in the producer rows container.',[
                   {'path':'report.py','quote':'return {"rows": rows}'},
                   {'path':'check_metrics.py','quote':'row = table.get(tag)'}]),
               decision(2,'needs_verification','Source indices need a separate audit.')]
    calls=[];adjudication_model(monkeypatch,review,decisions,calls)
    folder=config.project_dir(project['id'])/'codegen'/'s1'
    for _ in range(2):
        with pytest.raises(ValueError) as error:
            preflight.check_expensive_action(project['id'],'s1',spec,action)
        effective=json.loads(str(error.value))
        assert [item['path'] for item in effective['conflicts']]==['check_metrics.py']
        assert effective['conflicts'][0]['explanation']==decisions[1]['reason']
    assert len(calls)==2 # Original paid review and adjudication each reused.
    original=json.loads(next(folder.glob('preflight-*.json')).read_text('utf-8'))
    assert len(original['conflicts'])==3 # Preserve even the wrong initial claims.
    assert 'return {\\"rows\\": rows}' in calls[1][1]
    assert not (folder/'preflight-pending.json').exists()


def test_refuted_claim_does_not_change_status_or_claim_runtime_success(project,plan,monkeypatch):
    review={'conflicts':[{'requirement_quote':'Use 500 to 1000 images.','path':'main.py',
                         'code_quote':'count = 3264','explanation':'Potential concern, not proven.'}]}
    spec,action,_=setup(project,plan,monkeypatch,review)
    calls=[]
    adjudication_model(monkeypatch,review,[decision(0,'needs_verification','The quoted value may be the archive size, not the selected subset.')],calls)
    before=db.project(project['id'])['status']
    effective=preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert effective.conflicts==[] and effective.warnings
    assert db.project(project['id'])['status']==before
    assert db.query("SELECT id FROM events WHERE kind='preflight_adjudicated'")


@pytest.mark.parametrize('indices',[[0,0],[],[1]])
def test_adjudication_must_cover_each_finding_once(project,plan,monkeypatch,indices):
    review={'conflicts':[{'requirement_quote':'Use 500 to 1000 images.','path':'main.py','code_quote':'count = 3264','explanation':'Conflict'}]}
    spec,action,_=setup(project,plan,monkeypatch,review)
    adjudication_model(monkeypatch,review,[decision(i,'not_a_conflict','Reason') for i in indices],[])
    with pytest.raises(ValueError):preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert not list((config.project_dir(project['id'])/'codegen'/'s1').glob('adjudication-*.json'))


def test_adjudication_cannot_confirm_without_literal_support(project,plan,monkeypatch):
    review={'conflicts':[{'requirement_quote':'Use 500 to 1000 images.','path':'main.py','code_quote':'count = 3264','explanation':'Conflict'}]}
    spec,action,_=setup(project,plan,monkeypatch,review)
    adjudication_model(monkeypatch,review,[decision(0,'confirmed','Mismatch',[{'path':'main.py','quote':'IMAGINARY'}])],[])
    with pytest.raises(ValueError,match='дослівних'):preflight.check_expensive_action(project['id'],'s1',spec,action)
    with pytest.raises(ValueError):preflight.ConflictDecision.model_validate(decision(0,'confirmed','Mismatch'))


def test_cached_review_evidence_is_revalidated(project,plan,monkeypatch):
    spec,action,_=setup(project,plan,monkeypatch,{'conflicts':[]})
    preflight.check_expensive_action(project['id'],'s1',spec,action)
    path=next((config.project_dir(project['id'])/'codegen'/'s1').glob('preflight-*.json'))
    path.write_text(json.dumps({'conflicts':[{'requirement_quote':'Invented requirement','path':'main.py','code_quote':'count = 3264','explanation':'Conflict'}]}),'utf-8')
    with pytest.raises(ValueError,match='дослівних'):preflight.check_expensive_action(project['id'],'s1',spec,action)


def test_adjudication_failure_keeps_paid_review_and_pending_action(project,plan,monkeypatch):
    from study.codegen import save_candidate,checkpoint_write
    from study.schemas import ActionCheckpoint
    review={'conflicts':[{'requirement_quote':'Use 500 to 1000 images.','path':'main.py','code_quote':'count = 3264','explanation':'Conflict'}]}
    spec,action,_=setup(project,plan,monkeypatch,review)
    pid=project['id'];sid='s1';revision=db.project(pid)['revision']
    save_candidate(pid,sid,revision,'cloud',action)
    folder=config.project_dir(pid)/'codegen'/sid
    checkpoint=folder/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'));saved.written_to_workspace=True
    checkpoint_write(checkpoint,saved)
    workspace=config.project_dir(pid)/'workspace';workspace.mkdir()
    (workspace/'main.py').write_text(action.files[0].content,'utf-8')
    calls=[]
    def generate(self,prompt,schema):
        calls.append(schema.__name__)
        if schema is preflight.PreflightAdjudication:raise RuntimeError('Budget exhausted')
        return schema.model_validate(review)
    monkeypatch.setattr(preflight.BudgetedModel,'generate',generate)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='Budget'):preflight.check_expensive_action(pid,sid,spec,action)
    assert calls==['PreflightReview','PreflightAdjudication','PreflightAdjudication']
    assert preflight.pending_action(pid,sid,revision,workspace)==action


def test_changed_producer_invalidates_both_review_and_adjudication(project,plan,monkeypatch):
    review={'conflicts':[{'requirement_quote':'Use 500 to 1000 images.','path':'main.py','code_quote':'count = 3264','explanation':'Conflict'}]}
    spec,action,_=setup(project,plan,monkeypatch,review)
    action.files.append(FileWrite(path='producer.py',content='FORMAT = "rows"\n'))
    calls=[];adjudication_model(monkeypatch,review,[decision(0,'needs_verification','Need runtime evidence.')],calls)
    preflight.check_expensive_action(project['id'],'s1',spec,action)
    action.files[1].content='FORMAT = "tags"\n'
    preflight.check_expensive_action(project['id'],'s1',spec,action)
    assert len(calls)==4


def written_candidate(project,plan,monkeypatch):
    from study import engine
    from study.codegen import save_candidate,checkpoint_write
    from study.schemas import ActionCheckpoint
    plan['steps'][0]['timeout_seconds']=3600
    engine.save_plan(project['id'],plan)
    p=db.project(project['id']);step=p['steps'][0]
    root=config.project_dir(p['id'])/'workspace';root.mkdir()
    action=Action(files=[{'path':'main.py','content':'value=4\n'}],checks=[{'path':'check_result.py','content':'value=4\nassert value==4\n'}],entrypoint='main.py',explanation='')
    for file in action.files+action.checks:(root/file.path).write_text(file.content,'utf-8')
    save_candidate(p['id'],step['id'],p['revision'],'cloud',action)
    path=config.project_dir(p['id'])/'codegen'/step['id']/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(path.read_text('utf-8'));saved.written_to_workspace=True
    checkpoint_write(path,saved)
    monkeypatch.setattr(config,'settings',lambda:config.DEFAULTS|{'cloud_consent_mode':'budgeted'})
    return p,step,root,action


def test_engine_reuses_source_when_old_preflight_claims_are_refuted(project,plan,monkeypatch):
    from study import engine
    p,step,root,action=written_candidate(project,plan,monkeypatch)
    calls=[]
    def check(*args):calls.append(args);return preflight.PreflightReview(warnings=['Refuted'])
    monkeypatch.setattr(preflight,'check_expensive_action',check)
    monkeypatch.setattr(engine.OllamaProvider,'generate',lambda *a,**k:pytest.fail('No generation for a refuted claim'))
    assert engine.prepare_action(p['id'],step,'{"preflight": true, "conflicts": [{"explanation": "obsolete"}]}')==action
    assert len(calls)==1


def test_engine_repairs_only_current_confirmed_claims(project,plan,monkeypatch):
    from study import engine
    p,step,root,action=written_candidate(project,plan,monkeypatch)
    effective={'preflight':True,'conflicts':[{'path':'check_result.py','explanation':'Actual format mismatch'}],
               'evidence':[{'path':'producer.py','quote':'rows'}]}
    def check(*args):raise ValueError(json.dumps(effective))
    monkeypatch.setattr(preflight,'check_expensive_action',check)
    captured=[]
    def repair_sources(*args):
        captured.append(json.loads(args[-1]))
        raise RuntimeError('Captured repair boundary')
    monkeypatch.setattr(engine,'repair_sources',repair_sources)
    with pytest.raises(RuntimeError,match='Captured repair boundary'):
        engine.prepare_action(p['id'],step,'{"preflight": true, "conflicts": [{"explanation": "FALSE 54000"}]}')
    assert captured==[effective]


def test_changed_workspace_cannot_reuse_old_preflight_repair(project,plan,monkeypatch):
    p,step,root,action=written_candidate(project,plan,monkeypatch)
    (root/'main.py').write_text('value=8\n','utf-8')
    monkeypatch.setattr(preflight,'check_expensive_action',lambda *a:pytest.fail('Do not adjudicate stale source'))
    previous='{"preflight": true}'
    assert preflight.recheck_before_repair(p['id'],step['id'],step['spec'],p['revision'],root,previous)==(None,previous)


def test_compaction_preserves_literals_and_executable_source():
    source='"""Довгий опис модуля"""\n# comment\nVALUE = "# not a comment"\ndef f():\n    """Опис функції"""\n    return VALUE # explanation\n'
    compact=preflight.compact_sources({'a.py':source})['a.py']
    assert 'Довгий опис' not in compact and 'Опис функції' not in compact
    assert 'VALUE = "# not a comment"' in compact and 'return VALUE' in compact
    assert len(compact)<len(source)
    # A docstring-only function must not turn into invalid or empty code.
    only='def f():\n    """Only body"""\n'
    assert preflight.compact_sources({'a.py':only})['a.py']==only


def test_budget_rejection_compacts_context_but_not_requirements(project):
    sources={'a.py':'# verbose comments\ncount = 3264\n'}
    prompt=json.dumps({'requirements':'Exact mandatory requirement','sources':sources},ensure_ascii=False)
    calls=[]
    class Model:
        def generate(self,prompt,schema):
            calls.append(prompt)
            if len(calls)==1:raise ValueError('Запит поза межами ліміту: вхідні дані треба розділити на частини')
            assert 'Exact mandatory requirement' in prompt and 'count = 3264' in prompt
            assert 'verbose comments' not in prompt
            return schema(conflicts=[])
    assert preflight.generate_bounded(Model(),prompt,preflight.PreflightReview,project['id'],'s1',sources).conflicts==[]
    assert len(calls)==2


def test_finding_context_includes_recursive_local_dependencies():
    sources={'check.py':'import producer\nassert producer.value==4\n',
             'producer.py':'from validation import value\n',
             'validation.py':'value=4\n','unrelated.py':'OTHER=9\n'}
    review=preflight.PreflightReview(conflicts=[{'path':'check.py','requirement_quote':'value4',
        'code_quote':'assert producer.value==4','explanation':'Potential conflict'}])
    assert set(preflight.finding_sources(sources,review))=={'check.py','producer.py','validation.py'}


def test_adjudication_budget_fallback_keeps_requirement_and_marks_omissions(project):
    sources={'check.py':'# explanation\nimport producer\nassert producer.value==4\n',
             'producer.py':'value=4\n','other.py':'UNRELATED_SOURCE=9\n'}
    review=preflight.PreflightReview(conflicts=[{'path':'check.py','requirement_quote':'value4',
        'code_quote':'assert producer.value==4','explanation':'Potential conflict'}])
    prompt=json.dumps({'requirements':'value4','sources':sources,'review':review.model_dump()},ensure_ascii=False)
    calls=[]
    class Model:
        def generate(self,prompt,schema):
            calls.append(prompt)
            if len(calls)<3:raise ValueError('Запит поза межами ліміту: недостатньо бюджету для цілісної відповіді')
            assert 'value4' in prompt and 'value=4' in prompt and 'UNRELATED_SOURCE' not in prompt
            assert 'needs_verification' in prompt and 'other.py' in prompt
            return schema(decisions=[decision(0,'not_a_conflict','Code matches')])
    verdict=preflight.generate_bounded(Model(),prompt,preflight.PreflightAdjudication,project['id'],'s1',sources,review)
    assert verdict.decisions[0].disposition=='not_a_conflict'
