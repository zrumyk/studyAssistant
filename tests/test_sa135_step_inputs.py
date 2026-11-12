import json

import pytest

from study import config, db, engine
from study.artifact_contracts import capture_outputs
from study.input_inspection import inspect_inputs
from study.report_inputs import declared_report_evidence
from study.schemas import Action


@pytest.fixture
def handoff(project, plan):
    pid = project['id']
    step = plan['steps'][0]
    plan.update(artifact_contract_version=1, steps=[
        step | {'kind': 'write', 'artifact_outputs': ['query.sql', 'contract.txt']},
        step | {'artifact_inputs': ['query.sql', 'contract.txt']},
        step | {'kind': 'report', 'artifact_inputs': ['query.sql']},
    ])
    engine.save_plan(pid, plan)
    engine.approve(pid)
    producer, _, _ = db.project(pid)['steps']
    files = [
        {'path': 'query.sql', 'content': 'SELECT 1 AS item_a;'},
        {'path': 'contract.txt', 'content': 'verified record'},
        {'path': 'unrelated.sql', 'content': 'unrelated marker'},
    ]
    workspace = engine.write_action(pid, producer['id'], Action(files=files, explanation='Fixture'))
    receipt = capture_outputs(workspace, producer['spec'], [f['path'] for f in files])
    db.execute("UPDATE steps SET status='done',result=? WHERE id=?",
               (json.dumps({'artifact_contract': receipt}), producer['id']))
    return pid, workspace


def test_step_projection_is_verified_and_keeps_report_identity(handoff):
    pid, workspace = handoff
    project = db.project(pid)
    old_report = declared_report_evidence(project, max_bytes=1800)
    step = project['steps'][1]
    text, digest, cacheable = declared_report_evidence(project, consumer=step, max_bytes=1800)
    assert 'SELECT 1 AS item_a;' in text and 'verified record' in text
    assert 'unrelated marker' not in text and digest and cacheable
    (workspace / 'contract.txt').write_text('tampered record', encoding='utf-8')
    stale, new_digest, cacheable = declared_report_evidence(project, consumer=step, max_bytes=1800)
    assert 'stale' in stale and 'tampered record' not in stale and 'verified record' not in stale
    assert new_digest != digest and not cacheable
    assert declared_report_evidence(project, max_bytes=1800) == old_report


def test_declared_text_cannot_return_through_legacy_inspector(handoff, monkeypatch):
    pid, workspace = handoff
    step = db.project(pid)['steps'][1]
    (workspace / 'contract.txt').write_text('tampered record', encoding='utf-8')
    config.save_settings({'cloud_consent_mode': 'budgeted'})
    monkeypatch.setattr('study.assistance.BudgetedModel.generate',
                        lambda *a, **k: pytest.fail('Declared files should use the receipt reader'))
    assert inspect_inputs(pid, step['id'], step['spec'], workspace) == ''
