import pytest
from study import bounded_repair,config,db,engine
from study.codegen import save_candidate
from study.schemas import Action


def candidate(project,plan):
    engine.save_plan(project['id'],plan)
    p=db.project(project['id']);s=p['steps'][0]
    action=Action(files=[{'path':'helper.py','content':'def value():\n    return 2\n'},
                         {'path':'main.py','content':'from helper import value\nprint(value())\n'}],
                  checks=[{'path':'check_value.py','content':'assert 2+2==4\n'}],expected_files=['result.json'],explanation='')
    save_candidate(p['id'],s['id'],p['revision'],'cloud',action)
    return p,s,action


def test_cancelled_modular_repair_keeps_completed_modules_and_original_candidate(project,plan,monkeypatch):
    p,s,action=candidate(project,plan);calls=[];stop=[False]
    class Model:
        def __init__(self,*a,**kw):pass
        def generate(self,prompt,schema):
            calls.append(schema.__name__)
            if schema is bounded_repair.RepairPlan:
                return schema(targets=[{'path':'helper.py','reason':'fix value'},{'path':'main.py','reason':'write result'}])
            if '"target": "helper.py"' in prompt:
                stop[0]=True
                return schema(content='def value():\n    return 4\n')
            assert 'return 4' in prompt # Later modules see the actual repaired producer.
            return schema(content='from helper import value\nprint(value())\n')
    monkeypatch.setattr(bounded_repair,'BudgetedModel',Model)
    cp=config.project_dir(p['id'])/'codegen'/s['id']/'last-action.json';before=cp.read_bytes()
    with pytest.raises(RuntimeError,match='призупинено'):
        bounded_repair.repair_modules(p['id'],s['id'],s['spec'],'failure',lambda:stop[0])
    assert cp.read_bytes()==before
    stop[0]=False
    fixed=bounded_repair.repair_modules(p['id'],s['id'],s['spec'],'failure',lambda:stop[0])
    assert calls==['RepairPlan','ModuleSource','ModuleSource']
    assert fixed.files[0].content.endswith('return 4\n')
    assert fixed.checks==action.checks and fixed.expected_files==action.expected_files
    assert cp.read_bytes()==before and not (config.project_dir(p['id'])/'workspace').exists()


def test_modular_repair_plan_cannot_add_foreign_source(project,plan,monkeypatch):
    p,s,action=candidate(project,plan)
    class Model:
        def __init__(self,*a,**kw):pass
        def generate(self,prompt,schema):
            return schema(targets=[{'path':'../foreign.py','reason':'bad path'}])
    monkeypatch.setattr(bounded_repair,'BudgetedModel',Model)
    with pytest.raises(ValueError,match='власні Python'):
        bounded_repair.repair_modules(p['id'],s['id'],s['spec'],'failure',lambda:False)


def test_complete_plan_reason_keeps_wire_preference_without_discarding_content():
    reason='Observed causal explanation. '*30
    target=bounded_repair.RepairTarget(path='main.py',reason=reason)
    assert target.reason==reason
    assert bounded_repair.RepairTarget.model_json_schema()['properties']['reason']['maxLength']==240
    with pytest.raises(ValueError):bounded_repair.RepairTarget(path='main.py',reason='x'*2001)
