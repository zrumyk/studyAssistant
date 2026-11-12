import json
import pytest
from study import engine, db, config
from study.schemas import Action


@pytest.mark.parametrize('name', ['helper.py','helper.js','helper.c'])
def test_later_checker_change_does_not_stale_earlier_current_sources(project,plan,name):
    pid=project['id'];engine.save_plan(pid,plan);sid=db.project(pid)['steps'][0]['id']
    old=Action(files=[{'path':'main.py','content':'print(1)'},{'path':name,'content':'old source'}],checks=[{'path':'check_current_step.py','content':'assert 1==1'}],explanation='first')
    engine.write_action(pid,sid,old)
    new=old.model_copy(deep=True);new.checks[0].content='assert 2==2'
    engine.write_action(pid,sid,new)
    active=db.query("SELECT name,state,provenance FROM artifacts WHERE project_id=? AND state!='stale'",(pid,))
    assert len(active)==3
    assert {x['name'] for x in active}=={'main.py',name,'check_current_step.py'}
    assert all(x['state']=='created' for x in active)
    assert len(db.query("SELECT id FROM artifacts WHERE project_id=? AND state='stale'",(pid,)))==3


def test_failed_storage_rolls_back_bytes_artifacts_and_downstream(project,plan,monkeypatch):
    pid=project['id'];engine.save_plan(pid,plan);sid=db.project(pid)['steps'][0]['id']
    old=Action(files=[{'path':'main.py','content':'print(1)'},{'path':'helper.py','content':'value=1'}],checks=[{'path':'check_current_step.py','content':'assert 1==1'}],explanation='old')
    root=engine.write_action(pid,sid,old);before=db.project(pid)['artifacts']
    actual=engine.artifact;calls=[]
    def fail(*a,**k):
        calls.append(1)
        if len(calls)==2:raise OSError('storage unavailable')
        return actual(*a,**k)
    monkeypatch.setattr(engine,'artifact',fail)
    new=old.model_copy(deep=True);new.files[0].content='print(2)';new.files[1].content='value=2'
    with pytest.raises(OSError):engine.write_action(pid,sid,new)
    assert db.project(pid)['artifacts']==before
    assert (root/'main.py').read_text()=='print(1)' and (root/'helper.py').read_text()=='value=1'


def test_source_change_invalidates_downstream_once(project,plan):
    pid=project['id'];plan['steps'].append(dict(plan['steps'][0],title='downstream'))
    engine.save_plan(pid,plan);steps=db.project(pid)['steps'];sid=steps[0]['id']
    a=Action(files=[{'path':'main.py','content':'print(1)'}],checks=[{'path':'check_current_step.py','content':'assert 1==1'}],explanation='a')
    engine.write_action(pid,sid,a)
    db.execute("UPDATE steps SET status='done',attempts=2,result=? WHERE id=?",(json.dumps({'old':True}),steps[1]['id']))
    a.files[0].content='print(2)';a.checks[0].content='assert 2==2';engine.write_action(pid,sid,a)
    downstream=db.project(pid)['steps'][1]
    assert downstream['status']=='pending' and downstream['result']=={} and downstream['attempts']==0
    assert len([x for x in db.project(pid)['artifacts'] if x['state']!='stale'])==2


@pytest.fixture
def accepted_run(project,plan):
    from study.codegen import save_candidate
    from study.schemas import ActionCheckpoint
    from study.execution_protocol import stream_identity
    pid=project['id'];engine.save_plan(pid,plan);engine.approve(pid);p=db.project(pid);sid=p['steps'][0]['id']
    a=Action(files=[{'path':'main.py','content':'print(7)'},{'path':'lib/values.py','content':'value=7'}],checks=[{'path':'check_current_step.py','content':'assert 7==7'}],entrypoint='main.py',expected_files=['result.csv'],explanation='fixture')
    runid='a'*32;run=config.project_dir(pid)/'runs'/runid;run.mkdir(parents=True)
    workspace=config.project_dir(pid)/'workspace';workspace.mkdir()
    for file in a.files+a.checks:
        path=run/file.path;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(file.content)
    (run/'result.csv').write_text('value\n7\n');(workspace/'result.csv').write_bytes((run/'result.csv').read_bytes())
    save_candidate(pid,sid,p['revision'],'fixture',a)
    cp=config.project_dir(pid)/'codegen'/sid/'last-action.json';saved=ActionCheckpoint.model_validate_json(cp.read_text());saved.written_to_workspace=True;cp.write_text(saved.model_dump_json())
    result={'run_id':runid,'exit_code':0,'oom':False,'reason':'','checks':['check_current_step.py'],'checker_results':[{'path':'check_current_step.py','completed':True,'exit_code':0}]}
    (run/'result.json').write_text(json.dumps(result))
    result.update(assessment={'satisfied':True},artifact_contract={'version':1,'outputs':{'result.csv':stream_identity(run/'result.csv')}})
    db.execute("UPDATE steps SET status='done',result=?,attempts=2 WHERE id=?",(json.dumps(result),sid))
    # A later step may legitimately own root main.py; recovery uses immutable run.
    (workspace/'main.py').write_text('print(999)')
    return pid,sid,run,workspace


def test_reconcile_restores_snapshots_idempotently_without_runtime_or_status_change(accepted_run,monkeypatch):
    from study.source_artifacts import reconcile
    pid,sid,run,workspace=accepted_run
    before=db.project(pid)['steps']
    monkeypatch.setattr(engine.DockerRuntime,'run',lambda *a,**k:pytest.fail('No runtime replay'))
    restored=reconcile(pid,sid)
    assert len(restored['restored'])==3 and reconcile(pid,sid)['restored']==[]
    assert db.project(pid)['steps']==before and (workspace/'main.py').read_text()=='print(999)'
    rows=db.query("SELECT path,provenance,state FROM artifacts WHERE project_id=?",(pid,))
    assert len(rows)==3 and all(r['state']=='executed' for r in rows)
    for row in rows:
        proof=json.loads(row['provenance']);assert proof['run_id']=='a'*32
        assert (config.project_dir(pid)/row['path']).read_bytes()==(run/proof['workspace_path']).read_bytes()


@pytest.mark.parametrize('tamper',['source','output','run','revision','assessment','checker'])
def test_reconcile_rejects_inconsistent_evidence_without_partial_metadata(accepted_run,tamper):
    from study.source_artifacts import reconcile
    pid,sid,run,workspace=accepted_run
    if tamper=='source':(run/'lib/values.py').write_text('value=99')
    if tamper=='output':(workspace/'result.csv').write_text('value\n99\n')
    if tamper=='run':
        value=json.loads((run/'result.json').read_text());value['exit_code']=1;(run/'result.json').write_text(json.dumps(value))
    if tamper=='revision':db.execute('UPDATE projects SET revision=revision+1 WHERE id=?',(pid,))
    if tamper in ('assessment','checker'):
        value=db.project(pid)['steps'][0]['result']
        if tamper=='assessment':value['assessment']['satisfied']=False
        else:value['checker_results'][0]['exit_code']=1
        db.execute('UPDATE steps SET result=? WHERE id=?',(json.dumps(value),sid))
    with pytest.raises(ValueError):reconcile(pid,sid)
    assert not db.query('SELECT id FROM artifacts WHERE project_id=?',(pid,))
