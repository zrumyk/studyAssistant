import pytest
from study import codegen


def test_later_modules_receive_actual_producer_schema(project,monkeypatch):
    from study.schemas import FileWrite
    producer='def make_table(rows):\n    return {"rows": rows}\n'
    calls=[]
    def generate(self,prompt,schema):
        calls.append(prompt)
        if issubclass(schema,codegen.PythonBundle):
            return schema(files=[{'path':'producer.py','contract':'make_table(rows)'},{'path':'main.py','contract':'entry'}],
                          checks=[{'path':'check_table.py','contract':'check saved table'}],expected_files=[],explanation='',stdin_text='дані\n')
        return schema(content=producer if 'вміст файлу producer.py.' in prompt else 'value = 4\nassert value == 4\n')
    monkeypatch.setattr(codegen.OllamaProvider,'generate',generate)
    action=codegen.generate_bundle(project['id'],'s1','Create and verify a table.',lambda:False)
    assert action.stdin_text=='дані\n'
    assert producer in calls[-1]
    assert 'точні шляхи, вкладені ключі' in calls[-1]
    count=len(calls)
    assert codegen.generate_bundle(project['id'],'s1','Create and verify a table.',lambda:False)==action
    assert len(calls)==count
    limited=codegen.generated_source_context({'producer.py':FileWrite(path='producer.py',content=producer)},budget=5)
    assert 'Код не вмістився: producer.py' in limited and producer not in limited


def test_incomplete_repair_uses_bounded_modules_and_preserves_candidate(project,plan,monkeypatch):
    import json
    from study import config,db,engine,assistance
    from study.providers import ModelOutputTruncated
    from study.schemas import Action,RepairAdvice
    pid=project['id'];engine.save_plan(pid,plan)
    p=db.project(pid);step=p['steps'][0];root=config.project_dir(pid)/'workspace';root.mkdir()
    action=Action(files=[{'path':'main.py','content':'print(4)'}],checks=[{'path':'check_current_step.py','content':'value=2\nassert value+value==4'}],entrypoint='main.py',explanation='generated')
    for f in action.files+action.checks:(root/f.path).write_text(f.content,'utf-8')
    codegen.save_candidate(pid,step['id'],p['revision'],'cloud',action)
    cp=config.project_dir(pid)/'codegen'/step['id']/'last-action.json'
    saved=json.loads(cp.read_text('utf-8'));saved['written_to_workspace']=True;cp.write_text(json.dumps(saved),'utf-8')
    monkeypatch.setattr(engine,'context',lambda *a,**kw:'')
    monkeypatch.setattr(engine,'cancelled',lambda *a:False)
    monkeypatch.setattr('study.prior_sources.previous_sources',lambda *a,**kw:'')
    monkeypatch.setattr('study.input_inspection.inspect_inputs',lambda *a:'')
    monkeypatch.setattr('study.error_docs.runtime_documentation',lambda *a:'')
    monkeypatch.setattr(assistance.BudgetedModel,'available',lambda self:True)
    def generate(self,prompt,schema):
        if schema is RepairAdvice:return RepairAdvice(cause='A repair is needed',changes=[],preserve=[])
        raise ModelOutputTruncated('Patch output incomplete')
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    calls=[]
    def bundle(*a,**kw):calls.append(a);return action
    monkeypatch.setattr('study.bounded_repair.repair_modules',bundle)
    previous='{"preflight": true, "conflicts": []}'
    assert engine.prepare_action(pid,step,previous)==action
    assert len(calls)==1 and db.query("SELECT id FROM events WHERE kind='repair_split_into_modules'")
    assert engine.prepare_action(pid,step,previous)==action
    assert len(calls)==1 # Completed candidate survives retry without another bill.


def test_bundle_checker_is_a_python_module_not_an_expected_text_file():
    with pytest.raises(ValueError):
        codegen.ModuleSpec(path='probe.txt',contract='expected output')


def test_bundle_outputs_cannot_delete_their_own_source_before_execution(project,monkeypatch):
    monkeypatch.setattr(codegen.OllamaProvider,'generate',lambda *a:codegen.PythonBundle(
        files=[{'path':'main.py','contract':'entry'}],checks=[{'path':'check_result.py','contract':'verify'}],
        expected_files=['main.py'],explanation='bad manifest'))
    with pytest.raises(ValueError,match='вихідні результати'):
        codegen.generate_bundle(project['id'],'step1','Test',lambda:False)


def test_checker_reference_resolves_supplied_source_without_editing_it():
    from study.schemas import Action, FileWrite

    source = "assert 2 + 2 == 4"
    action = Action(
        files=[
            {"path": "main.py", "content": "print(4)"},
            {"path": "check_results.py", "content": source},
        ],
        checks=[{"path": "check_results.py", "content": ""}],
        entrypoint="main.py",
        explanation="test",
    )
    fixed = codegen.normalize_checks(action)
    assert [f.path for f in fixed.files] == ["main.py"]
    assert fixed.checks[0].content == source
    action.files.append(FileWrite(path="check_results.py", content="assert False"))
    with pytest.raises(ValueError, match="Суперечливі"):
        codegen.normalize_checks(action)


def test_valid_unwritten_candidate_survives_pause_without_another_model_call(project,plan,monkeypatch):
    from study import config,db,engine
    from study.schemas import Action
    engine.save_plan(project['id'],plan)
    p=db.project(project['id']);step=p['steps'][0]
    a=Action(files=[{'path':'main.py','content':'print(4)'}],checks=[{'path':'check_result.py','content':'value=2\nassert value+value==4'}],entrypoint='main.py',explanation='already generated')
    codegen.save_candidate(p['id'],step['id'],p['revision'],'cloud/deepseek/deepseek-flash',a)
    monkeypatch.setattr(engine.OllamaProvider,'generate',lambda *a,**k:pytest.fail('Do not bill another generation'))
    assert engine.prepare_action(p['id'],step,'')==a
    assert not (config.project_dir(p['id'])/'workspace'/'main.py').exists()


def test_unwritten_candidate_from_old_generation_contract_is_not_silently_reused(project,plan,monkeypatch):
    import json
    from study import config,db,engine
    from study.schemas import Action,PythonProgram
    engine.save_plan(project['id'],plan)
    p=db.project(project['id']);step=p['steps'][0]
    a=Action(files=[{'path':'main.py','content':'print("OLD_CONTRACT")'}],checks=[{'path':'check_result.py','content':'value=2\nassert value+value==4'}],entrypoint='main.py',explanation='old')
    codegen.save_candidate(p['id'],step['id'],p['revision'],'cloud',a)
    path=config.project_dir(p['id'])/'codegen'/step['id']/'last-action.json'
    payload=json.loads(path.read_text('utf-8'));payload.pop('generation_contract');path.write_text(json.dumps(payload),'utf-8')
    monkeypatch.setattr(engine,'context',lambda *a,**k:'')
    monkeypatch.setattr(engine.OllamaProvider,'generate',lambda *a,**k:PythonProgram(main_code='print("NEW_CONTRACT")',checker_code='value=2\nassert value+value==4',expected_files=[],explanation='new'))
    action=engine.prepare_action(p['id'],step,'')
    assert 'NEW_CONTRACT' in action.files[0].content


def test_recovery_uses_supplied_checker_without_another_model_call(
    project, plan, monkeypatch
):
    from study import db, engine
    from study.schemas import Action

    engine.save_plan(project["id"], plan)
    p = db.project(project["id"])
    sid = p["steps"][0]["id"]
    a = Action(
        files=[
            {"path": "main.py", "content": "print(4)"},
            {"path": "check_result.py", "content": "assert 4 == 4"},
        ],
        checks=[{"path": "check_result.py", "content": ""}],
        entrypoint="main.py",
        explanation="test",
    )
    codegen.save_candidate(
        p["id"], sid, p["revision"], "cloud/deepseek/deepseek-flash", a
    )
    monkeypatch.setattr(
        engine.OllamaProvider,
        "generate",
        lambda *a, **k: pytest.fail("No generation needed"),
    )
    fixed = engine.prepare_action(p["id"], p["steps"][0], "")
    assert fixed.checks[0].content == "assert 4 == 4"


def test_module_generation_resumes_and_shares_actual_interfaces(project, monkeypatch):
    calls = []

    def generate(self, prompt, schema):
        calls.append(prompt)
        if issubclass(schema, codegen.PythonBundle):
            return codegen.PythonBundle(
                files=[
                    {
                        "path": "helpers.py",
                        "contract": "average(values) returns arithmetic mean",
                    },
                    {"path": "main.py", "contract": "Use average, write results.csv"},
                    {"path": "check_results.py", "contract": "Validate result CSV"},
                ],
                checks=[
                    {"path": "check_results.py", "contract": "Check CSV mean equals 4"}
                ],
                expected_files=["results.csv"],
                explanation="Розрахунок із перевіркою",
            )
        if "ТІЛЬКИ вміст файлу helpers.py" in prompt:
            return codegen.ModuleSource(
                content="def average(values):\n    return sum(values)/len(values)\n"
            )
        if "ТІЛЬКИ вміст файлу main.py" in prompt:
            assert "def average(values)" in prompt
            return codegen.ModuleSource(
                content="from helpers import average\nfrom pathlib import Path\nPath('results.csv').write_text('mean\\n'+str(average([2,4,6])))\n"
            )
        return codegen.ModuleSource(
            content="from pathlib import Path\nassert float(Path('results.csv').read_text().splitlines()[1]) == 4\n"
        )

    monkeypatch.setattr(codegen.OllamaProvider, "generate", generate)
    with pytest.raises(RuntimeError, match="призупинено"):
        codegen.generate_bundle(project["id"], "step1", "Тест", lambda: len(calls) >= 2)
    action = codegen.generate_bundle(project["id"], "step1", "Тест", lambda: False)
    assert len(calls) == 4  # Cached manifest and helper were not requested again.
    assert action.entrypoint == "main.py"
    assert [file.path for file in action.files] == ["helpers.py", "main.py"]
    assert action.checks[0].path == "check_results.py"


def test_bundle_manifest_cannot_write_outside_workspace(project, monkeypatch):
    monkeypatch.setattr(
        codegen.OllamaProvider,
        "generate",
        lambda *args: codegen.PythonBundle(
            files=[
                {"path": "main.py", "contract": "entry"},
                {"path": "../escape.py", "contract": "bad"},
            ],
            checks=[{"path": "check_results.py", "contract": "check"}],
            expected_files=[],
            explanation="",
        ),
    )
    with pytest.raises(ValueError):
        codegen.generate_bundle(project["id"], "step1", "Тест", lambda: False)


@pytest.mark.parametrize(
    "source,reason",
    [("print(np.zeros(2))", "np"), ("from main import calculate\n", "сам себе"), ('{"files":[]}', 'JSON')],
)
def test_invalid_module_is_rejected_before_deployment(
    project, monkeypatch, source, reason
):
    def generate(self, prompt, schema):
        if issubclass(schema, codegen.PythonBundle):
            return codegen.PythonBundle(
                files=[{"path": "main.py", "contract": "calculate"}],
                checks=[{"path": "check_result.py", "contract": "verify"}],
                expected_files=[],
                explanation="test",
            )
        return codegen.ModuleSource(content=source)

    monkeypatch.setattr(codegen.OllamaProvider, "generate", generate)
    with pytest.raises(ValueError, match=reason):
        codegen.generate_bundle(project["id"], "step1", "Тест", lambda: False)

def test_compact_manifest_reuses_complete_contract_before_generating_modules(project,monkeypatch):
    from study import assistance
    payload={'files':[{'path':'main.py','contract':'x'*300}], 'checks':[{'path':'check.py','contract':'verify'}], 'expected_files':['result.txt'], 'explanation':'complete'}
    calls=[]
    class Model:
        def __init__(self,*args,**kwargs):pass
        def generate(self,prompt,schema):
            if schema is codegen.CompactPythonBundle:
                calls.append('manifest');return schema.model_validate(payload)
            if 'ТІЛЬКИ вміст файлу main.py.' in prompt:return codegen.ModuleSource(content="from pathlib import Path\nPath('result.txt').write_text('4')")
            return codegen.ModuleSource(content="from pathlib import Path\nassert Path('result.txt').read_text() == '4'")
        def revalidate_last_response(self,schema):
            calls.append('local parse');return schema.model_validate(payload)
    monkeypatch.setattr(assistance,'BudgetedModel',Model)
    action=codegen.generate_bundle(project['id'],'step','stable task',lambda:False,assisted=True)
    assert action.entrypoint=='main.py' and calls==['manifest','local parse']
    assert codegen.generate_bundle(project['id'],'step','stable task',lambda:False,assisted=True)==action
    assert calls==['manifest','local parse']
