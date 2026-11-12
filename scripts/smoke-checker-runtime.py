"""Developer infrastructure probe, explicitly not a lab acceptance case."""
import json
import uuid
from study import config
from study.runtime import DockerRuntime

results=[]
for invoked in (False,True):
    pid='checkerprobe'+uuid.uuid4().hex
    workspace=config.project_dir(pid)/'workspace';workspace.mkdir()
    (workspace/'main.py').write_text("from pathlib import Path\nPath('value.txt').write_text('4')\n",'utf-8')
    text="from pathlib import Path\ndef verify():\n    assert int(Path('value.txt').read_text())==4\n"
    (workspace/'check_value.py').write_text(text+('verify()\n' if invoked else ''),'utf-8')
    result=DockerRuntime().run(pid,None,'python',[],60,lambda:False,expected_files=['value.txt'],check_paths=['check_value.py'])
    assert (result['exit_code']==0)==invoked,result
    results.append({'invoked':invoked,'exit_code':result['exit_code'],'log':result['log'],'run_id':result['run_id']})
out=config.DATA/'verification'/'checker-runtime-smoke.json'
out.write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf-8')
print(out)
