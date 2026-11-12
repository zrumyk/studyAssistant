import json
import pytest
from study.execution_diagnostics import failure_context,runtime_failure_message,failure_signature


@pytest.mark.parametrize('log',[
    'Traceback: File "/runs/{run}/main.py", line 8\nKeyError: missing_column',
    'TypeError: Cannot read properties of undefined (reading id)\n at /runs/{run}/main.js:8',
    'sqlite3.IntegrityError: UNIQUE constraint failed: users.email\n/runs/{run}/main.sql',
])
def test_repeat_signature_does_not_depend_on_runtime_id_or_inventory(log):
    def error(run,count):
        return json.dumps({'run_id':run,'exit_code':1,'oom':False,'reason':'','log':log.format(run=run),
                           'output_file_count':count,'files':[run+'/output.txt']})
    assert failure_signature(error('a'*32,3))==failure_signature(error('b'*32,8))


@pytest.mark.parametrize('field,value',[
    ('log','KeyError: another_column'),('exit_code',137),('oom',True),
    ('resource_limits',{'memory_mb':2048}),('diagnostic_files',{'expected':4,'actual':7}),
])
def test_repeat_signature_keeps_causal_differences(field,value):
    original={'run_id':'a'*32,'exit_code':1,'oom':False,'log':'KeyError: missing_column',
              'resource_limits':{'memory_mb':4096},'diagnostic_files':{'expected':4,'actual':6}}
    assert failure_signature(json.dumps(original))!=failure_signature(json.dumps(original|{field:value}))


def test_unknown_diagnostics_are_not_semantically_collapsed():
    assert failure_signature('AssertionError: expected 5, actual 3')!=failure_signature('AssertionError: expected 5, actual 4')
    assert failure_signature('{broken json with run_id a}')!=failure_signature('{broken json with run_id b}')


def test_long_inventory_cannot_hide_the_actual_runtime_traceback():
    result={'run_id':'example','exit_code':1,'oom':False,'reason':'',
            'log':'progress\n'*2000+'Traceback: UNIQUE_ACTUAL_FAILURE_AT_END',
            'files':['large-path-'+str(i) for i in range(3000)]}
    message=runtime_failure_message(result)
    assert len(message)<7000
    assert json.loads(message)['output_file_count']==3000
    prompt=failure_context(message)
    assert 'UNIQUE_ACTUAL_FAILURE_AT_END' in prompt
    assert 'large-path-' not in prompt


def test_oom_diagnostics_preserve_the_actual_container_limit():
    result={'run_id':'oom-run','exit_code':137,'oom':True,'reason':'','log':'Killed',
            'resource_limits':{'memory_mb':4096,'cpus':4,'timeout_seconds':3600},'files':[]}
    message=runtime_failure_message(result)
    assert json.loads(message)['resource_limits']['memory_mb']==4096
    context=failure_context(message)
    assert '4096' in context and '"oom": true' in context


def test_legacy_failure_cut_inside_inventory_still_exposes_intact_error_log():
    legacy=json.dumps({'run_id':'old','exit_code':1,'log':'Traceback: LEGACY_ERROR_VISIBLE',
                       'files':['file-'+str(i) for i in range(3000)]})[:7000]
    assert 'LEGACY_ERROR_VISIBLE' in failure_context(legacy)
    assert failure_context('SyntaxError: source line invalid')=='SyntaxError: source line invalid'


def test_generation_error_recovers_only_exact_written_action_failure(project,plan):
    from study import config,db,engine
    from study.codegen import save_candidate
    from study.schemas import Action,ActionCheckpoint
    from study.execution_diagnostics import recover_runtime_failure
    pid=project['id'];engine.save_plan(pid,plan)
    revision=db.project(pid)['revision'];sid=db.project(pid)['steps'][0]['id']
    root=config.project_dir(pid)/'workspace';root.mkdir()
    (root/'main.py').write_text('original_code','utf-8')
    action=Action(files=[{'path':'main.py','content':'original_code'}],explanation='code')
    save_candidate(pid,sid,revision,'cloud',action)
    checkpoint=config.project_dir(pid)/'codegen'/sid/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'));saved.written_to_workspace=True
    checkpoint.write_text(saved.model_dump_json(),'utf-8')
    run=config.project_dir(pid)/'runs'/'failed';run.mkdir(parents=True)
    (run/'main.py').write_text('unrelated_code','utf-8')
    (run/'result.json').write_text(json.dumps({'run_id':'failed','exit_code':1,'log':'ACTUAL_RUNTIME_FAULT','files':[]}),'utf-8')
    previous='Cloud output incomplete'
    assert recover_runtime_failure(pid,sid,revision,root,previous)==previous
    (run/'main.py').write_text('original_code','utf-8')
    recovered=recover_runtime_failure(pid,sid,revision,root,previous)
    assert 'ACTUAL_RUNTIME_FAULT' in recovered and 'Cloud output incomplete' in recovered
    assert recover_runtime_failure(pid,sid,revision+1,root,previous)==previous
    (run/'result.json').write_text(json.dumps({'run_id':'failed','exit_code':0,'log':'success','files':[]}),'utf-8')
    assert recover_runtime_failure(pid,sid,revision,root,previous)==previous


def test_cancelled_execution_resumes_unchanged_code_without_new_generation(project,plan,monkeypatch):
    from study import config,db,engine
    from study.codegen import save_candidate
    from study.schemas import Action,ActionCheckpoint
    pid=project['id'];engine.save_plan(pid,plan)
    revision=db.project(pid)['revision'];sid=db.project(pid)['steps'][0]['id']
    root=config.project_dir(pid)/'workspace';root.mkdir()
    action=Action(files=[{'path':'main.py','content':'print(4)'}],checks=[{'path':'check_current_step.py','content':'value=2\nassert value+value==4'}],explanation='code')
    for file in action.files+action.checks:(root/file.path).write_text(file.content,'utf-8')
    save_candidate(pid,sid,revision,'cloud',action)
    checkpoint=config.project_dir(pid)/'codegen'/sid/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'));saved.written_to_workspace=True
    checkpoint.write_text(saved.model_dump_json(),'utf-8')
    run=config.project_dir(pid)/'runs'/'cancelled';run.mkdir(parents=True)
    for file in action.files+action.checks:(run/file.path).write_text(file.content,'utf-8')
    (run/'result.json').write_text(json.dumps({'run_id':'cancelled','exit_code':137,'reason':'Зупинено користувачем','log':'','files':[]}),'utf-8')
    monkeypatch.setattr(engine,'context',lambda *a,**k:pytest.fail('Must reuse cancelled action, not generate again'))
    assert engine.prepare_action(pid,db.project(pid)['steps'][0],'Cloud output incomplete')==action
