import json
from pathlib import Path
import numpy as np
from study.input_metadata import array_metadata


class PickleMarker:
    def __init__(self, path):
        self.path = path

    def __reduce__(self):
        return Path.write_text, (self.path, "unpickled")


def test_array_schema_is_read_without_unpickling(isolated):
    marker = isolated / "unexpected.txt"
    np.savez(
        isolated / "input.npz",
        values=np.zeros((2, 3), dtype=np.float32),
        payload=np.array([PickleMarker(marker)], dtype=object),
    )
    info = json.loads(array_metadata(isolated))[0]
    assert info["arrays"]["values"]["shape"] == [2, 3]
    assert info["arrays"]["payload"]["object_payload"] is True
    assert len(info["sha256"]) == 64
    assert not marker.exists()


def test_repair_uses_latest_unwritten_cloud_candidate_instead_of_older_workspace(project,plan):
    from study import config,db,engine
    from study.codegen import save_candidate
    from study.input_metadata import repair_sources
    from study.schemas import Action
    pid=project['id'];engine.save_plan(pid,plan)
    current=db.project(pid);step=current['steps'][0]
    root=config.project_dir(pid)/'workspace';root.mkdir()
    (root/'main.py').write_text("print('STALE_WORKSPACE')",'utf-8')
    action=Action(files=[{'path':'main.py','content':'print(CLOUD_CANDIDATE_MISSING_NAME)'}],explanation='candidate')
    save_candidate(pid,step['id'],current['revision'],'cloud/deepseek/deepseek-flash',action)
    actual=repair_sources(pid,step['id'],current['revision'],'qwen2.5-coder:7b',root,'main.py','Статичні помилки main.py')
    assert 'CLOUD_CANDIDATE_MISSING_NAME' in actual
    assert 'STALE_WORKSPACE' not in actual
    stale=repair_sources(pid,step['id'],current['revision']+1,'qwen2.5-coder:7b',root,'main.py','Статичні помилки main.py')
    assert 'CLOUD_CANDIDATE_MISSING_NAME' not in stale


def test_authorized_cloud_repair_sees_complete_failed_function_despite_small_local_context(project,plan):
    from study import config,db,engine
    from study.input_metadata import repair_sources
    pid=project['id'];engine.save_plan(pid,plan)
    step=db.project(pid)['steps'][0]
    root=config.project_dir(pid)/'workspace';root.mkdir()
    content=('# setup\n'*700)+'def failing_function():\n    raise RuntimeError("IMPORTANT_MIDDLE_OF_FAILED_FUNCTION")\n'+('# footer\n'*700)
    (root/'main.py').write_text(content,'utf-8')
    (root/'check_current_step.py').write_text('# checks\n'*900,'utf-8')
    from study.codegen import save_candidate
    from study.schemas import Action,ActionCheckpoint
    revision=db.project(pid)['revision']
    save_candidate(pid,step['id'],revision,'cloud',Action(files=[{'path':'main.py','content':content}],checks=[{'path':'check_current_step.py','content':'# checks\n'*900}],explanation='current step'))
    checkpoint=config.project_dir(pid)/'codegen'/step['id']/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'));saved.written_to_workspace=True
    checkpoint.write_text(saved.model_dump_json(),'utf-8')
    config.save_settings({'cloud_consent_mode':'budgeted','context':4096})
    text=repair_sources(pid,step['id'],db.project(pid)['revision'],'local-model',root,'main.py','File "/work/check_current_step.py", line 71')
    assert content in text
    assert 'IMPORTANT_MIDDLE_OF_FAILED_FUNCTION' in text
    assert 'середину великого файлу пропущено' not in text


def test_never_written_step_does_not_mislabel_previous_steps_main_as_failed_code(project,plan):
    from study import config,db,engine
    from study.input_metadata import repair_sources
    engine.save_plan(project['id'],plan)
    p=db.project(project['id']);step=p['steps'][0]
    root=config.project_dir(p['id'])/'workspace';root.mkdir()
    (root/'main.py').write_text('PREVIOUS_COMPLETED_TASK_NOT_CURRENT_CODE','utf-8')
    assert repair_sources(p['id'],step['id'],p['revision'],'cloud',root,'main.py','Module main.py too large during generation')==''


def test_handled_runtime_error_includes_written_helper_actual_bytes(project,plan):
    from study import config,db,engine
    from study.codegen import save_candidate
    from study.input_metadata import repair_sources
    from study.schemas import Action,ActionCheckpoint
    pid=project['id'];engine.save_plan(pid,plan)
    revision=db.project(pid)['revision'];step=db.project(pid)['steps'][0]
    root=config.project_dir(pid)/'workspace';(root/'helpers').mkdir(parents=True)
    (root/'main.py').write_text('import helpers.convert','utf-8')
    (root/'helpers'/'convert.py').write_text('ACTUAL_HELPER_BYTES','utf-8')
    (root/'unrelated.py').write_text('UNRELATED_PREVIOUS_STEP','utf-8')
    action=Action(files=[{'path':'main.py','content':'old main'},{'path':'helpers/convert.py','content':'OLD_SNAPSHOT'}],explanation='candidate')
    save_candidate(pid,step['id'],revision,'cloud/deepseek/deepseek-flash',action)
    checkpoint=config.project_dir(pid)/'codegen'/step['id']/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'))
    saved.written_to_workspace=True
    checkpoint.write_text(saved.model_dump_json(),'utf-8')
    text=repair_sources(pid,step['id'],revision,'local-model',root,'main.py','Conversion unavailable: missing optional package')
    assert 'ACTUAL_HELPER_BYTES' in text and 'OLD_SNAPSHOT' not in text
    assert 'UNRELATED_PREVIOUS_STEP' not in text
