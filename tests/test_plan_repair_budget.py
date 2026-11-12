import copy

import pytest

from study import assistance, cloud, config, db
from study.artifact_contracts import validate_contract
from study.plan_repair import repair_prompt
from study.runtime import RUNTIMES
from study.schemas import Plan, PlanDraft, Assessment


@pytest.mark.parametrize('language', list(RUNTIMES))
def test_consumed_driver_is_rejected_for_each_runtime(language):
    entry = RUNTIMES[language][1]
    plan = Plan.model_validate({'artifact_contract_version': 1, 'goal': 'Test', 'requirements': [],
        'missing': [], 'conflicts': [], 'report_sections': [], 'steps': [
            {'title': 'Source', 'instruction': 'Create', 'kind': 'write', 'expected': 'Source', 'artifact_outputs': [entry]},
            {'title': 'Consumer', 'instruction': 'Run', 'kind': 'code', 'language': language, 'expected': 'Data', 'artifact_inputs': [entry]},
        ]})
    with pytest.raises(ValueError, match='entrypoint'):
        validate_contract(plan)
    # A module in another directory is an ordinary consumed file.
    plan.steps[0].artifact_outputs = ['work/source/' + entry]
    plan.steps[1].artifact_inputs = ['work/source/' + entry]
    validate_contract(plan)
    # Report-only consumption does not need a code driver.
    plan.steps[0].artifact_outputs = [entry]
    plan.steps[1].artifact_inputs = [entry]
    plan.steps[1].kind = 'report'
    validate_contract(plan)


def test_budget_floor_stops_before_quote_send_or_reserve(project, monkeypatch):
    config.save_settings({'cloud_consent_mode': 'budgeted'})
    monkeypatch.setattr(cloud, 'budgeted_output_limit', lambda *a: 1784)
    monkeypatch.setattr(cloud, 'quote', lambda *a, **k: pytest.fail('No quote below floor'))
    model = assistance.BudgetedModel(project['id'], None, 'plan', min_output_tokens=4096)
    with pytest.raises(ValueError, match='доступно1784'):
        model.remote('long prompt', PlanDraft)
    assert not db.query('SELECT id FROM cloud_requests')


@pytest.mark.parametrize('value', [True, 0, 255, 4096.0])
def test_invalid_output_floor_is_not_accepted(value):
    with pytest.raises(ValueError):
        assistance.BudgetedModel('test', None, 'test', min_output_tokens=value)


def test_optional_floor_does_not_change_existing_callers(project, monkeypatch):
    config.save_settings({'cloud_consent_mode': 'budgeted'})
    monkeypatch.setattr(cloud, 'budgeted_output_limit', lambda *a: 1784)
    def quote(*a, **k):
        assert k['max_output_tokens'] == 1784
        raise RuntimeError('Reached normal quote')
    monkeypatch.setattr(cloud, 'quote', quote)
    with pytest.raises(RuntimeError, match='normal quote'):
        assistance.BudgetedModel(project['id'], None, 'other').remote('prompt', Assessment)


def test_repair_prompt_preserves_data_without_accumulating_old_boilerplate(plan):
    draft = PlanDraft.model_validate({k: v for k, v in plan.items() if k != 'sources'})
    before = copy.deepcopy(draft.model_dump())
    kwargs = dict(task='TASK_MARKER', variant='7', platform_facts='FACTS_MARKER',
                  sources='COMPLETE_SOURCE_MARKER', draft=draft, error='ERROR_MARKER',
                  clarifications='USER_CLARIFICATION', report_profiles='REPORT_PROFILE', subject='SUBJECT')
    text = repair_prompt(**kwargs)
    assert all(marker in text for marker in ['TASK_MARKER', 'FACTS_MARKER', 'COMPLETE_SOURCE_MARKER', 'ERROR_MARKER'])
    assert text.count('COMPLETE_SOURCE_MARKER') == 1
    assert all(marker in text for marker in ['USER_CLARIFICATION', 'REPORT_PROFILE', 'SUBJECT'])
    assert 'КОНТРОЛЕР Docker' in text and 'venv/virtualenv' in text
    assert draft.model_dump() == before
    assert repair_prompt(**kwargs) == text


def test_reviewer_facts_distinguish_declared_packages_and_prior_outputs():
    import json
    from study.plan_review import plan_spec_facts
    data = {'goal': 'Test', 'requirements': [], 'missing': [], 'conflicts': [], 'report_sections': [], 'steps': [
        {'title': 'Producer', 'kind': 'code', 'language': 'typescript', 'instruction': 'Run', 'expected': 'Summary',
         'dependencies': ['mocha', '@types/mocha'], 'artifact_outputs': ['work/summary.json', 'main.ts']},
        {'title': 'Consumer', 'kind': 'code', 'language': 'typescript', 'instruction': 'Read', 'expected': 'Final',
         'artifact_inputs': ['work/summary.json', 'main.ts'], 'artifact_outputs': ['work/final.json']},
    ]}
    facts = json.loads(plan_spec_facts(Plan.model_validate(data)))['steps']
    assert facts[0]['dependencies'] == ['mocha', '@types/mocha']
    assert facts[0]['inputs'] == []
    assert facts[1]['inputs'][0]['prior_producer'] == 1
    assert facts[1]['consumes_own_entrypoint'] is True
    data['steps'][1]['artifact_inputs'] = ['work/summary.json']
    changed = json.loads(plan_spec_facts(Plan.model_validate(data)))['steps']
    assert changed[1]['consumes_own_entrypoint'] is False
    data['steps'][0]['artifact_outputs'] = []
    missing = json.loads(plan_spec_facts(Plan.model_validate(data)))['steps']
    assert missing[1]['inputs'][0]['prior_producer'] is None


def test_copy_to_new_path_is_not_declared_input_overwrite():
    import json
    from study.plan_review import plan_spec_facts
    data = {'goal': 'Copy', 'requirements': [], 'missing': [], 'conflicts': [], 'report_sections': [], 'steps': [
        {'title': 'Copy', 'kind': 'code', 'instruction': 'Copy', 'expected': 'Copy',
         'artifact_inputs': ['inputs/source.zip'], 'artifact_outputs': ['work/source.zip']}]}
    plan = Plan.model_validate(data)
    facts = json.loads(plan_spec_facts(plan))['steps'][0]
    assert facts['input_output_overlap'] == []
    assert facts['inputs'][0]['imported_namespace'] is True
    plan.steps[0].artifact_outputs = ['inputs/source.zip']
    assert json.loads(plan_spec_facts(plan))['steps'][0]['input_output_overlap'] == ['inputs/source.zip']


@pytest.mark.parametrize('expected', ['Незмінні конфіги та готові source файли', 'immutable variableset config files'])
def test_immutable_file_words_are_not_process_variables(plan, expected):
    from study.engine import validate_generated_plan
    data = copy.deepcopy(plan)
    data['steps'][0]['expected'] = expected
    data['steps'].append({'title': 'Report', 'kind': 'report', 'instruction': 'Render', 'expected': 'DOCX/PDF'})
    validate_generated_plan(Plan.model_validate(data))


@pytest.mark.parametrize('expected', ['Змінні процесу з числами', 'Process variables for next step'])
def test_real_process_variables_without_persistent_files_remain_rejected(plan, expected):
    from study.engine import validate_generated_plan
    data = copy.deepcopy(plan)
    data['steps'][0]['expected'] = expected
    data['steps'].append({'title': 'Report', 'kind': 'report', 'instruction': 'Render', 'expected': 'DOCX/PDF'})
    with pytest.raises(ValueError, match='Змінні процесу'):
        validate_generated_plan(Plan.model_validate(data))
