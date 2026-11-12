import json
import pytest
from study import config,db,engine
from study.codegen import save_candidate
from study.schemas import Action,ActionCheckpoint
from study.output_validation import validate_expected_outputs
from study.execution_diagnostics import recover_successful_run


def test_output_directory_requires_a_real_new_child(isolated):
    folder=isolated/'logs';folder.mkdir();(folder/'experiment.zip').write_bytes(b'archive')
    validate_expected_outputs(isolated,{'produced_files':['logs/experiment.zip']},['logs'])
    for produced in ([],['other.txt'],['logs/missing.zip']):
        with pytest.raises(ValueError):validate_expected_outputs(isolated,{'produced_files':produced},['logs'])


def test_unchanged_old_files_or_the_project_root_are_not_results(isolated):
    (isolated/'old.json').write_text('{}','utf-8')
    with pytest.raises(ValueError):validate_expected_outputs(isolated,{'produced_files':[]},['old.json'])
    with pytest.raises(ValueError):validate_expected_outputs(isolated,{'produced_files':['old.json']},['.'])


def test_successful_run_recovery_requires_same_source_and_output_bytes(project,plan):
    pid=project['id'];engine.save_plan(pid,plan)
    p=db.project(pid);step=p['steps'][0]
    root=config.project_dir(pid)/'workspace';(root/'logs').mkdir(parents=True)
    action=Action(files=[{'path':'main.py','content':'print(4)'}],checks=[{'path':'check_result.py','content':'value=2\nassert value+value==4'}],expected_files=['logs'],entrypoint='main.py',explanation='code')
    for file in action.files+action.checks:(root/file.path).write_text(file.content,'utf-8')
    (root/'logs/experiment.zip').write_bytes(b'real results')
    save_candidate(pid,step['id'],p['revision'],'cloud',action)
    path=config.project_dir(pid)/'codegen'/step['id']/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(path.read_text('utf-8'));saved.written_to_workspace=True
    path.write_text(saved.model_dump_json(),'utf-8')
    runid='c'*32;run=config.project_dir(pid)/'runs'/runid;(run/'logs').mkdir(parents=True)
    for file in action.files+action.checks:(run/file.path).write_text(file.content,'utf-8')
    (run/'logs/experiment.zip').write_bytes(b'real results')
    result={'run_id':runid,'exit_code':0,'reason':'','oom':False,'checks':['check_result.py'],'files':['logs/experiment.zip'],'produced_files':['logs/experiment.zip']}
    (run/'result.json').write_text(json.dumps(result),'utf-8')
    previous='Цей запуск не створив очікуваний результат: logs'
    restored=recover_successful_run(pid,step['id'],p['revision'],root,previous)
    assert restored and restored[0]==action and restored[1]['run_id']==runid
    from study.execution_protocol import stdin_identity
    saved.action.stdin_text='first\n'
    path.write_text(saved.model_dump_json(),'utf-8')
    input_path=run.parent/(runid+'.stdin')
    input_path.write_bytes(b'first\n')
    from study.execution_protocol import stream_identity
    (run/'.study_main.stdout').write_bytes(b'actual stdout\n')
    (run/'.study_main.stderr').write_bytes(b'')
    result['execution_protocol']={'version':2,'stdin':stdin_identity('first\n'),'stdin_artifact':input_path.name,
                                  'streams':{name:stream_identity(run/('.study_main.'+name)) for name in ('stdout','stderr')}}
    (run/'result.json').write_text(json.dumps(result),'utf-8')
    assert recover_successful_run(pid,step['id'],p['revision'],root,previous)
    (run/'.study_main.stdout').write_bytes(b'changed stdout\n')
    assert recover_successful_run(pid,step['id'],p['revision'],root,previous) is None
    (run/'.study_main.stdout').write_bytes(b'actual stdout\n')
    input_path.write_bytes(b'tampered\n')
    assert recover_successful_run(pid,step['id'],p['revision'],root,previous) is None
    input_path.write_bytes(b'first\n')
    saved.action.stdin_text='second\n'
    path.write_text(saved.model_dump_json(),'utf-8')
    assert recover_successful_run(pid,step['id'],p['revision'],root,previous) is None
    saved.action.stdin_text=''
    path.write_text(saved.model_dump_json(),'utf-8')
    result.pop('execution_protocol')
    (run/'result.json').write_text(json.dumps(result),'utf-8')
    assert restored[1]['recovered_successful_run'] is True
    (root/'logs/experiment.zip').write_bytes(b'changed data')
    assert recover_successful_run(pid,step['id'],p['revision'],root,previous) is None
    (root/'logs/experiment.zip').write_bytes(b'real results')
    result['exit_code']=1;(run/'result.json').write_text(json.dumps(result),'utf-8')
    assert recover_successful_run(pid,step['id'],p['revision'],root,previous) is None
