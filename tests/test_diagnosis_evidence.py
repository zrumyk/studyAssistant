import json
import pytest
from study import config, db, engine, assistance, input_inspection
from study.schemas import PythonProgram, RepairAdvice, PythonRepair
from study.codegen import repair_action, save_candidate


@pytest.mark.parametrize('facts', [
    'Observed SQL: CREATE TABLE readings (id INTEGER PRIMARY KEY, value REAL) STRICT',
    'Observed array header: dtype=float32, shape=[8, 3]; labels are separate int64',
    'Observed JSON: {"enabled": false, "threshold": 0.25}; boolean is not a string',
])
def test_diagnosis_receives_same_observed_inputs_and_approved_criterion(project, plan, monkeypatch, facts):
    config.save_settings({'cloud_consent_mode': 'budgeted'})
    engine.save_plan(project['id'], plan)
    monkeypatch.setattr(engine, 'context', lambda *a, **k: '')
    monkeypatch.setattr(input_inspection, 'inspect_inputs', lambda *a, **k: facts)
    seen = []
    def generate(self, prompt, schema):
        seen.append(schema)
        if schema is RepairAdvice:
            assert facts in prompt and plan['steps'][0]['expected'] in prompt
            assert 'producer І checker' in prompt and 'не розширюй допуски' in prompt
            return RepairAdvice(cause='Evidence-dependent diagnosis', changes=[], preserve=[])
        assert facts in prompt and 'Evidence-dependent diagnosis' in prompt
        return PythonProgram(main_code='print(4)', checker_code='value=2\nassert value+value==4', explanation='fixture')
    monkeypatch.setattr(assistance.BudgetedModel, 'generate', generate)
    engine.prepare_action(project['id'], db.project(project['id'])['steps'][0], '{"run_id":"fixture", "log":"AssertionError"}')
    assert seen == [RepairAdvice, PythonProgram]


def test_schema_beyond_sample_rows_reaches_inspection_without_executing_sql(project, monkeypatch):
    config.save_settings({'cloud_consent_mode': 'budgeted'})
    root=config.project_dir(project['id'])/'workspace';root.mkdir()
    ddl='CREATE TABLE readings (id INTEGER PRIMARY KEY, value REAL) STRICT'
    payload={'records':[{'type':'index','sql':None} for _ in range(12)]+[{'sql':ddl}], 'padding':'x'*6000}
    (root/'inventory.json').write_text(json.dumps(payload), 'utf-8')
    monkeypatch.setattr(assistance.BudgetedModel, 'generate', lambda *a, **k: input_inspection.InputInspection(paths=['inventory.json']))
    text=input_inspection.inspect_inputs(project['id'],'s',{'instruction':'Read schema'},root)
    assert ddl in text and len(text)<13000
    assert not (root/'readings').exists()


@pytest.mark.parametrize('good,bad,old,new', [(8,9,"'8'",'8'), (False,True,"'false'",'False')])
def test_model_authored_checker_type_correction_preserves_negative_detection(project, plan, good, bad, old, new):
    engine.save_plan(project['id'], plan);p=db.project(project['id']);sid=p['steps'][0]['id']
    root=config.project_dir(project['id'])/'workspace';root.mkdir()
    source='def identity(value):\n    return value\n'
    checker='assert observed == '+old+'\n'
    action=PythonProgram(main_code=source, checker_code=checker, explanation='fixture').as_action()
    save_candidate(project['id'],sid,p['revision'],'fixture',action)
    repair=PythonRepair(edits=[{'path':'check_current_step.py','before':old,'after':new}],explanation='Observed type correction')
    fixed=repair_action(project['id'],sid,p['revision'],root,repair)
    assert fixed.files==action.files and fixed.expected_files==action.expected_files
    with pytest.raises(AssertionError):exec(checker, {'observed':good})
    exec(fixed.checks[0].content, {'observed':good})
    with pytest.raises(AssertionError):exec(fixed.checks[0].content, {'observed':bad})
    assert not list(root.iterdir())
