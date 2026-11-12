"""Infrastructure check only: a failed resource probe must prevent main.py."""
import json
from study import config,db
from study.runtime import DockerRuntime


results=[]
for succeeds in (False,True):
    pid='probecheck'+db.uid()
    workspace=config.project_dir(pid)/'workspace';workspace.mkdir()
    (workspace/'main.py').write_text("from pathlib import Path\nPath('main_ran.txt').write_text('yes')\n",'utf-8')
    (workspace/'resource_probe.py').write_text(
        "from pathlib import Path\nPath('probe_ran.txt').write_text('yes')\n"+
        ('' if succeeds else 'raise SystemExit(23)\n'),'utf-8')
    result=DockerRuntime().run(pid,'probe','python',[],30,lambda:False,
                               expected_files=[],check_paths=[],resource_probe='resource_probe.py')
    assert (workspace/'probe_ran.txt').is_file()
    assert (workspace/'main_ran.txt').is_file()==succeeds
    assert (result['exit_code']==0)==succeeds
    results.append({'run_id':result['run_id'],'probe_succeeded':succeeds,'main_executed':succeeds,'exit_code':result['exit_code']})
proof={'kind':'Resource probe controller smoke check; not proof of ResNet50 memory feasibility','cases':results}
(config.DATA/'verification'/'resource-probe-smoke.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(proof))
