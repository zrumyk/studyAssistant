import json
import pytest
from study import resource_recovery as recovery
from study.schemas import Action, FileWrite


def test_oom_probe_is_required_across_restarts_and_non_oom_errors(project):
    pid=project['id'];action=Action(files=[{'path':'main.py','content':'print(1)'}],explanation='source')
    with pytest.raises(ValueError,match='resource_probe_required'):
        recovery.require_probe(pid,'step','python',action,json.dumps({'oom':True,'run_id':'old'}))
    assert recovery.marker_path(pid,'step').exists()
    with pytest.raises(ValueError,match='resource_probe_required'):
        recovery.require_probe(pid,'step','python',action,'a later validation error')
    action.files.append(FileWrite(path='resource_probe.py',content='real_probe()'))
    recovery.require_probe(pid,'step','python',action,'')
    # A probe on a different step cannot satisfy this step's own requirement.
    with pytest.raises(ValueError):recovery.require_probe(pid,'step','javascript',action,'')


def test_other_runtime_errors_do_not_require_a_memory_probe(project):
    action=Action(files=[{'path':'main.py','content':'print(1)'}],explanation='source')
    recovery.remember_oom(project['id'],'other',{'exit_code':1,'oom':False})
    recovery.require_probe(project['id'],'other','python',action,json.dumps({'exit_code':1}))
    assert not recovery.marker_path(project['id'],'other').exists()
