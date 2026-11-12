import copy
import json
from types import SimpleNamespace

import pytest

from study import assessment_recovery as recovery
from study import cloud, config, db, engine, worker

ERROR = 'Хмарний сервіс повернув HTTP 503; автоматичного повтору не буде'


@pytest.fixture
def stopped(client, project, plan, monkeypatch):
    config.save_settings({'cloud_provider': 'deepseek', 'cloud_model': 'deepseek-flash',
                          'cloud_consent_mode': 'budgeted', 'cloud_prices_date': db.now()[:10]})
    pid = project['id']
    p = copy.deepcopy(plan)
    p['artifact_contract_version'] = 1
    p['steps'][0]['artifact_outputs'] = ['data.json']
    engine.save_plan(pid, p)
    engine.approve(pid)
    step = db.project(pid)['steps'][0]
    root = config.project_dir(pid)
    folder = root/'runs'/('a'*32)
    folder.mkdir(parents=True)
    (folder/'check.py').write_text('assert 2+2==4\n', encoding='utf-8')
    (folder/'data.json').write_text('{"value":4}', encoding='utf-8')
    (root/'workspace').mkdir(exist_ok=True)
    (root/'workspace'/'data.json').write_bytes((folder/'data.json').read_bytes())
    snapshot = dict(run_id='a'*32, exit_code=0, oom=False, reason='', log='STUDY_ASSERTS_EXECUTED check.py 1',
                    checks=['check.py'], files=['data.json'], produced_files=['data.json'])
    (folder/'result.json').write_text(json.dumps(snapshot), encoding='utf-8')
    result = snapshot | {'assessment_pending': True, 'assessment_error': ERROR}
    db.execute("UPDATE steps SET status='waiting_user',attempts=1,result=? WHERE id=?", (json.dumps(result), step['id']))
    db.execute("UPDATE projects SET status='needs_user' WHERE id=?", (pid,))

    def fail(rid):
        db.execute("UPDATE cloud_requests SET state='uncertain' WHERE id=?", (rid,))
        raise RuntimeError(ERROR)

    monkeypatch.setattr(cloud, 'send', fail)
    monkeypatch.setattr(engine.DockerRuntime, 'run', lambda *a, **k: pytest.fail('Successful program must not run again'))
    reply = client.post(f'/api/projects/{pid}/steps/{step["id"]}/reassess')
    assert reply.status_code == 200
    dispatch_one()
    monkeypatch.setattr(recovery, 'COOLDOWN_SECONDS', 0)
    assert recovery.availability(db.project(pid), db.project(pid)['steps'][0])['available']
    return pid, step['id'], folder


def dispatch_one():
    job = worker.claim()
    assert job is not None
    result = worker.dispatch(job)
    db.execute("UPDATE jobs SET status='done',result=?,updated=? WHERE id=?", (json.dumps(result), db.now(), job['id']))
    return result


def good_send(monkeypatch, *, satisfied=True, needs_user=False, side_effect=None):
    calls = []

    def send(rid):
        calls.append(rid)
        if side_effect:
            side_effect()
        text = json.dumps({'satisfied': satisfied, 'needs_user': needs_user, 'explanation': 'Controlled reviewer fixture'})
        db.execute("UPDATE cloud_requests SET state='done',actual=.001,response=? WHERE id=?", (text, rid))
        return {'text': text, 'complete': True, 'cost_usd': .001, 'provider': 'deepseek'}

    monkeypatch.setattr(cloud, 'send', send)
    return calls


@pytest.mark.parametrize('language', ['python', 'c', 'javascript'])
def test_recovery_keeps_failed_receipt_run_and_counter(client, stopped, monkeypatch, language):
    pid, sid, folder = stopped
    # Language independence; only test fixture spec changes, never real student state.
    spec = db.project(pid)['steps'][0]['spec'] | {'language': language}
    db.execute('UPDATE steps SET spec=? WHERE id=?', (json.dumps(spec), sid))
    old = db.one('SELECT id,state,reserved,actual FROM cloud_requests WHERE project_id=?', (pid,))
    calls = good_send(monkeypatch)
    reply = client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment')
    assert reply.status_code == 200
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 400
    assert dispatch_one() == {'accepted': True, 'program_executed_again': False}
    step = db.project(pid)['steps'][0]
    assert step['status'] == 'done' and step['attempts'] == 1
    assert step['result']['run_id'] == 'a'*32 and step['result']['review_only_attempts'] == 1
    assert step['result']['assessment_recoveries'][0]['state'] == 'completed'
    assert len(calls) == 1 and calls[0] != old['id']
    assert db.one('SELECT id,state,reserved,actual FROM cloud_requests WHERE id=?', (old['id'],)) == old
    assert db.query("SELECT id FROM jobs WHERE kind='execute' AND status='queued'")
    assert (folder/'data.json').read_text() == '{"value":4}'


@pytest.mark.parametrize('needs_user', [False, True])
def test_completed_semantic_review_is_not_a_transport_retry(client, stopped, monkeypatch, needs_user):
    pid, sid, _ = stopped
    good_send(monkeypatch, satisfied=needs_user, needs_user=needs_user)
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 200
    assert dispatch_one()['accepted'] is False
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 400
    assert not db.query("SELECT id FROM jobs WHERE kind='execute'")


def test_repeated_service_failures_remain_bounded_and_do_not_resume(client, stopped):
    pid, sid, _ = stopped
    for _ in range(recovery.MAX_RECOVERIES):
        assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 200
        assert dispatch_one()['accepted'] is False
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 400
    records = db.query('SELECT id,state FROM cloud_requests WHERE project_id=?', (pid,))
    assert len(records) == 3 and len({r['id'] for r in records}) == 3
    assert all(r['state'] == 'uncertain' for r in records)
    assert not db.query("SELECT id FROM jobs WHERE kind='execute'")


@pytest.mark.parametrize('tamper', ['output', 'checker', 'approval'])
def test_queue_worker_rechecks_evidence_and_approval(client, stopped, monkeypatch, tamper):
    pid, sid, folder = stopped
    calls = good_send(monkeypatch)
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 200
    if tamper == 'output':
        (config.project_dir(pid)/'workspace'/'data.json').write_text('{"value":99}')
    elif tamper == 'checker':
        (folder/'check.py').write_text('assert False')
    else:
        db.execute('UPDATE projects SET approved_hash=NULL WHERE id=?', (pid,))
    assert dispatch_one()['accepted'] is False
    assert calls == []
    assert db.project(pid)['steps'][0]['status'] == 'waiting_user'


def test_error_text_without_receipt_or_wrong_run_cannot_unlock(client, stopped):
    pid, sid, _ = stopped
    result = db.project(pid)['steps'][0]['result']
    result.pop('review_request_id', None)
    result.pop('review_request_run_id', None)
    db.execute('UPDATE steps SET result=? WHERE id=?', (json.dumps(result), sid))
    db.execute("DELETE FROM events WHERE project_id=? AND kind='result_reassessed'", (pid,))
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 400
    assert len(db.query('SELECT id FROM cloud_requests WHERE project_id=?', (pid,))) == 1


def test_legacy_service_failure_uses_same_step_job_window_and_run_event(client, stopped):
    pid, sid, _ = stopped
    result = db.project(pid)['steps'][0]['result']
    result.pop('review_request_id', None)
    result.pop('review_request_run_id', None)
    db.execute('UPDATE steps SET result=? WHERE id=?', (json.dumps(result), sid))
    assert recovery.availability(db.project(pid), db.project(pid)['steps'][0])['available']
    result['run_id'] = 'b'*32
    db.execute('UPDATE steps SET result=? WHERE id=?', (json.dumps(result), sid))
    assert not recovery.availability(db.project(pid), db.project(pid)['steps'][0])['available']


def test_queued_recovery_survives_worker_restart_without_new_call(client, stopped, monkeypatch):
    import subprocess
    pid, sid, _ = stopped
    calls = good_send(monkeypatch)
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 200
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=''))
    worker.recover()
    assert db.project(pid)['status'] == 'running'
    assert calls == []
    assert dispatch_one()['accepted'] is True


def test_cooldown_permission_and_pause_prevent_dispatch(client, stopped, monkeypatch):
    pid, sid, _ = stopped
    monkeypatch.setattr(recovery, 'COOLDOWN_SECONDS', 60)
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 400
    monkeypatch.setattr(recovery, 'COOLDOWN_SECONDS', 0)
    config.save_settings({'cloud_consent_mode': 'per_request'})
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 400
    config.save_settings({'cloud_consent_mode': 'budgeted'})
    db.execute("UPDATE projects SET status='paused' WHERE id=?", (pid,))
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 400


def test_pause_during_review_does_not_resume(client, stopped, monkeypatch):
    pid, sid, _ = stopped
    good_send(monkeypatch, side_effect=lambda: db.execute("UPDATE projects SET status='paused' WHERE id=?", (pid,)))
    assert client.post(f'/api/projects/{pid}/steps/{sid}/recover-assessment').status_code == 200
    assert dispatch_one()['accepted'] is True
    assert db.project(pid)['status'] == 'paused'
    assert not db.query("SELECT id FROM jobs WHERE kind='execute'")
