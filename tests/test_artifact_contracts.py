import copy
import hashlib
import json

import pytest

from study import config, db, engine
from study.artifact_contracts import artifact_path, validate_contract, check_action, capture_outputs, require_inputs, fresh_outputs
from study.schemas import Action, Plan


def contract_plan():
    return Plan.model_validate(dict(artifact_contract_version=1, goal='Transfer measured data', requirements=[],
        missing=[], conflicts=[], report_sections=['Results'], steps=[
            dict(title='Native', kind='code', language='c', instruction='Compute', expected='data.json',
                 artifact_outputs=['data.json']),
            dict(title='Summarize', kind='code', language='javascript', instruction='Read measured data', expected='summary.csv',
                 artifact_inputs=['data.json'], artifact_outputs=['summary.csv']),
            dict(title='Report', kind='report', instruction='Render', expected='DOCX/PDF', artifact_inputs=['summary.csv'])]))


@pytest.mark.parametrize('path', ['/tmp/program', '/work/data.json', r'D:\data.csv', '../data.csv',
                                  '.', 'a/../b', 'a//b', '.study_main.stdout', 'a/.hidden', 'x\x00y', 'result.json'])
def test_nonportable_or_private_paths_cannot_be_transferred(path):
    with pytest.raises(ValueError):
        artifact_path(path)


def test_graph_requires_prior_single_producer_and_readonly_inputs(project):
    plan = contract_plan()
    validate_contract(plan, project['id'], require=True)
    invalid = plan.model_copy(deep=True)
    invalid.steps[1].artifact_inputs = ['unpublished.bin']
    with pytest.raises(ValueError, match='producer'):
        validate_contract(invalid)
    invalid = plan.model_copy(deep=True)
    invalid.steps[1].artifact_outputs = ['data.json']
    with pytest.raises(ValueError, match='producer'):
        validate_contract(invalid)
    invalid = plan.model_copy(deep=True)
    invalid.steps[0].artifact_inputs = ['summary.csv']
    with pytest.raises(ValueError):
        validate_contract(invalid)
    invalid = plan.model_copy(deep=True)
    invalid.steps[0].artifact_outputs = ['inputs/data.json']
    with pytest.raises(ValueError, match='читання'):
        validate_contract(invalid)
    invalid = plan.model_copy(deep=True)
    invalid.steps[1].artifact_inputs = ['DATA.json']
    with pytest.raises(ValueError, match='producer'):
        validate_contract(invalid)


def test_real_import_path_checked_before_plan_replaces_existing_steps(project, plan):
    engine.save_plan(project['id'], plan)
    before = db.project(project['id'])
    data = contract_plan()
    data.steps[0].artifact_inputs = ['inputs/missing.c']
    with pytest.raises(ValueError, match='відсутній'):
        engine.save_plan(project['id'], data.model_dump())
    assert db.project(project['id'])['plan'] == before['plan']
    assert [s['id'] for s in db.project(project['id'])['steps']] == [s['id'] for s in before['steps']]


def test_action_cannot_silently_publish_other_files_or_overwrite_input():
    spec = contract_plan().steps[1].model_dump()
    action = Action(entrypoint='main.js', explanation='fixture', files=[{'path':'main.js','content':'code'}], expected_files=['summary.csv'])
    assert check_action(spec, action) == []
    # An extra scratch file next to the declared output is unpublished, not fatal.
    extra = action.model_copy(deep=True)
    extra.expected_files = ['summary.csv', 'scratch.db']
    assert check_action(spec, extra) == ['scratch.db']
    assert extra.expected_files == ['summary.csv']
    # Replacing the declared output with another file is still rejected.
    bad = action.model_copy(deep=True)
    bad.expected_files = ['other.csv']
    with pytest.raises(ValueError, match='Data output'):
        check_action(spec, bad)
    # Writing into the read-only inputs/ namespace is still rejected.
    bad = action.model_copy(deep=True)
    bad.expected_files = ['summary.csv', 'inputs/x.csv']
    with pytest.raises(ValueError, match='artifact_outputs'):
        check_action(spec, bad)
    bad = action.model_copy(deep=True)
    bad.files.append({'path':'data.json','content':'changed'})
    from study.schemas import FileWrite
    bad.files[-1] = FileWrite.model_validate(bad.files[-1])
    with pytest.raises(ValueError, match='перезаписати'):
        check_action(spec, bad)


def test_prepared_path_records_unpublished_outputs(project):
    from study import prepared
    spec = contract_plan().steps[1].model_dump()
    action = Action(entrypoint='main.js', explanation='fixture', files=[{'path':'main.js','content':'code'}],
                    expected_files=['summary.csv', 'scratch.db'])
    workspace = config.project_dir(project['id']) / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        prepared.validate(action, spec, workspace, contract_version=1)
    except ValueError:
        pass  # later prepared checks may reject this fixture; the contract step ran first
    events = db.query("SELECT data FROM events WHERE project_id=? AND kind='undeclared_outputs_unpublished'", (project['id'],))
    assert events and 'scratch.db' in events[0]['data']
    assert action.expected_files == ['summary.csv']


def test_consumer_requires_matching_completed_producer_receipt(project):
    pid = project['id']
    root = config.project_dir(pid) / 'workspace'
    root.mkdir()
    (root/'data.json').write_bytes(b'{"value":26}')
    spec = contract_plan().steps[0].model_dump()
    receipt = capture_outputs(root, spec, ['data.json'])
    first = dict(position=0, status='done', spec=spec, result={'artifact_contract':receipt})
    second = dict(position=1, spec=contract_plan().steps[1].model_dump())
    p = dict(id=pid, plan={'artifact_contract_version':1}, steps=[first, second])
    require_inputs(p, second)
    (root/'data.json').write_bytes(b'{"value":99}')
    with pytest.raises(RuntimeError, match='змінився'):
        require_inputs(p, second)
    (root/'data.json').write_bytes(b'{"value":26}')
    first['status'] = 'waiting_user'
    with pytest.raises(RuntimeError, match='Producer'):
        require_inputs(p, second)


def test_receipt_cannot_bind_post_run_fabrication(project):
    root = config.project_dir(project['id']) / 'workspace'
    run = config.project_dir(project['id']) / 'runs' / 'fixture'
    root.mkdir(); run.mkdir(parents=True)
    (run/'data.json').write_bytes(b'original')
    (root/'data.json').write_bytes(b'fabricated')
    with pytest.raises(ValueError, match='snapshot'):
        capture_outputs(root, contract_plan().steps[0].model_dump(), ['data.json'], snapshot=run)


def test_retry_archives_old_outputs_and_keeps_model_source(project):
    root = config.project_dir(project['id'])/'workspace'
    (root/'results').mkdir(parents=True)
    (root/'results/old.txt').write_bytes(b'failed attempt evidence')
    action = Action(explanation='model source', files=[{'path':'results/helper.py','content':'value=1\n'}])
    fresh_outputs(project['id'], {'artifact_outputs':['results']}, action)
    assert not (root/'results/old.txt').exists()
    assert (root/'results/helper.py').read_text('utf-8') == 'value=1\n'
    old = list((config.project_dir(project['id'])/'history').rglob('old.txt'))
    assert len(old)==1 and old[0].read_bytes()==b'failed attempt evidence'


def test_legacy_approval_digest_stays_identical(project, plan):
    engine.save_plan(project['id'], plan)
    p = db.project(project['id'])
    old = copy.deepcopy(p['plan'])
    old.pop('artifact_contract_version')
    for step in old['steps']:
        step.pop('artifact_inputs'); step.pop('artifact_outputs')
    digest = hashlib.sha256(json.dumps(old, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert engine.plan_hash(p['plan']) == digest
    db.execute("UPDATE projects SET plan=?,approved_hash=?,status='paused' WHERE id=?", (json.dumps(old), digest, project['id']))
    engine.approve(project['id'])
    current = db.project(project['id'])
    assert current['approved_hash'] == digest and current['status'] == 'paused' and current['plan'] == old


def test_legacy_plan_cannot_masquerade_as_new_generated_contract(plan):
    with pytest.raises(ValueError, match='artifact_contract_version'):
        validate_contract(Plan.model_validate(plan), require=True)


@pytest.mark.parametrize('outputs', [[], ['inputs'], ['inputs/user.csv'], ['inputs/downloads/a','inputs/downloads/b']])
def test_fetch_contract_cannot_replace_imported_input_namespace(outputs):
    plan = contract_plan()
    plan.steps[0].kind = 'fetch'
    plan.steps[0].artifact_outputs = outputs
    with pytest.raises(ValueError, match='fetch'):
        validate_contract(plan)


@pytest.mark.parametrize('tamper', [False, True])
def test_engine_records_receipt_or_holds_success_without_reexecution(project, monkeypatch, tamper):
    from study.schemas import Assessment
    from study.assistance import BudgetedModel
    pid = project['id']
    data = contract_plan().model_dump()
    data['steps'] = [data['steps'][0], data['steps'][2]]
    data['steps'][0]['language'] = 'python'
    data['steps'][1]['artifact_inputs'] = ['data.json']
    engine.save_plan(pid, data)
    engine.approve(pid)
    action = Action(entrypoint='main.py', files=[dict(path='main.py', content='print(26)')],
                    checks=[dict(path='check_current.py', content='assert 2+2==4')], expected_files=['data.json'], explanation='fixture')
    monkeypatch.setattr(engine, 'prepare_action', lambda *args: action)
    monkeypatch.setattr(engine.OllamaProvider, 'unload', lambda self: None)
    monkeypatch.setattr(BudgetedModel, 'generate', lambda *a, **k: Assessment(satisfied=True, needs_user=False, explanation='Controlled fixture'))
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs['publish_files'] == ['data.json'] and kwargs['readonly_artifacts'] == []
        workspace = config.project_dir(pid)/'workspace'
        snapshot = config.project_dir(pid)/'runs'/'fixture'
        snapshot.mkdir(parents=True)
        (snapshot/'data.json').write_bytes(b'{"value":26}')
        (workspace/'data.json').write_bytes(b'{"value":99}' if tamper else b'{"value":26}')
        return dict(run_id='fixture', exit_code=0, oom=False, reason='', checks=['check_current.py'],
                    produced_files=['data.json'], files=['data.json'], log='fixture')

    monkeypatch.setattr(engine.DockerRuntime, 'run', run)
    monkeypatch.setattr('study.reports.build_report', lambda *a: {'files':[]})
    engine.execute_project(pid)
    p = db.project(pid)
    assert len(calls) == 1
    if tamper:
        assert p['status'] == 'needs_user' and p['steps'][0]['status'] == 'waiting_user'
        assert p['steps'][0]['result']['exit_code'] == 0 and p['steps'][0]['result']['artifact_contract_error']
        with pytest.raises(ValueError, match='snapshot'):
            engine.reassess_result(pid, p['steps'][0]['id'])
        assert len(calls) == 1
    else:
        assert p['status'] == 'completed'
        assert p['steps'][0]['result']['artifact_contract']['outputs']['data.json']['sha256']
