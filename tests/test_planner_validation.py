import copy
import pytest
from pydantic import ValidationError
from study import engine, db
from study.schemas import Plan,PlanDraft
from study.schemas import Action
from study.runtime import dependencies


def test_non_code_language_can_be_unused_but_code_language_cannot_be_null():
    from study.schemas import Step
    base={'title':'Step','instruction':'Do task','expected':'Files','language':None}
    for kind in ('manual','fetch','write','report','diagram'):
        assert Step.model_validate(base|{'kind':kind}).language=='python'
    with pytest.raises(ValidationError):
        Step.model_validate(base|{'kind':'code'})


def test_unused_download_cap_does_not_block_local_steps_but_fetch_requires_positive_cap():
    from study.schemas import Step
    base={'title':'Step','instruction':'Do task','expected':'Files','max_download_bytes':0}
    for kind in ('code','manual','write','report','diagram'):
        assert Step.model_validate(base|{'kind':kind}).max_download_bytes==50_000_000
    with pytest.raises(ValidationError):
        Step.model_validate(base|{'kind':'fetch'})


def test_old_assistant_drafts_do_not_become_user_clarifications(project,plan,monkeypatch):
    data=copy.deepcopy(plan);data.pop('sources')
    data['steps'].append({'title':'Звіт','instruction':'Зібрати','kind':'report','expected':'DOCX/PDF'})
    db.message(project['id'],'assistant','DO_NOT_TREAT_ASSISTANT_DRAFT_AS_USER')
    db.message(project['id'],'user','REAL_USER_CLARIFICATION')
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    def generate(self,prompt,schema):
        assert 'REAL_USER_CLARIFICATION' in prompt
        assert 'DO_NOT_TREAT_ASSISTANT_DRAFT_AS_USER' not in prompt
        return PlanDraft.model_validate(data)
    monkeypatch.setattr(engine.OllamaProvider,'generate',generate)
    engine.analyse(project['id'])
    assert db.project(project['id'])['status']=='awaiting_approval'


def test_analysis_supplies_existing_short_task_in_first_request(project,plan,monkeypatch):
    from study import config,documents
    data=copy.deepcopy(plan);data.pop('sources')
    data['steps'].append({'title':'Звіт','instruction':'Зібрати','kind':'report','expected':'DOCX/PDF'})
    path=config.DATA/'additional.txt'
    path.write_text('Імена шарів креслення обирає виконавець. EVIDENCE_FROM_EXISTING_FILE','utf-8')
    documents.import_file(path,'task',project['subject'])
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    calls=[]
    def generate(self,prompt,schema):
        calls.append(prompt)
        draft=copy.deepcopy(data)
        assert 'EVIDENCE_FROM_EXISTING_FILE' in prompt
        return PlanDraft.model_validate(draft)
    monkeypatch.setattr(engine.OllamaProvider,'generate',generate)
    engine.analyse(project['id'])
    result=db.project(project['id'])
    assert len(calls)==1
    assert result['plan']['missing']==[]
    assert result['plan']['sources'] and result['approved_hash'] is None


def test_generated_lab_diagram_cannot_use_workflow_tool(plan):
    candidate = copy.deepcopy(plan)
    candidate['steps'][0]['kind'] = 'diagram'
    candidate['steps'].append({'title':'Звіт','instruction':'Зібрати','kind':'report','expected':'DOCX/PDF'})
    with pytest.raises(ValueError, match='лише схему самого плану'):
        engine.validate_generated_plan(Plan.model_validate(candidate))


@pytest.mark.parametrize('filename',['drawing_preview.pdf','figure.svg','plot.pdf'])
def test_code_can_create_graphic_documents_without_duplicating_final_report(plan,filename):
    data=copy.deepcopy(plan)
    data['steps'][0].update(title='Графічний результат',instruction='Побудувати зображення',expected=filename)
    data['steps'].append({'title':'Звіт','instruction':'Зібрати звіт','kind':'report','expected':'DOCX/PDF'})
    engine.validate_generated_plan(Plan.model_validate(data))


def test_plan_repair_receives_all_bounded_review_issues(project,plan,monkeypatch,patch_reply):
    from study import config,assistance
    from study.schemas import Assessment
    valid=copy.deepcopy(plan)
    valid['steps'].append({'title':'Звіт','instruction':'Зібрати','kind':'report','expected':'DOCX/PDF'})
    config.save_settings({'cloud_consent_mode':'budgeted'})
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    generated=[];reviews=[]
    def generate(self,prompt,schema):
        generated.append(prompt)
        if len(generated)>1:
            assert 'IMPORTANT_TAIL_REQUIREMENT' in prompt
            return patch_reply(prompt)
        return Plan.model_validate(valid | {"artifact_contract_version":1})
    def remote(self,prompt,schema):
        reviews.append(prompt)
        return Assessment(satisfied=len(reviews)>1,explanation='Earlier issue. '*70+'IMPORTANT_TAIL_REQUIREMENT')
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    monkeypatch.setattr(assistance.BudgetedModel,'remote',remote)
    engine.analyse(project['id'])
    assert len(generated)==2
    assert db.project(project['id'])['status']=='awaiting_approval'


def test_plan_repair_preserves_actual_rejected_plan_and_controller_setup_rules(project,plan,monkeypatch,patch_reply):
    from study import config,assistance
    from study.schemas import Assessment
    valid=copy.deepcopy(plan)
    valid['steps'][0]['title']='ACTUAL_PREVIOUS_PLAN_MARKER'
    valid['steps'].append({'title':'Звіт','instruction':'Зібрати','kind':'report','expected':'DOCX/PDF'})
    config.save_settings({'cloud_consent_mode':'budgeted'})
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    generated=[];reviews=[]
    def generate(self,prompt,schema):
        generated.append(prompt)
        assert 'КОНТРОЛЕР Docker' in prompt and 'venv/virtualenv' in prompt
        if len(generated)>1:
            assert 'Фактичний попередній план' in prompt
            assert 'ACTUAL_PREVIOUS_PLAN_MARKER' in prompt and 'FIX_PACKAGE_SETUP' in prompt
            return patch_reply(prompt)
        return Plan.model_validate(valid | {"artifact_contract_version":1})
    def remote(self,prompt,schema):
        reviews.append(prompt)
        return Assessment(satisfied=len(reviews)>1,explanation='FIX_PACKAGE_SETUP')
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    monkeypatch.setattr(assistance.BudgetedModel,'remote',remote)
    engine.analyse(project['id'])
    assert len(generated)==2 and len(reviews)==2
    p=db.project(project['id'])
    assert p['status']=='awaiting_approval' and not p['approved_hash']


def test_automatic_plan_audit_rejects_invented_answers(project, plan, monkeypatch,patch_reply):
    from study import config, assistance
    from study.schemas import Assessment

    valid = copy.deepcopy(plan)
    valid["steps"].append(
        {
            "title": "Звіт",
            "instruction": "Зібрати",
            "kind": "report",
            "expected": "DOCX/PDF",
        }
    )
    config.save_settings({"cloud_consent_mode": "budgeted"})
    monkeypatch.setattr(
        engine.OllamaProvider, "generate", lambda *a, **k: Plan.model_validate(valid)
    )
    monkeypatch.setattr(engine, "context", lambda *a, **k: "")
    seen = [];reviews=[];drafts=[]

    def remote(self, prompt, schema):
        from study.plan_patches import PlanPatch
        seen.append(prompt)
        if schema is PlanPatch:
            drafts.append(prompt)
            assert 'вигадані відповіді' in prompt
            return patch_reply(prompt)
        if schema in (Plan,PlanDraft):
            drafts.append(prompt)
            if reviews:assert "вигадані відповіді" in prompt
            return Plan.model_validate(valid | {"artifact_contract_version":1})
        assert "Не розв’язуй завдання" in prompt
        reviews.append(prompt)
        return Assessment(satisfied=len(reviews) > 1, explanation="вигадані відповіді")

    monkeypatch.setattr(assistance.BudgetedModel, "remote", remote)
    engine.analyse(project["id"])
    assert len(reviews)==2 and drafts and 'вигадані відповіді' in drafts[-1]
    assert db.project(project["id"])["status"] == "awaiting_approval"


def test_analysis_repairs_tool_plan_without_approving_it(project, plan, monkeypatch,patch_reply):
    valid = copy.deepcopy(plan)
    valid["steps"].append(
        {
            "title": "Звіт",
            "instruction": "Зібрати звіт",
            "kind": "report",
            "expected": "DOCX і PDF",
        }
    )
    wrong_packages = copy.deepcopy(valid)
    wrong_packages["steps"][0]["dependencies"] = ["python"]
    replies = iter([plan, wrong_packages, valid])
    calls = []

    def generate(self, prompt, schema):
        calls.append(prompt)
        if len(calls) == 2:
            return patch_reply(prompt, insert_step={**valid['steps'][-1], 'dependencies': ['python']})
        if len(calls) == 3:
            return patch_reply(prompt, path='/steps/1/dependencies', value=[])
        return Plan.model_validate(next(replies) | {"artifact_contract_version":1})

    monkeypatch.setattr(engine.OllamaProvider, "generate", generate)
    monkeypatch.setattr(engine, "context", lambda *args, **kwargs: "")
    engine.analyse(project["id"])
    saved = db.project(project["id"])
    assert len(calls) == 3
    assert "kind=report" in calls[1]
    assert "python" in calls[2]
    assert saved["approved_hash"] is None
    assert saved["status"] == "awaiting_approval"
    assert saved["plan"]["steps"][-1]["kind"] == "report"
    assert all(step["status"] == "pending" for step in saved["steps"])


@pytest.mark.parametrize("package", ["python", "csv", "statistics"])
def test_interpreter_and_standard_modules_are_not_installed(package):
    with pytest.raises(ValueError):
        dependencies("python", [package])


def test_model_must_explicitly_report_missing_data_and_conflicts(plan):
    incomplete = {k: v for k, v in plan.items() if k not in ("missing", "conflicts")}
    with pytest.raises(ValidationError):
        Plan.model_validate(incomplete)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"expected": "Файл report.docx"}, "kind=report"),
        ({"expected": "Змінні з числами"}, "контейнеру"),
        ({"instruction": "Завантажити бібліотеки numpy та matplotlib"}, "numpy"),
    ],
)
def test_generated_plan_respects_real_tool_boundaries(plan, change, reason):
    plan["steps"][0].update(change)
    plan["steps"].append(
        {
            "title": "Звіт",
            "instruction": "Зібрати звіт",
            "kind": "report",
            "expected": "DOCX/PDF",
        }
    )
    with pytest.raises(ValueError, match=reason):
        engine.validate_generated_plan(Plan.model_validate(plan))


def test_print_only_checker_is_rejected_before_execution(project, plan, monkeypatch):
    engine.save_plan(project["id"], plan)
    action = Action(
        files=[{"path": "main.py", "content": "print(4)"}],
        entrypoint="main.py",
        explanation="Перевірка",
        checks=[{"path": "check_results.py", "content": "print('all good')"}],
    )
    monkeypatch.setattr(
        engine.OllamaProvider, "generate", lambda *args, **kwargs: action
    )
    monkeypatch.setattr(engine, "context", lambda *args, **kwargs: "")
    step = db.project(project["id"])["steps"][0]
    with pytest.raises(ValueError, match="assert"):
        engine.prepare_action(project["id"], step, "")


def test_repair_receives_the_actual_failed_source(project, plan, monkeypatch):
    from study import config

    engine.save_plan(project["id"], plan)
    root = config.project_dir(project["id"]) / "workspace"
    root.mkdir()
    (root / "main.py").write_text("raise RuntimeError('UNIQUE_OLD_CODE')", "utf-8")
    from study.codegen import save_candidate
    from study.schemas import ActionCheckpoint
    p=db.project(project['id']);sid=p['steps'][0]['id']
    save_candidate(p['id'],sid,p['revision'],'local',Action(files=[{'path':'main.py','content':"raise RuntimeError('UNIQUE_OLD_CODE')"}],explanation='current failed step'))
    checkpoint=config.project_dir(p['id'])/'codegen'/sid/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'));saved.written_to_workspace=True
    checkpoint.write_text(saved.model_dump_json(),'utf-8')

    def generate(self, prompt, schema):
        assert "UNIQUE_OLD_CODE" in prompt
        return Action(explanation="Source received", requires_user=True)

    monkeypatch.setattr(engine.OllamaProvider, "generate", generate)
    monkeypatch.setattr(engine, "context", lambda *args: "")
    step = db.project(project["id"])["steps"][0]
    with pytest.raises(ValueError,match='requires_user=false'):
        engine.prepare_action(
            project["id"], step, 'File "/work/main.py", line 1\nRuntimeError'
        )


def test_static_rejection_is_saved_for_the_next_repair(project, plan, monkeypatch):
    from study import config

    engine.save_plan(project["id"], plan)
    root = config.project_dir(project["id"]) / "workspace"
    root.mkdir()
    (root / "main.py").write_text("print('OLD_SOURCE')", "utf-8")
    calls = []

    def generate(self, prompt, schema):
        calls.append(prompt)
        if len(calls) == 1:
            return Action(
                files=[{"path": "main.py", "content": "print(MISSING_CANDIDATE)"}],
                explanation="candidate",
            )
        assert "print(MISSING_CANDIDATE)" in prompt
        assert "OLD_SOURCE" not in prompt
        return Action(explanation="received candidate", requires_user=True)

    monkeypatch.setattr(engine.OllamaProvider, "generate", generate)
    monkeypatch.setattr(engine, "context", lambda *args: "")
    step = db.project(project["id"])["steps"][0]
    with pytest.raises(ValueError, match="MISSING_CANDIDATE"):
        engine.prepare_action(project["id"], step, "")
    assert (root / "main.py").read_text("utf-8") == "print('OLD_SOURCE')"
    with pytest.raises(ValueError,match='requires_user=false'):
        engine.prepare_action(
            project["id"], step, "Статичні помилки main.py: undefined name"
        )


@pytest.mark.parametrize('truncated',[False,True])
def test_short_code_uses_one_response_and_only_truncation_needs_modular_fallback(project,plan,monkeypatch,truncated):
    from study import assistance,codegen
    from study.providers import ModelOutputTruncated
    from study.schemas import PythonAction,PythonProgram
    plan['steps'][0]['dependencies']=['opencv-python']
    engine.save_plan(project['id'],plan)
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    action=PythonAction(files=[{'path':'main.py','content':'print(2+2)'}],checks=[{'path':'check_result.py','content':'value=2\nassert value+value==4'}],expected_files=[],explanation='Short implementation')
    calls=[];fallback=[]
    def generate(self,prompt,schema):
        calls.append(schema)
        if truncated:raise ModelOutputTruncated('response truncated')
        return action
    def bundle(*a,**k):
        fallback.append(1);return action
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    monkeypatch.setattr(codegen,'generate_bundle',bundle)
    step=db.project(project['id'])['steps'][0]
    assert engine.prepare_action(project['id'],step,'')==action
    assert calls==[PythonProgram]
    assert len(fallback)==int(truncated)


def test_simple_program_has_one_implementation_and_independent_checker(project,plan,monkeypatch):
    from study import assistance
    from study.schemas import PythonProgram
    engine.save_plan(project['id'],plan)
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    def generate(self,prompt,schema):
        assert schema is PythonProgram
        assert 'Поверни структуру Action' not in prompt
        assert 'manual_instructions' not in prompt
        return PythonProgram(main_code='print(4)',checker_code='value=2\nassert value+value==4',expected_files=[],explanation='done')
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    action=engine.prepare_action(project['id'],db.project(project['id'])['steps'][0],'')
    assert [f.path for f in action.files]==['main.py']
    assert [f.path for f in action.checks]==['check_current_step.py']
    assert not action.requires_user


def test_simple_program_cannot_offer_its_source_as_a_new_runtime_result(project,plan,monkeypatch):
    from study import assistance
    from study.schemas import PythonProgram
    engine.save_plan(project['id'],plan)
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    monkeypatch.setattr(assistance.BudgetedModel,'generate',lambda *a,**k:PythonProgram(main_code='print(4)',checker_code='value=2\nassert value+value==4',expected_files=['main.py'],explanation='bad outputs'))
    with pytest.raises(ValueError,match='лише створені'):
        engine.prepare_action(project['id'],db.project(project['id'])['steps'][0],'')


@pytest.mark.parametrize('previous,expected',[
    ('',None),('Static syntax error',None),
    ('{"run_id":"failed-runtime","log":"AssertionError"}',True),
    ('{"preflight": true, "conflicts": []}',True),
])
def test_runtime_repair_uses_reasoning_even_after_public_retry_reset(project,plan,monkeypatch,previous,expected):
    from study import assistance,config
    from study.schemas import PythonProgram,RepairAdvice
    config.save_settings({'cloud_consent_mode':'budgeted'})
    engine.save_plan(project['id'],plan)
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    def generate(self,prompt,schema):
        if schema is RepairAdvice:
            assert expected is True and self.thinking_override is True
            return RepairAdvice(cause='Verified diagnosis marker',changes=['Repair fault'],preserve=['Keep checks'])
        assert self.thinking_override is None
        if expected:assert 'Verified diagnosis marker' in prompt
        return PythonProgram(main_code='print(4)',checker_code='value=2\nassert value+value==4',expected_files=[],explanation='done')
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    step=db.project(project['id'])['steps'][0]
    assert step['attempts']==0
    engine.prepare_action(project['id'],step,previous)
