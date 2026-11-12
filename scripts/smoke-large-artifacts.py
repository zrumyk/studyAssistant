"""Real-container output policy probe, explicitly not a lab/model result."""
import json
import uuid
from study import config
from study.runtime import DockerRuntime,file_hash

original=config.settings
config.settings=lambda:original()|{'container_output_mb':64}
records=[]
for size in (40,80):
    pid='artifactprobe'+uuid.uuid4().hex
    root=config.project_dir(pid);workspace=root/'workspace';workspace.mkdir()
    script="from pathlib import Path\nimport time\nPath('payload.bin').write_bytes(bytes("+str(size*1024**2)+"))\n"
    if size>64:script+='time.sleep(5)\n'
    (workspace/'main.py').write_text(script,'utf-8')
    result=DockerRuntime().run(pid,None,'python',[],30,lambda:False,expected_files=['payload.bin'])
    if size<=64:
        assert result['exit_code']==0 and not result['reason'],result
        assert 'payload.bin' in result['files']
        assert (workspace/'payload.bin').stat().st_size==size*1024**2
        assert file_hash(workspace/'payload.bin')==file_hash(root/'runs'/result['run_id']/'payload.bin')
    else:
        assert result['exit_code']!=0 and result['reason'],result
    records.append({'requested_megabytes':size,'quota_megabytes':64,'run_id':result['run_id'],'exit_code':result['exit_code'],'reason':result['reason'],'copied':'payload.bin' in result['files']})
target=config.DATA/'verification'/'large-artifacts-smoke.json'
target.write_text(json.dumps({'kind':'Infrastructure only','lab_accepted':False,'records':records},ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(records,ensure_ascii=False,indent=2))
