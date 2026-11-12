import json

import pytest

from study import config, db, engine
from study.execution_protocol import stdin_identity
from study.reports import report_evidence


@pytest.mark.parametrize('language,entrypoint,source', [
    ('c', 'main.c', 'int main(void){return 0;}'),
    ('javascript', 'main.js', 'console.log("actual-runtime-source");'),
])
def test_report_uses_actual_profile_entrypoint_and_input_identity(project, plan, language, entrypoint, source):
    pid = project['id']
    plan['steps'][0]['language'] = language
    engine.save_plan(pid, plan)
    step = db.project(pid)['steps'][0]
    run = config.project_dir(pid) / 'runs' / 'snapshot'
    run.mkdir(parents=True)
    (run / entrypoint).write_text(source, encoding='utf-8')
    (run / 'check_result.py').write_text('assert 2+2==4', encoding='utf-8')
    identity = stdin_identity('дані\n')
    result = dict(run_id='snapshot', exit_code=0, reason='', oom=False, checks=['check_result.py'],
                  execution_protocol={'stdin': identity})
    db.execute("UPDATE steps SET status='done',result=? WHERE id=?", (json.dumps(result), step['id']))
    evidence = report_evidence(db.project(pid))
    assert entrypoint in evidence and source in evidence
    assert identity['sha256'] in evidence
    assert 'дані' not in evidence  # Raw stdin is not copied to reporting prompts automatically.
