"""Regression of the normal report builder with a declared write input."""
import json

from study import assistance, config, db, engine, reports
from study.artifact_contracts import capture_outputs
from study.report_inputs import declared_report_evidence
from study.schemas import Action, ContentReview


def test_write_input_reaches_author_and_review_and_invalidates_cache(project, plan, monkeypatch):
    pid = project['id']
    name = 'schema.sql'
    base_step = plan['steps'][0]
    plan.update(artifact_contract_version=1, report_sections=['Результати'], steps=[
        base_step | {'kind': 'write', 'artifact_outputs': [name]},
        base_step | {'kind': 'report', 'artifact_inputs': [name]},
    ])
    engine.save_plan(pid, plan)
    engine.approve(pid)
    workspace = config.project_dir(pid) / 'workspace'
    workspace.mkdir(exist_ok=True)
    source = workspace / name
    producer = db.project(pid)['steps'][0]

    def record_write(text):
        action = Action(files=[{'path': name, 'content': text}], explanation='Controlled write fixture')
        engine.write_action(pid, producer['id'], action)
        result = {'artifact_contract': capture_outputs(workspace, producer['spec'], [name])}
        db.execute("UPDATE steps SET status='done',result=? WHERE id=?", (json.dumps(result), producer['id']))

    record_write('CREATE TABLE fixture_one(id INTEGER);')
    monkeypatch.setattr('study.search.context', lambda *args, **kwargs: '')
    monkeypatch.setattr(reports, 'binary', lambda *args: None)
    calls = []

    def generate(self, prompt, schema=None):
        calls.append(prompt)
        return ContentReview(blocking_issues=[], explanation='Fixture review') if schema else 'Результати контрольної роботи.'

    monkeypatch.setattr(assistance.BudgetedModel, 'generate', generate)
    reports.build_report(pid)
    assert len(calls) == 2 and all('fixture_one' in prompt for prompt in calls)
    calls.clear()
    reports.build_report(pid)
    assert calls == []

    source.write_bytes(b'CREATE TABLE stale_bytes(id INTEGER);')
    reports.build_report(pid)
    assert len(calls) == 2 and all('stale' in prompt and 'stale_bytes' not in prompt for prompt in calls)
    assert len(list((config.project_dir(pid) / 'report-sections').glob('*.json'))) == 1

    calls.clear()
    record_write('CREATE TABLE fixture_two(id INTEGER);')
    reports.build_report(pid)
    assert len(calls) == 2 and all('fixture_two' in prompt for prompt in calls)
    assert len(list((config.project_dir(pid) / 'report-sections').glob('*.json'))) == 2


def test_omitted_source_bytes_change_digest_and_context_stays_bounded(project, plan):
    pid = project['id']
    workspace = config.project_dir(pid) / 'workspace'
    workspace.mkdir(exist_ok=True)
    spec = {'kind': 'write', 'artifact_outputs': ['source.ts']}
    report = {'position': 1, 'spec': {'kind': 'report', 'artifact_inputs': ['source.ts']}}
    sample = {'id': pid, 'plan': {'artifact_contract_version': 1}, 'steps': [None, report]}
    digests = []
    for char in ('а', 'б'):
        (workspace / 'source.ts').write_bytes((char * 2000).encode())
        result = {'artifact_contract': capture_outputs(workspace, spec, ['source.ts'])}
        sample['steps'][0] = {'id': 'write', 'position': 0, 'spec': spec, 'status': 'done', 'result': result}
        context, digest, cacheable = declared_report_evidence(sample, max_bytes=512)
        assert len(context.encode()) <= 512 and 'omitted_budget' in context and cacheable
        digests.append(digest)
    assert digests[0] != digests[1]
