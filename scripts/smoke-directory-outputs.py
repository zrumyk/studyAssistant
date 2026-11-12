"""Real sandbox probe: stale directory contents must not satisfy outputs."""
import json,uuid
from study import config
from study.runtime import DockerRuntime
from study.output_validation import validate_expected_outputs

pid='directoryprobe'+uuid.uuid4().hex
root=config.project_dir(pid);workspace=root/'workspace';(workspace/'logs').mkdir(parents=True)
(workspace/'logs/stale.txt').write_text('inherited content must not reach this run','utf-8')
(workspace/'main.py').write_text("from pathlib import Path\nassert not Path('logs/stale.txt').exists()\nPath('logs').mkdir(exist_ok=True)\nPath('logs/new.txt').write_text(str(sum([2,4,6])))\n",'utf-8')
(workspace/'check_outputs.py').write_text("from pathlib import Path\nvalue=int(Path('logs/new.txt').read_text())\nassert value==12\nassert not Path('logs/stale.txt').exists()\n",'utf-8')
result=DockerRuntime().run(pid,None,'python',[],30,lambda:False,expected_files=['logs'],check_paths=['check_outputs.py'])
assert result['exit_code']==0 and not result['reason'] and not result['oom'],result
validate_expected_outputs(workspace,result,['logs'])
assert 'logs\\stale.txt' not in result['files'] and 'logs/stale.txt' not in result['files']
proof={'kind':'Infrastructure only, not a lab result','run_id':result['run_id'],'directory_output_accepted':True,'stale_child_not_inherited':True,'actual_checker_executed':result['checks'],'exit_code':result['exit_code']}
(config.DATA/'verification/directory-outputs-smoke.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(proof,ensure_ascii=False))
