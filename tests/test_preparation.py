import json
import pytest
from study import config,db,engine,preparation,preflight,worker
from study.schemas import Action


def approved(project,plan):
    engine.save_plan(project['id'],plan)
    engine.approve(project['id'])
    p=db.project(project['id']);s=p['steps'][0]
    db.execute("UPDATE steps SET status='failed',attempts=3 WHERE id=?",(s['id'],))
    db.execute("UPDATE projects SET status='needs_user' WHERE id=?",(p['id'],))
    return db.project(p['id']),s


def test_prepare_job_never_executes_or_resets_attempts(client,project,plan,monkeypatch):
    p,s=approved(project,plan);pid=p['id'];sid=s['id']
    root=config.project_dir(pid)/'workspace';root.mkdir();(root/'main.py').write_text('ORIGINAL','utf-8')
    action=Action(files=[{'path':'main.py','content':'value=4'}],checks=[{'path':'check_result.py','content':'assert 2+2==4'}],entrypoint='main.py',explanation='')
    def generate(*args,cancelled_check):
        assert not cancelled_check()
        from study.codegen import save_candidate
        save_candidate(pid,sid,p['revision'],'test',action)
        return action
    monkeypatch.setattr(engine,'prepare_action',generate)
    monkeypatch.setattr(preflight,'check_expensive_action',lambda *a:None)
    monkeypatch.setattr(engine.DockerRuntime,'run',lambda *a,**k:pytest.fail('No runtime in preparation'))
    response=client.post(f'/api/projects/{pid}/steps/{sid}/prepare');assert response.status_code==200
    job=db.one('SELECT * FROM jobs WHERE id=?',(response.json()['job_id'],))
    assert worker.dispatch(job)['status']=='ready'
    after=db.project(pid);assert after['status']==p['status'] and after['steps'][0]['attempts']==3
    assert after['steps'][0]['status']=='failed'
    assert (root/'main.py').read_text('utf-8')=='ORIGINAL'
    assert client.post(f'/api/projects/{pid}/steps/{sid}/prepare').status_code==400


def test_prepare_rejects_changed_approval_and_success(project,plan,monkeypatch):
    p,s=approved(project,plan)
    monkeypatch.setattr(engine,'prepare_action',lambda *a,**k:pytest.fail('No generation'))
    with pytest.raises(ValueError,match='змінився'):preparation.prepare(p['id'],s['id'],p['revision']-1,p['approved_hash'])
    db.execute('UPDATE steps SET result=? WHERE id=?',(json.dumps({'exit_code':0}),s['id']))
    with pytest.raises(ValueError,match='успішний'):preparation.eligible(p['id'],s['id'])


def test_pause_during_prepare_discards_result(project,plan,monkeypatch):
    p,s=approved(project,plan)
    def generate(*a,cancelled_check):
        db.event(p['id'],'paused',{})
        assert cancelled_check()
        return Action(explanation='cancelled')
    monkeypatch.setattr(engine,'prepare_action',generate)
    assert preparation.prepare(p['id'],s['id'],p['revision'],p['approved_hash'])=={'status':'cancelled','executed':False}


def test_pause_cancels_queued_preparation(client,project,plan):
    p,s=approved(project,plan)
    jid=client.post(f"/api/projects/{p['id']}/steps/{s['id']}/prepare").json()['job_id']
    client.post(f"/api/projects/{p['id']}/pause")
    assert db.one('SELECT status FROM jobs WHERE id=?',(jid,))['status']=='cancelled'


def test_unapproved_preparation_is_rejected(client,project,plan):
    engine.save_plan(project['id'],plan)
    sid=db.project(project['id'])['steps'][0]['id']
    assert client.post(f"/api/projects/{project['id']}/steps/{sid}/prepare").status_code==400


def test_candidate_diagnostic_is_bound_to_exact_source(client,project,plan):
    import hashlib
    from study.codegen import save_candidate
    p,s=approved(project,plan)
    action=Action(files=[{'path':'main.py','content':'value=4'}],explanation='')
    save_candidate(p['id'],s['id'],p['revision'],'test',action)
    payload={'candidate_sha256':'0'*64,'log':'Actual probe failure'}
    url=f"/api/projects/{p['id']}/steps/{s['id']}/prepare"
    assert client.post(url,json=payload).status_code==400
    payload['candidate_sha256']=hashlib.sha256(action.model_dump_json().encode()).hexdigest()
    assert client.post(url,json=payload).status_code==200


def test_repair_unwritten_candidate_uses_staged_bytes(project,plan):
    from study.codegen import save_candidate,repair_action
    from study.schemas import PythonRepair
    p,s=approved(project,plan)
    workspace=config.project_dir(p['id'])/'workspace';workspace.mkdir()
    (workspace/'main.py').write_text('OLD_WORKSPACE','utf-8')
    action=Action(files=[{'path':'main.py','content':'value=4'}],explanation='')
    save_candidate(p['id'],s['id'],p['revision'],'test',action)
    proposal=PythonRepair(edits=[{'path':'main.py','before':'value=4','after':'value=5'}],explanation='repair',replace_expected_files=False,expected_files=[])
    fixed=repair_action(p['id'],s['id'],p['revision'],workspace,proposal)
    assert fixed.files[0].content=='value=5'
    assert (workspace/'main.py').read_text('utf-8')=='OLD_WORKSPACE'
