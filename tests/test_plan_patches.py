import copy
import json

import pytest

from study.schemas import PlanDraft
from study.plan_patches import PlanPatch, PlanEdit, PlanReplanRequired, apply_plan_patch, draft_hash, save_plan_patch


def base_plan(plan):
    return PlanDraft.model_validate({k: v for k, v in plan.items() if k != 'sources'})


def patch(base, path='/steps/0/dependencies', before=None, after=None):
    return PlanPatch(base_sha256=draft_hash(base), requires_replan=False, explanation='Declared dependency repair',
        edits=[PlanEdit(op='replace', path=path, before_json=json.dumps([] if before is None else before),
                        after_json=json.dumps(['numpy'] if after is None else after))])


def test_one_field_changes_other_fields_are_canonically_unchanged(plan):
    base = base_plan(plan)
    data = copy.deepcopy(base.model_dump())
    result = apply_plan_patch(base, patch(base))
    assert base.model_dump() == data
    data['steps'][0]['dependencies'] = ['numpy']
    assert result.model_dump() == data


def test_stale_base_and_before_value_are_rejected_without_mutation(plan):
    base = base_plan(plan); original = base.model_dump()
    stale = patch(base); stale.base_sha256 = '0' * 64
    with pytest.raises(ValueError, match='base_sha256'):
        apply_plan_patch(base, stale)
    with pytest.raises(ValueError, match='before_json'):
        apply_plan_patch(base, patch(base, before=['not actual']))
    assert base.model_dump() == original


@pytest.mark.parametrize('path', ['/sources', '/conflict_evidence', '/artifact_contract_version', '/steps',
    '/steps/0', '/steps/00/dependencies', '/steps/-1/dependencies', '/steps/99/dependencies', '/steps/0/__class__'])
def test_provenance_and_noncanonical_or_structural_paths_are_forbidden(plan, path):
    base = base_plan(plan)
    with pytest.raises(ValueError, match='path'):
        apply_plan_patch(base, patch(base, path=path))


def test_all_edits_are_atomic_and_schema_validated(plan):
    base = base_plan(plan); original = base.model_dump()
    changes = patch(base)
    changes.edits.append(PlanEdit(op='replace', path='/steps/0/language', before_json='"python"', after_json='"unknown"'))
    with pytest.raises(ValueError):
        apply_plan_patch(base, changes)
    assert base.model_dump() == original


def test_duplicate_paths_and_noop_do_not_claim_progress(plan):
    base = base_plan(plan); changes = patch(base)
    changes.edits.append(changes.edits[0].model_copy())
    with pytest.raises(ValueError, match='Повторний'):
        apply_plan_patch(base, changes)
    with pytest.raises(ValueError, match='не змінює'):
        apply_plan_patch(base, patch(base, after=[]))


@pytest.mark.parametrize('value', ['["numpy"', 'NaN'])
def test_partial_or_nonfinite_values_are_not_repaired_locally(plan, value):
    base = base_plan(plan); changes = patch(base)
    changes.edits[0].after_json = value
    with pytest.raises(ValueError, match='JSON'):
        apply_plan_patch(base, changes)


def test_explicit_structural_replan_stops_without_changing_base(plan):
    base = base_plan(plan)
    p = PlanPatch(base_sha256=draft_hash(base), requires_replan=True, explanation='Need remove/reorder steps', edits=[])
    with pytest.raises(PlanReplanRequired):
        apply_plan_patch(base, p)


def test_single_step_insert_supports_missing_report_without_rewriting_other_steps(plan):
    base = base_plan(plan)
    step = {'kind': 'report', 'title': 'Report', 'instruction': 'Render', 'expected': 'DOCX/PDF'}
    p = PlanPatch(base_sha256=draft_hash(base), requires_replan=False, explanation='Missing final report',
        edits=[PlanEdit(op='insert_step', path=f'/steps/{len(base.steps)}', before_json='null', after_json=json.dumps(step))])
    result = apply_plan_patch(base, p)
    assert result.steps[:-1] == base.steps and result.steps[-1].kind == 'report'


def test_checkpoint_is_unapproved_and_idempotent(project, plan):
    base = base_plan(plan); changes = patch(base); result = apply_plan_patch(base, changes)
    one = save_plan_patch(project['id'], base, changes, result, None)
    assert save_plan_patch(project['id'], base, changes, result, None) == one
    record = json.loads(one.read_text(encoding='utf-8'))
    assert 'unapproved' in record['state']
    assert record['result_sha256'] == draft_hash(result)
    from study import db
    p = db.project(project['id'])
    assert not p['plan'] and not p['steps'] and p['approved_hash'] is None


@pytest.mark.parametrize('stop_kind', ['truncated', 'replan'])
def test_analysis_does_not_retry_truncated_or_explicit_replan_patch(project, plan, monkeypatch, stop_kind):
    from study import engine, assistance, db
    from study.providers import ModelOutputTruncated
    base = base_plan(plan)
    base.steps[0].dependencies = ['python']
    calls = []
    def generate(self, prompt, schema):
        calls.append(schema)
        if schema is PlanDraft:
            return base
        assert schema is PlanPatch
        if stop_kind == 'truncated':
            raise ModelOutputTruncated('Paid patch incomplete; do not replay')
        return PlanPatch(base_sha256=draft_hash(base), requires_replan=True, explanation='Needs separate structural planning', edits=[])
    monkeypatch.setattr(assistance.BudgetedModel, 'generate', generate)
    exception = ModelOutputTruncated if stop_kind == 'truncated' else PlanReplanRequired
    with pytest.raises(exception):
        engine.analyse(project['id'])
    assert calls == [PlanDraft, PlanPatch]
    assert not db.project(project['id'])['steps']
    assert not db.query('SELECT id FROM cloud_requests')


def test_patch_cannot_use_bool_as_integer_compare_value(plan):
    base = base_plan(plan)
    changes = patch(base, path='/steps/0/timeout_seconds')
    changes.edits[0].before_json = 'true'
    changes.edits[0].after_json = '600'
    with pytest.raises(ValueError, match='before_json'):
        apply_plan_patch(base, changes)


def test_combined_patch_size_is_bounded_and_no_base_is_mutated(plan):
    base = base_plan(plan)
    changes = patch(base)
    changes.edits = [PlanEdit(op='replace', path='/steps/0/instruction', before_json=json.dumps('x' * 5800),
                             after_json=json.dumps('y' * 5800)),
                     PlanEdit(op='replace', path='/steps/0/title', before_json=json.dumps('x' * 5800), after_json='"small"')]
    with pytest.raises(ValueError, match='14000'):
        apply_plan_patch(base, changes)


def test_patch_validation_failure_retains_tail_of_original_review(project, plan, monkeypatch, patch_reply):
    from study import engine, assistance
    from study.schemas import Assessment
    base = base_plan(plan)
    from study.schemas import Step
    base.steps.append(Step(title='Report', kind='report', instruction='Render', expected='DOCX/PDF'))
    calls = []; reviews = []
    def generate(self, prompt, schema):
        calls.append(schema)
        if schema is PlanDraft:
            return base
        if len(calls) == 2:
            raise ValueError('INVALID_PATCH_VALUE')
        assert 'IMPORTANT_REVIEW_TAIL' in prompt and 'INVALID_PATCH_VALUE' in prompt
        return patch_reply(prompt)
    def review(self, prompt, schema, **kwargs):
        reviews.append(prompt)
        return Assessment(satisfied=len(reviews) > 1, explanation='Issue. ' * 380 + 'IMPORTANT_REVIEW_TAIL')
    monkeypatch.setattr(assistance.BudgetedModel, 'available', lambda self: True)
    monkeypatch.setattr(assistance.BudgetedModel, 'generate', generate)
    monkeypatch.setattr(assistance.BudgetedModel, 'remote_with_fallback', review)
    engine.analyse(project['id'])
    assert calls == [PlanDraft, PlanPatch, PlanPatch] and len(reviews) == 2
