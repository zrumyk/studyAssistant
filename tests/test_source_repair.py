import pytest
from study import config,db,engine
from study.codegen import save_candidate,repair_action
from study.schemas import Action,ActionCheckpoint,PythonRepair


@pytest.fixture
def written_action(project,plan):
    pid=project['id'];engine.save_plan(pid,plan)
    p=db.project(pid);sid=p['steps'][0]['id']
    workspace=config.project_dir(pid)/'workspace';workspace.mkdir()
    action=Action(files=[{'path':'main.py','content':'def value():\n    return 2\n\nprint(value())\n'}],checks=[{'path':'check_value.py','content':'expected=4\nassert expected>0\n'}],expected_files=['result.json'],explanation='original')
    for file in action.files+action.checks:(workspace/file.path).write_text(file.content,'utf-8')
    save_candidate(pid,sid,p['revision'],'cloud',action)
    path=config.project_dir(pid)/'codegen'/sid/'last-action.json'
    checkpoint=ActionCheckpoint.model_validate_json(path.read_text('utf-8'));checkpoint.written_to_workspace=True
    path.write_text(checkpoint.model_dump_json(),'utf-8')
    return pid,sid,p['revision'],workspace,action


def test_exact_repair_preserves_everything_else_and_is_not_written_before_validation(written_action):
    pid,sid,revision,workspace,original=written_action
    proposal=PythonRepair(edits=[{'path':'main.py','before':'return 2','after':'return 4'}],explanation='fix')
    repaired=repair_action(pid,sid,revision,workspace,proposal)
    assert repaired.files[0].content==original.files[0].content.replace('return 2','return 4')
    assert repaired.checks==original.checks and repaired.expected_files==original.expected_files
    assert (workspace/'main.py').read_text('utf-8')==original.files[0].content
    assert original.explanation=='original'


@pytest.mark.parametrize('path,before',[
    ('../other-project/main.py','return 2'),('inputs/data.py','return 2'),
    ('result.json','return 2'),('new_module.py','return 2'),('main.py','not present'),
    ('main.py','\n'),
])
def test_unsafe_missing_or_ambiguous_repair_is_rejected(written_action,path,before):
    pid,sid,revision,workspace,original=written_action
    proposal=PythonRepair(edits=[{'path':path,'before':before,'after':'replacement'}],explanation='wrong')
    with pytest.raises(ValueError):repair_action(pid,sid,revision,workspace,proposal)
    assert (workspace/'main.py').read_text('utf-8')==original.files[0].content


def test_repair_rejects_a_different_project_revision(written_action):
    pid,sid,revision,workspace,_=written_action
    proposal=PythonRepair(edits=[{'path':'main.py','before':'return 2','after':'return 4'}],explanation='fix')
    with pytest.raises(ValueError):repair_action(pid,sid,revision+1,workspace,proposal)


def test_mismatched_edits_regenerate_only_owned_module_from_original_bytes(written_action):
    pid,sid,revision,workspace,original=written_action
    proposal=PythonRepair(edits=[{'path':'main.py','before':'return 2','after':'return 3'},{'path':'main.py','before':'not present','after':'return 4'}],explanation='fix')
    calls=[]
    def regenerate(path,source,edits):
        calls.append(path)
        assert source==original.files[0].content and len(edits)==2
        return source.replace('return 2','return 4')
    repaired=repair_action(pid,sid,revision,workspace,proposal,regenerate=regenerate)
    assert calls==['main.py'] and 'return 4' in repaired.files[0].content
    assert repaired.checks==original.checks
    assert (workspace/'main.py').read_text('utf-8')==original.files[0].content


def test_regeneration_cannot_bypass_path_scope_or_static_validation(written_action):
    pid,sid,revision,workspace,original=written_action
    proposal=PythonRepair(edits=[{'path':'../foreign.py','before':'missing','after':'x=4'}],explanation='bad')
    with pytest.raises(ValueError):repair_action(pid,sid,revision,workspace,proposal,regenerate=lambda *a:pytest.fail('Outside scope'))
    proposal.edits[0].path='main.py'
    with pytest.raises(SyntaxError):repair_action(pid,sid,revision,workspace,proposal,regenerate=lambda *a:'def invalid(')
    assert (workspace/'main.py').read_text('utf-8')==original.files[0].content
