import json

from study import config
from study.planning_facts import planning_facts


def test_planning_facts_share_global_profile_and_exact_capabilities():
    config.save_settings({'author': 'Global Author', 'group': 'Global Group', 'institution': 'Global Institution'})
    facts = json.loads(planning_facts())
    assert facts['global_report_profile'] == {'author': 'Global Author', 'group': 'Global Group', 'institution': 'Global Institution'}
    assert facts['runtime_profiles']['c']['entrypoint'] == 'main.c'
    assert facts['runtime_profiles']['javascript']['entrypoint'] == 'main.js'
    assert facts['runtime_profiles']['c']['automatic_dependencies'] is False
    assert facts['runtime_profiles']['python']['automatic_dependencies'] is True
    assert facts['runtime_profiles']['c']['checker_packages'] == 'stdlib only'
    assert facts['runtime_profiles']['java']['checker_language'] is None
    assert facts['stdin']['max_utf8_bytes'] == 65536
    assert facts['runtime_profiles']['c']['same_container_test_argv'] == ['/tmp/program']
    assert facts['workspace_inputs'] == []
    assert 'current run only' in facts['execution_scope']['capture_scope']
    contract = facts['execution_scope']['artifact_contract']
    assert contract['version'] == 1
    assert 'artifact_outputs MUST be []' in contract['report']
    assert 'report artifact registry' in contract['report']
    assert 'artifact_outputs MUST be []' in contract['manual']
    assert set(facts) == {'global_report_profile', 'runtime_profiles', 'stdin', 'external_files', 'availability_limit', 'workspace_inputs', 'workspace_inputs_truncated', 'execution_scope', 'dependency_specs'}


def test_planner_and_reviewer_receive_identical_live_facts(project, plan, monkeypatch):
    import copy
    from study import engine, db
    from study.assistance import BudgetedModel
    from study.schemas import Assessment, PlanDraft
    config.save_settings({'author': 'Current Author', 'group': 'Current Group', 'institution': 'Current Institution'})
    draft = copy.deepcopy(plan)
    draft.pop('sources')
    draft['steps'].append(dict(title='Report', instruction='Create report', kind='report', expected='DOCX/PDF'))
    prompts = []
    facts = planning_facts()

    def generate(self, prompt, schema):
        prompts.append(prompt)
        assert facts in prompt
        return PlanDraft.model_validate(draft)

    def review(self, prompt, schema, *args, **kwargs):
        prompts.append(prompt)
        assert facts in prompt
        assert schema is Assessment
        assert 'report artifact registry' in prompt
        return Assessment(satisfied=True, explanation='Controlled review fixture', needs_user=False)

    monkeypatch.setattr(engine, 'context', lambda *a, **k: '')
    monkeypatch.setattr(BudgetedModel, 'generate', generate)
    monkeypatch.setattr(BudgetedModel, 'remote_with_fallback', review)
    monkeypatch.setattr(BudgetedModel, 'available', lambda self: True)
    engine.analyse(project['id'])
    assert len(prompts) == 2
    assert db.project(project['id'])['status'] == 'awaiting_approval'


def test_workspace_manifest_does_not_invent_access_to_repository_or_rag_files(project):
    import hashlib
    workspace = config.project_dir(project['id']) / 'workspace'
    available = workspace / 'inputs' / 'document-id' / 'starter.c'
    available.parent.mkdir(parents=True)
    available.write_bytes(b'// actual provided input\n')
    unavailable = config.DATA / 'source-only.html'
    unavailable.write_text('RAG-only source outside sandbox', encoding='utf-8')
    inputs = json.loads(planning_facts(project['id']))['workspace_inputs']
    assert inputs == [{'path': 'inputs/document-id/starter.c', 'bytes': available.stat().st_size,
                       'sha256': hashlib.sha256(available.read_bytes()).hexdigest()}]


def test_large_input_manifest_declares_incomplete_coverage(project):
    root = config.project_dir(project['id']) / 'workspace' / 'inputs'
    root.mkdir(parents=True)
    for index in range(33):
        (root / f'{index:03}.txt').write_bytes(b'data')
    facts = json.loads(planning_facts(project['id']))
    assert len(facts['workspace_inputs']) == 32
    assert facts['workspace_inputs_truncated'] is True
    (root / '032.txt').unlink()
    assert json.loads(planning_facts(project['id']))['workspace_inputs_truncated'] is False
