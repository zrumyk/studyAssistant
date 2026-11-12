import json

import pytest

from study import config, db, assistance, worker
from study.plan_patches import PlanDraft, PlanPatch, PlanEdit, draft_hash, apply_plan_patch, save_plan_patch
from study.planning_sources import planning_sources, save_receipt
from study.planning_recovery import candidate, enqueue, execute


@pytest.fixture
def failed_plan(project, plan):
    config.save_settings({'cloud_consent_mode':'budgeted','cloud_provider':'deepseek'})
    source = db.enqueue(project['id'], 'analyse')
    base = PlanDraft.model_validate({k:v for k,v in plan.items() if k!='sources'})
    from study.schemas import Step
    base.steps.append(Step(title='Report',kind='report',instruction='Render',expected='DOCX/PDF'))
    change = PlanPatch(base_sha256=draft_hash(base),requires_replan=False,explanation='Candidate',edits=[
        PlanEdit(op='replace',path='/warnings',before_json='[]',after_json='["Needs review"]')])
    result = apply_plan_patch(base,change)
    text='CONTROLLER_PATCH_PROMPT'
    import hashlib
    _,_,coverage=planning_sources(project['id'],project['task'])
    save_receipt(project['id'],{**coverage,'phase':'patch','prompt_characters':len(text),
                               'prompt_sha256':hashlib.sha256(text.encode()).hexdigest()})
    rid=db.uid();snapshot={**config.settings(),'project_revision':project['revision']}
    with db.connect() as c:
        c.execute('INSERT INTO cloud_requests(id,project_id,prompt,purpose,state,response,config,created) VALUES(?,?,?,?,?,?,?,?)',
                  (rid,project['id'],text+' suffix','auto/test/patch','done',change.model_dump_json(),json.dumps(snapshot),db.now()))
    jid=db.enqueue(project['id'],'cloud',{'id':rid})
    db.execute("UPDATE jobs SET status='done',result=? WHERE id=?",(json.dumps({'complete':True,'text':change.model_dump_json()}),jid))
    db.event(project['id'],'automatic_assistance',{'request_id':rid,'complete':True})
    checkpoint=save_plan_patch(project['id'],base,change,result,rid)
    db.execute("UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",('Semantic requirement needs clarification',db.now(),source))
    db.execute("UPDATE projects SET status='error' WHERE id=?",(project['id'],))
    return {'pid':project['id'],'source':source,'sha':checkpoint.stem,'path':checkpoint,'receipt':rid,'cloud_job':jid}


def test_authenticated_queue_is_atomic_idempotent_and_does_not_approve(client,failed_plan):
    x=failed_plan;url=f"/api/projects/{x['pid']}/recover-planning"
    data={'source_job_id':x['source'],'checkpoint_sha256':x['sha']}
    assert client.post(url,json=data,headers={'x-study-token':''}).status_code==403
    one=client.post(url,json=data);assert one.status_code==200,one.text
    two=client.post(url,json=data);assert two.status_code==200
    assert one.json()['job_id']==two.json()['job_id'] and two.json()['existing']
    assert len(db.query('SELECT id FROM planning_recoveries'))==1
    p=db.project(x['pid']);assert p['status']=='analysis_running' and not p['steps'] and p['approved_hash'] is None


@pytest.mark.parametrize('mutation', ['revision','approved','steps','receipt_unknown','receipt_truncated','checkpoint_bytes','source_chunks','permission','not_latest'])
def test_invalid_or_stale_recovery_is_rejected_before_queue(failed_plan,mutation):
    x=failed_plan;pid=x['pid']
    if mutation=='revision':db.execute('UPDATE projects SET revision=revision+1 WHERE id=?',(pid,))
    elif mutation=='approved':db.execute("UPDATE projects SET approved_hash='approved' WHERE id=?",(pid,))
    elif mutation=='steps':db.execute('INSERT INTO steps(id,project_id,position,spec,status) VALUES(?,?,?,?,?)',(db.uid(),pid,0,'{}','pending'))
    elif mutation=='receipt_unknown':db.execute("UPDATE cloud_requests SET state='uncertain' WHERE id=?",(x['receipt'],))
    elif mutation=='receipt_truncated':db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps({'complete':False,'text':'{}'}),x['cloud_job']))
    elif mutation=='checkpoint_bytes':x['path'].write_text('{}',encoding='utf-8')
    elif mutation=='source_chunks':
        from study import documents
        path=config.DATA/'new.txt';path.write_text('New task',encoding='utf-8');documents.import_file(path,'task','Приклад',pid)
    elif mutation=='permission':config.save_settings({'cloud_consent_mode':'per_request'})
    elif mutation=='not_latest':
        jid=db.enqueue(pid,'analyse');db.execute("UPDATE jobs SET status='failed',error='new error' WHERE id=?",(jid,))
    with pytest.raises((ValueError,RuntimeError)):
        enqueue(pid,x['source'],x['sha'])
    assert not db.query('SELECT id FROM planning_recoveries')


def test_foreign_checkpoint_receipt_is_rejected(client,failed_plan):
    x=failed_plan
    other=client.post('/api/projects',json={'title':'Other','task':'Other','subject':'Other','variant':''}).json()
    target=config.project_dir(other['id'])/'planning-patches'/x['path'].name;target.parent.mkdir();target.write_bytes(x['path'].read_bytes())
    with pytest.raises(ValueError):candidate(other['id'],x['source'],x['sha'])


@pytest.mark.parametrize('mutation',['pause','input','settings','revision'])
def test_queued_context_changes_stop_before_model(failed_plan,monkeypatch,mutation):
    x=failed_plan;queued=enqueue(x['pid'],x['source'],x['sha']);job=db.one('SELECT * FROM jobs WHERE id=?',(queued['job_id'],))
    rid=json.loads(job['payload'])['recovery_id'];db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
    if mutation=='pause':db.execute("UPDATE projects SET status='paused' WHERE id=?",(x['pid'],))
    elif mutation=='input':
        path=config.project_dir(x['pid'])/'workspace/inputs/new.txt';path.parent.mkdir(parents=True,exist_ok=True);path.write_text('changed')
    elif mutation=='settings':config.save_settings({'group':'Changed'})
    else:db.execute('UPDATE projects SET revision=revision+1 WHERE id=?',(x['pid'],))
    monkeypatch.setattr(assistance.BudgetedModel,'generate',lambda *a,**k:pytest.fail('No model after stale intent'))
    with pytest.raises((RuntimeError,ValueError)):execute(x['pid'],rid)


def test_successful_recovery_starts_with_patch_and_still_requires_approval(failed_plan,monkeypatch,patch_reply):
    from study.schemas import Assessment
    x=failed_plan;q=enqueue(x['pid'],x['source'],x['sha']);job=db.one('SELECT * FROM jobs WHERE id=?',(q['job_id'],))
    db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
    calls=[]
    def generate(self,prompt,schema):
        calls.append(schema);assert schema is PlanPatch
        return patch_reply(prompt,path='/warnings',value=['Reviewed candidate'])
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    monkeypatch.setattr(assistance.BudgetedModel,'remote_with_fallback',lambda *a,**k:Assessment(satisfied=True,explanation='Controlled review'))
    worker.dispatch(job)
    p=db.project(x['pid'])
    assert calls==[PlanPatch] and p['status']=='awaiting_approval' and p['approved_hash'] is None
    assert all(step['status']=='pending' and step['attempts']==0 for step in p['steps'])


def test_restart_preserves_queued_intent_and_interrupts_running_without_requeue(failed_plan,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('study.runtime.subprocess.run',lambda *a,**k:SimpleNamespace(stdout=''))
    x=failed_plan;q=enqueue(x['pid'],x['source'],x['sha']);worker.recover()
    assert db.one('SELECT status FROM jobs WHERE id=?',(q['job_id'],))['status']=='queued'
    assert db.project(x['pid'])['status']=='analysis_running'
    db.execute("UPDATE jobs SET status='running' WHERE id=?",(q['job_id'],));worker.recover()
    assert db.one('SELECT status FROM jobs WHERE id=?',(q['job_id'],))['status']=='interrupted'
    assert db.project(x['pid'])['status']=='paused'
    assert enqueue(x['pid'],x['source'],x['sha'])=={'job_id':q['job_id'],'existing':True}


def test_cap_is_not_reset_by_failed_jobs(failed_plan):
    x=failed_plan
    # Independent historical intents consume the cap for this exact revision.
    with db.connect() as c:
        for i in range(2):
            c.execute('INSERT INTO planning_recoveries VALUES(?,?,?,?,?,?,?,?)',
                      (db.uid(),x['pid'],0,'old'+str(i),'0'*63+str(i),'old',db.uid(),db.now()))
    with pytest.raises(ValueError,match='Ліміт2'):
        enqueue(x['pid'],x['source'],x['sha'])


def test_pause_cancels_queued_recovery_and_duplicate_stays_same_job(client,failed_plan):
    x=failed_plan;q=enqueue(x['pid'],x['source'],x['sha'])
    assert client.post(f"/api/projects/{x['pid']}/pause").status_code==200
    assert db.one('SELECT status FROM jobs WHERE id=?',(q['job_id'],))['status']=='cancelled'
    assert enqueue(x['pid'],x['source'],x['sha'])['job_id']==q['job_id']
    assert len(db.query("SELECT id FROM jobs WHERE kind='recover_plan'"))==1


def test_two_simultaneous_triggers_create_one_intent(failed_plan):
    from concurrent.futures import ThreadPoolExecutor
    x=failed_plan
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:enqueue(x['pid'],x['source'],x['sha']),range(2)))
    assert results[0]['job_id']==results[1]['job_id']
    assert sorted(r['existing'] for r in results)==[False,True]


def test_mid_generation_context_change_cannot_save_plan(failed_plan,monkeypatch,patch_reply):
    x=failed_plan;q=enqueue(x['pid'],x['source'],x['sha']);job=db.one('SELECT * FROM jobs WHERE id=?',(q['job_id'],))
    db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
    def generate(self,prompt,schema):
        config.save_settings({'group':'Changed during response'})
        return patch_reply(prompt,path='/warnings',value=['Response after source change'])
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    with pytest.raises(RuntimeError,match='context'):
        worker.dispatch(job)
    assert not db.project(x['pid'])['plan'] and not db.project(x['pid'])['steps']


@pytest.mark.parametrize('error',['Хмарна відповідь незавершена; repeat forbidden','Попередній хмарний запит має непідтверджений результат'])
def test_unknown_failure_is_not_recovered_from_older_complete_checkpoint(failed_plan,error):
    x=failed_plan;db.execute('UPDATE jobs SET error=? WHERE id=?',(error,x['source']))
    with pytest.raises(ValueError,match='Unknown/truncated'):
        enqueue(x['pid'],x['source'],x['sha'])


def test_legacy_crlf_coverage_address_is_verified_without_altering_it(failed_plan):
    x=failed_plan;folder=config.project_dir(x['pid'])/'planning-coverage'
    for path in folder.glob('*.json'):
        path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
    assert len(candidate(x['pid'],x['source'],x['sha'])[0].steps)==2


def test_physical_source_tamper_is_detected(failed_plan):
    from study import documents
    from study.planning_recovery import source_state
    x=failed_plan;source=config.DATA/'source.txt';source.write_text('original',encoding='utf-8')
    did=documents.import_file(source,'task','Приклад',x['pid'])
    target=db.one('SELECT path FROM documents WHERE id=?',(did,))['path']
    from pathlib import Path
    Path(target).write_text('tampered',encoding='utf-8')
    with pytest.raises(ValueError,match='Байти'):
        source_state(x['pid'])


def record_reply(pid, text, patch):
    """Synthetic complete receipts in the isolated test DB only."""
    rid=db.uid();snapshot={**config.settings(),'project_revision':db.project(pid)['revision']}
    with db.connect() as c:
        c.execute('INSERT INTO cloud_requests(id,project_id,prompt,purpose,state,response,config,created) VALUES(?,?,?,?,?,?,?,?)',
                  (rid,pid,text,'auto/test/patch','done',patch.model_dump_json(),json.dumps(snapshot),db.now()))
    jid=db.enqueue(pid,'cloud',{'id':rid})
    db.execute("UPDATE jobs SET status='done',result=? WHERE id=?",(json.dumps({'complete':True,'text':patch.model_dump_json()}),jid))
    db.event(pid,'automatic_assistance',{'request_id':rid,'complete':True})
    return rid,jid


def finish_terminal(x):
    q=enqueue(x['pid'],x['source'],x['sha']);job=q['job_id']
    db.execute("UPDATE jobs SET status='running' WHERE id=?",(job,))
    draft,_,_=candidate(x['pid'],x['source'],x['sha'])
    patch=PlanPatch(base_sha256=draft_hash(draft),requires_replan=True,explanation='Fixture terminal structural concern',edits=[])
    rid,cjid=record_reply(x['pid'],'OLDER_REPAIR_PROTOCOL_WITHOUT_OWNERSHIP_WITNESSES',patch)
    db.execute("UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",('Потрібне окреме перепланування: '+patch.explanation,db.now(),job))
    db.execute("UPDATE projects SET status='error' WHERE id=?",(x['pid'],))
    return {**x,'terminal':job,'terminal_receipt':rid,'terminal_cloud_job':cjid,'draft':draft}


@pytest.fixture
def terminal_graph(project):
    import hashlib
    from study.schemas import Step
    config.save_settings({'cloud_consent_mode':'budgeted','cloud_provider':'deepseek'})
    pid=project['id'];source=db.enqueue(pid,'analyse')
    base=PlanDraft(goal='Fixture',requirements=['Sources retained'],missing=[],conflicts=[],report_sections=[],steps=[
        Step(title='Inventory',kind='code',instruction='Inspect fixture',expected='File',artifact_outputs=['work/source.txt']),
        Step(title='Implementation',kind='code',instruction='Create fixture',expected='File',
             artifact_inputs=['work/source.txt'],artifact_outputs=['work/result.txt']),
        Step(title='Report',kind='report',instruction='Render',expected='DOCX/PDF',artifact_inputs=['work/source.txt'])])
    patch=PlanPatch(base_sha256=draft_hash(base),requires_replan=False,explanation='Synthetic invalid graph',edits=[
        PlanEdit(op='replace',path='/steps/1/artifact_outputs',before_json='["work/result.txt"]',after_json='["work/source.txt"]')])
    text='ROOT_COMPLETE_PATCH'
    _,_,coverage=planning_sources(pid,project['task'])
    save_receipt(pid,{**coverage,'phase':'patch','prompt_characters':len(text),'prompt_sha256':hashlib.sha256(text.encode()).hexdigest()})
    rid,cjid=record_reply(pid,text+' suffix',patch)
    path=save_plan_patch(pid,base,patch,apply_plan_patch(base,patch),rid)
    db.execute("UPDATE jobs SET status='failed',error='Ownership failure',updated=? WHERE id=?",(db.now(),source))
    db.execute("UPDATE projects SET status='error' WHERE id=?",(pid,))
    return finish_terminal({'pid':pid,'source':source,'sha':path.stem,'path':path,'receipt':rid,'cloud_job':cjid})


def test_terminal_reconsideration_keeps_old_intent_and_consumes_second_slot(client,terminal_graph):
    x=terminal_graph;url=f"/api/projects/{x['pid']}/recover-planning"
    old=client.post(url,json={'source_job_id':x['source'],'checkpoint_sha256':x['sha']}).json()
    assert old['existing'] and old['job_id']==x['terminal']
    one=client.post(url,json={'source_job_id':x['terminal'],'checkpoint_sha256':x['sha']});assert one.status_code==200,one.text
    two=client.post(url,json={'source_job_id':x['terminal'],'checkpoint_sha256':x['sha']}).json()
    assert two['existing'] and one.json()['job_id']==two['job_id'] and two['job_id']!=old['job_id']
    assert len(db.query('SELECT id FROM planning_recoveries'))==2
    assert db.project(x['pid'])['approved_hash'] is None and not db.project(x['pid'])['steps']


@pytest.mark.parametrize('mutation',['same_policy','uncertain','truncated','false_signal','nonempty_edits','error_mismatch','revision','foreign_checkpoint'])
def test_terminal_reconsideration_rejects_unproven_or_unchanged_outcomes(terminal_graph,mutation):
    from study.artifact_repair import graph_policy_marker
    x=terminal_graph
    if mutation=='same_policy':db.execute('UPDATE cloud_requests SET prompt=? WHERE id=?',(graph_policy_marker(x['draft']),x['terminal_receipt']))
    elif mutation=='uncertain':db.execute("UPDATE cloud_requests SET state='uncertain' WHERE id=?",(x['terminal_receipt'],))
    elif mutation=='truncated':db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps({'complete':False,'text':'{}'}),x['terminal_cloud_job']))
    elif mutation in ('false_signal','nonempty_edits'):
        row=db.one('SELECT result FROM jobs WHERE id=?',(x['terminal_cloud_job'],));result=json.loads(row['result']);raw=json.loads(result['text'])
        if mutation=='false_signal':raw['requires_replan']=False
        else:raw['edits']=[{'op':'replace','path':'/warnings','before_json':'[]','after_json':'["Extra"]'}]
        result['text']=json.dumps(raw);db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result),x['terminal_cloud_job']))
    elif mutation=='error_mismatch':db.execute("UPDATE jobs SET error='Потрібне окреме перепланування: forged' WHERE id=?",(x['terminal'],))
    elif mutation=='revision':db.execute('UPDATE projects SET revision=revision+1 WHERE id=?',(x['pid'],))
    elif mutation=='foreign_checkpoint':x['sha']='f'*64
    with pytest.raises(ValueError):enqueue(x['pid'],x['terminal'],x['sha'])
    assert len(db.query('SELECT id FROM planning_recoveries'))==1


def test_terminal_without_locally_proven_graph_scope_is_not_retried(failed_plan):
    x=finish_terminal(failed_plan)
    with pytest.raises(ValueError,match='нової enforced graph policy'):
        enqueue(x['pid'],x['terminal'],x['sha'])


def test_second_recovery_runs_patch_only_and_saves_unapproved_plan(terminal_graph,monkeypatch):
    from study.schemas import Assessment
    x=terminal_graph;q=enqueue(x['pid'],x['terminal'],x['sha'])
    db.execute("UPDATE jobs SET status='running' WHERE id=?",(q['job_id'],));calls=[]
    def generate(self,prompt,schema):
        calls.append(schema);assert schema is PlanPatch and 'GraphRepairPolicy:' in prompt
        base=x['draft']
        return PlanPatch(base_sha256=draft_hash(base),requires_replan=False,explanation='Synthetic controlled two-field repair',edits=[
            PlanEdit(op='replace',path='/steps/0/artifact_outputs',before_json='["work/source.txt"]',after_json='["work/inventory.json"]'),
            PlanEdit(op='replace',path='/steps/1/artifact_inputs',before_json='["work/source.txt"]',after_json='["work/inventory.json"]')])
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    monkeypatch.setattr(assistance.BudgetedModel,'remote_with_fallback',lambda *a,**k:Assessment(satisfied=True,explanation='Controlled'))
    worker.dispatch(db.one('SELECT * FROM jobs WHERE id=?',(q['job_id'],)))
    p=db.project(x['pid']);assert calls==[PlanPatch] and p['status']=='awaiting_approval' and p['approved_hash'] is None
    assert all(s['attempts']==0 and s['status']=='pending' for s in p['steps'])


def test_second_slot_remains_bounded_after_pause_and_restart(client,terminal_graph,monkeypatch):
    from types import SimpleNamespace
    x=terminal_graph;q=enqueue(x['pid'],x['terminal'],x['sha'])
    assert client.post(f"/api/projects/{x['pid']}/pause").status_code==200
    monkeypatch.setattr('study.runtime.subprocess.run',lambda *a,**k:SimpleNamespace(stdout=''))
    worker.recover()
    assert enqueue(x['pid'],x['terminal'],x['sha'])['job_id']==q['job_id']
    assert db.one('SELECT status FROM jobs WHERE id=?',(q['job_id'],))['status']=='cancelled'
    assert len(db.query('SELECT id FROM planning_recoveries'))==2


def test_third_terminal_chain_cannot_create_new_operation(terminal_graph):
    x=terminal_graph
    second=finish_terminal({**x,'source':x['terminal']})
    with pytest.raises(ValueError,match='ancestry|Ліміт2'):
        enqueue(x['pid'],second['terminal'],x['sha'])
    assert len(db.query('SELECT id FROM planning_recoveries'))==2


def test_terminal_root_source_or_checkpoint_tamper_is_rejected(terminal_graph):
    x=terminal_graph;x['path'].write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='bytes/hash'):
        enqueue(x['pid'],x['terminal'],x['sha'])


def test_terminal_second_operation_context_change_stops_before_model(terminal_graph,monkeypatch):
    x=terminal_graph;q=enqueue(x['pid'],x['terminal'],x['sha'])
    job=db.one('SELECT * FROM jobs WHERE id=?',(q['job_id'],));db.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],))
    config.save_settings({'cloud_consent_mode':'per_request'})
    monkeypatch.setattr(assistance.BudgetedModel,'generate',lambda *a,**k:pytest.fail('No model after permission change'))
    with pytest.raises((ValueError,RuntimeError)):
        worker.dispatch(job)
    assert not db.project(x['pid'])['plan']
