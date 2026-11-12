import copy
import json

import pytest

from study.artifact_contracts import validate_contract
from study.artifact_repair import graph_repair_context, graph_prompt, validate_graph_patch
from study.plan_patches import PlanEdit, PlanPatch, apply_plan_patch, draft_hash
from study.schemas import PlanDraft, Step


def example(language='python', prefix='work'):
    return PlanDraft(goal='Fixture only', requirements=['Retain sources'], missing=[], conflicts=[], report_sections=[], steps=[
        Step(title='Inventory', kind='code', language=language, instruction='Inspect', expected='Manifest',
             artifact_outputs=[f'{prefix}/source.txt', f'{prefix}/config.json', f'{prefix}/inventory.json']),
        Step(title='Implementation', kind='code', language=language, instruction='Create', expected='Source',
             artifact_inputs=[f'{prefix}/source.txt', f'{prefix}/config.json'],
             artifact_outputs=[f'{prefix}/source.txt', f'{prefix}/config.json', f'{prefix}/run.json']),
        Step(title='Check', kind='code', language=language, instruction='Check', expected='Evidence',
             artifact_inputs=[f'{prefix}/source.txt', f'{prefix}/run.json'], artifact_outputs=[f'{prefix}/check.json']),
        Step(title='Report', kind='report', instruction='Render', expected='Report',
             artifact_inputs=[f'{prefix}/check.json', f'{prefix}/run.json'])])


def edits(base, changes):
    data = base.model_dump()
    values = []
    for path, after in changes.items():
        parts = path.split('/')
        before = data['steps'][int(parts[2])][parts[3]]
        values.append(PlanEdit(op='replace', path=path, before_json=json.dumps(before), after_json=json.dumps(after)))
    return PlanPatch(base_sha256=draft_hash(base), requires_replan=False, explanation='Controlled test fixture', edits=values)


@pytest.mark.parametrize('language,prefix', [('python','measurements'), ('typescript','project'), ('c','build')])
def test_complete_witnesses_and_minimal_list_field_repair(language, prefix):
    base = example(language, prefix); original = copy.deepcopy(base.model_dump())
    context = graph_repair_context(base)
    assert context['complete'] and context['scope_enforced']
    assert len(context['issues']) == 4  # Two duplicate producers and two overwrites.
    assert context['conflict_paths'] == [f'{prefix}/config.json', f'{prefix}/source.txt']
    assert [2, f'{prefix}/source.txt'] in context['consumers']
    assert '/steps/3/artifact_inputs' not in context['affected_fields']
    change = edits(base, {'/steps/0/artifact_outputs': [f'{prefix}/inventory.json']})
    validate_graph_patch(base, change)
    result = apply_plan_patch(base, change)
    # Completing a real repair is up to the model. This fixture proves the
    # scope gate neither writes a solution nor certifies artifact validity.
    with pytest.raises(ValueError, match='попереднього producer'):
        validate_contract(result, require=True)
    assert base.model_dump() == original


def test_valid_three_field_fixture_passes_whole_graph_and_preserves_others():
    base = example(); original = copy.deepcopy(base.model_dump())
    change = edits(base, {'/steps/0/artifact_outputs': ['work/inventory.json'],
                         '/steps/1/artifact_inputs': ['work/inventory.json'],
                         '/steps/0/instruction': 'Controlled fixture inventory only'})
    validate_graph_patch(base, change)
    result = apply_plan_patch(base, change)
    validate_contract(result, require=True)
    expected = copy.deepcopy(original)
    expected['steps'][0]['artifact_outputs'] = ['work/inventory.json']
    expected['steps'][1]['artifact_inputs'] = ['work/inventory.json']
    expected['steps'][0]['instruction'] = 'Controlled fixture inventory only'
    assert result.model_dump() == expected and base.model_dump() == original


@pytest.mark.parametrize('path,after', [('/steps/3/instruction','Unrelated rewrite'),
    ('/steps/1/dependencies',['invented-package']),('/steps/2/artifact_outputs',['work/renamed-check.json']),
    ('/steps/1/artifact_outputs',['work/source.txt','work/config.json','work/renamed-run.json']),
    ('/steps/2/artifact_inputs',['work/source.txt','work/renamed-run.json'])])
def test_collateral_changes_rejected_atomically(path, after):
    base = example(); before = base.model_dump()
    with pytest.raises(ValueError, match='Graph repair scope'):
        validate_graph_patch(base, edits(base, {path: after}))
    assert base.model_dump() == before


def test_new_conflict_output_path_can_flow_to_existing_consumers():
    base = example()
    change = edits(base, {'/steps/1/artifact_outputs': ['work/v2/source.txt','work/v2/config.json','work/run.json'],
                         '/steps/2/artifact_inputs': ['work/v2/source.txt','work/run.json']})
    validate_graph_patch(base, change)
    validate_contract(apply_plan_patch(base, change), require=True)


def test_casefold_and_parent_path_overlap_use_same_validator_rules():
    base = example()
    base.steps[0].artifact_outputs = ['Work/SOURCE.txt', 'work/config.json','work/inventory.json']
    base.steps[1].artifact_inputs[0] = 'Work/SOURCE.txt'
    context = graph_repair_context(base)
    assert any(issue['kind']=='overlapping_producers' for issue in context['issues'])
    base.steps[0].artifact_outputs = ['work']
    base.steps[1].artifact_inputs[0] = 'work/source.txt'
    assert graph_repair_context(base)['scope_enforced']


def test_witness_count_limit_does_not_claim_exhaustive_scope():
    base = example()
    for step in base.steps[:2]:
        step.artifact_outputs = [f'work/{i}.txt' for i in range(20)]
    base.steps[1].artifact_inputs = ['work/0.txt']
    base.steps.insert(2, base.steps[1].model_copy(deep=True))
    context = graph_repair_context(base)
    assert len(context['issues']) == 32 and not context['scope_enforced']
    assert not context['complete'] and '32' in context['omission_reason']


def test_unrelated_edge_cannot_be_removed_even_if_valid_new_input_added():
    base = example()
    with pytest.raises(ValueError, match='unrelated artifact'):
        validate_graph_patch(base, edits(base, {'/steps/2/artifact_inputs': ['work/source.txt','work/inventory.json']}))


def test_analysis_checks_scope_before_checkpoint_or_whole_plan_save(project, monkeypatch):
    from study import engine, assistance, db
    base = example()
    calls = []
    def generate(self, prompt, schema):
        calls.append(schema)
        if schema is PlanDraft:
            return base
        assert 'ownership witnesses' in prompt
        return edits(base, {'/steps/2/artifact_outputs': ['work/unrelated-renamed.json']})
    monkeypatch.setattr(assistance.BudgetedModel, 'generate', generate)
    with pytest.raises(ValueError, match='Graph repair scope'):
        engine.analyse(project['id'])
    assert len(calls) == 3
    assert not db.project(project['id'])['steps'] and not db.project(project['id'])['plan']
    assert not db.query("SELECT id FROM events WHERE kind='plan_patch_applied'")


def test_size_limits_are_explicit_and_never_exhaustive():
    base = example()
    for i in range(10):
        base.steps.append(Step(title='Large',kind='write',instruction='fixture',expected='files',
                               artifact_outputs=[f'outputs/{i}/{j}.txt' for j in range(100)]))
    context = graph_repair_context(base)
    assert not context['complete'] and not context['scope_enforced'] and context['omission_reason']
    base = example()
    base.steps[1].artifact_outputs += ['work/source.txt'] * 40
    # A distinct local duplicate-list error retains its own repair workflow.
    assert graph_repair_context(base) is None


def test_valid_or_other_invalid_graph_does_not_override_semantic_repair():
    base = example()
    base.steps[0].artifact_outputs = ['work/inventory.json']
    base.steps[1].artifact_inputs = ['work/inventory.json']
    assert graph_repair_context(base) is None and graph_prompt(base) == ''
    validate_graph_patch(base, edits(base, {'/steps/2/dependencies':['numpy']}))
    base.steps[1].artifact_inputs = ['unknown/source.txt']
    assert graph_repair_context(base) is None


def test_replan_signal_remains_stop_not_a_patch():
    base = example()
    p = PlanPatch(base_sha256=draft_hash(base),requires_replan=True,explanation='Stop',edits=[])
    validate_graph_patch(base, p)
    from study.plan_patches import PlanReplanRequired
    with pytest.raises(PlanReplanRequired): apply_plan_patch(base, p)


def test_prompt_contains_witnesses_not_controller_authored_replacement():
    text = graph_prompt(example())
    assert 'zero-based' in text and 'overwritten_input' in text and 'affected_fields' in text
    assert 'before_json' not in text and 'after_json' not in text
    assert 'Max6' in text
