"""Infrastructure probe only; not a generated lab or acceptance result."""
import json
import uuid
from study import config
from study.runtime import DockerRuntime

pid='headlessprobe'+uuid.uuid4().hex
workspace=config.project_dir(pid)/'workspace'
workspace.mkdir()
(workspace/'main.py').write_text("import cv2\nimport numpy as np\nfrom pathlib import Path\na=np.zeros((9,9),dtype=np.uint8)\na[4,4]=255\nb=cv2.medianBlur(a,3)\nassert int(b.sum())==0\nassert cv2.imwrite('probe.png',b)\nPath('version.txt').write_text(cv2.__version__)\n",'utf-8')
result=DockerRuntime().run(pid,None,'python',['numpy','opencv-python'],120,lambda:False,expected_files=['probe.png','version.txt'])
out=config.DATA/'verification'/'headless-runtime-smoke.json'
out.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps({'exit_code':result['exit_code'],'run_id':result['run_id'],'log':result['log'][-2000:]},ensure_ascii=False))
assert result['exit_code']==0,result
