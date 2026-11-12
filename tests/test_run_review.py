import json
import pytest
from study import config,db,engine,worker
from study.schemas import Assessment


@pytest.fixture
def waiting_run(project,plan):
    pid=project['id'];engine.save_plan(pid,plan);engine.approve(pid)
    step=db.project(pid)['steps'][0];runid='f'*32
    folder=config.project_dir(pid)/'runs'/runid;folder.mkdir(parents=True)
    (folder/'check_current_step.py').write_text('values=[0,2,3]\nassert min(values)==0\n','utf-8')
    snapshot={'run_id':runid,'exit_code':0,'oom':False,'reason':'','log':'STUDY_ASSERTS_EXECUTED check_current_step.py 1','checks':['check_current_step.py'],'files':[]}
    (folder/'result.json').write_text(json.dumps(snapshot),'utf-8')
    result=snapshot|{'assessment':{'satisfied':False,'explanation':'Zero is suspicious','needs_user':True},'instructions':'Please review'}
    db.execute("UPDATE steps SET status='waiting_user',result=? WHERE id=?",(json.dumps(result),step['id']))
    db.execute("UPDATE projects SET status='needs_user' WHERE id=?",(pid,))
    return pid,step['id'],runid


def test_review_route_resumes_after_positive_review_without_executing_code(client,waiting_run,monkeypatch):
    from study import run_review
    pid,sid,runid=waiting_run
    prompts=[]
    def generate(self,prompt,schema):
        prompts.append(prompt)
        assert 'assert min(values)==0' in prompt
        assert 'STUDY_ASSERTS_EXECUTED' in prompt and self.thinking_override is True
        return Assessment(satisfied=True,explanation='Zero is the independently checked minimum')
    monkeypatch.setattr(run_review.BudgetedModel,'generate',generate)
    monkeypatch.setattr(engine.DockerRuntime,'run',lambda *a,**k:pytest.fail('Do not execute again'))
    response=client.post(f'/api/projects/{pid}/steps/{sid}/reassess');assert response.status_code==200
    job=db.one('SELECT * FROM jobs WHERE id=?',(response.json()['job_id'],))
    outcome=worker.dispatch(job)
    assert outcome=={'accepted':True,'program_executed_again':False}
    p=db.project(pid);step=p['steps'][0]
    assert step['status']=='done' and step['result']['run_id']==runid
    assert step['result']['review_only_attempts']==1 and 'instructions' not in step['result']
    assert p['approved_hash'] and db.query("SELECT id FROM jobs WHERE kind='execute' AND status='queued'")
    assert len(prompts)==1


@pytest.mark.parametrize('assessment',[
    Assessment(satisfied=False,explanation='A concrete defect remains',needs_user=True),
    Assessment(satisfied=True,explanation='Still requires user evidence',needs_user=True),
])
def test_negative_or_uncertain_review_preserves_run_and_is_bounded(waiting_run,monkeypatch,assessment):
    from study import run_review
    pid,sid,runid=waiting_run;calls=[]
    def generate(*args,**kwargs):calls.append(1);return assessment
    monkeypatch.setattr(run_review.BudgetedModel,'generate',generate)
    db.execute("UPDATE projects SET status='running' WHERE id=?",(pid,))
    assert engine.reassess_result(pid,sid)['accepted'] is False
    step=db.project(pid)['steps'][0]
    assert step['status']=='waiting_user' and step['result']['run_id']==runid
    with pytest.raises(ValueError,match='вже виконано'):engine.reassess_result(pid,sid)
    assert len(calls)==1 and not db.query("SELECT id FROM jobs WHERE kind='execute'")


def test_changed_plan_or_failed_execution_cannot_be_accepted_by_review(client,waiting_run):
    pid,sid,_=waiting_run
    db.execute('UPDATE projects SET approved_hash=NULL WHERE id=?',(pid,))
    assert client.post(f'/api/projects/{pid}/steps/{sid}/reassess').status_code>=400
    assert not db.query("SELECT id FROM jobs WHERE kind='reassess'")


def test_pause_during_review_does_not_resume_execution(waiting_run,monkeypatch):
    from study import run_review
    pid,sid,_=waiting_run
    def generate(*args,**kwargs):
        db.execute("UPDATE projects SET status='paused' WHERE id=?",(pid,))
        return Assessment(satisfied=True,explanation='Saved checks sufficient')
    monkeypatch.setattr(run_review.BudgetedModel,'generate',generate)
    db.execute("UPDATE projects SET status='running' WHERE id=?",(pid,))
    assert engine.reassess_result(pid,sid)['accepted'] is True
    assert db.project(pid)['status']=='paused'
    assert not db.query("SELECT id FROM jobs WHERE kind='execute'")
