"""Real two-profile file transfer fixtures; no lab code, paid calls or acceptance."""
import argparse
import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from study import config
from study.artifact_contracts import capture_outputs, require_inputs, check_action, fresh_outputs, validate_contract
from study.runtime import DockerRuntime
from study.schemas import Action, Plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve old evidence; choose a new output')
    with sqlite3.connect(config.DB_PATH.resolve().as_uri()+'?mode=ro', uri=True) as con:
        if con.execute("SELECT count(*) FROM jobs WHERE status IN ('running','queued')").fetchone()[0]:
            raise ValueError('Active jobs')
    if shutil.disk_usage(ROOT).free < 13*1024**3:
        raise ValueError('Insufficient space for protocol fixtures')
    pid = 'artifactcheck' + uuid.uuid4().hex
    root = config.project_dir(pid)
    workspace = root / 'workspace'
    workspace.mkdir()
    (workspace/'unrelated.txt').write_bytes(b'not a declared input')
    plan = Plan.model_validate(dict(artifact_contract_version=1, goal='Two-profile measured data transfer',
        requirements=[], missing=[], conflicts=[], report_sections=['Results'], steps=[
            dict(title='Native producer', kind='code', language='c', instruction='Measure', expected='measurement.json', artifact_outputs=['measurement.json']),
            dict(title='Consumer', kind='code', language='javascript', instruction='Transform', expected='summary.json', artifact_inputs=['measurement.json'], artifact_outputs=['summary.json']),
            dict(title='Report', kind='report', instruction='Render', expected='DOCX/PDF', artifact_inputs=['summary.json'])]))
    validate_contract(plan, pid)
    sources = [
        ('main.c', '#include <stdio.h>\nint main(void){int n;if(scanf("%d",&n)!=1)return 2;FILE*f=fopen("measurement.json","w");fprintf(f,"{\\"value\\":%d}",2*n);fclose(f);f=fopen("unpublished.txt","w");fputs("snapshot only",f);fclose(f);return 0;}\n',
         'from pathlib import Path\nimport json\nassert not Path("unrelated.txt").exists()\nassert json.loads(Path("measurement.json").read_text())["value"]==int(Path("/study-stdin").read_text())*2\n'),
        ('main.js', 'const fs=require("fs");const m=JSON.parse(fs.readFileSync("measurement.json","utf8"));let ro=false;try{fs.writeFileSync("measurement.json","changed");}catch(e){ro=["EROFS","EACCES"].includes(e.code);}fs.writeFileSync("summary.json",JSON.stringify({value:m.value+1,readonly:ro}));\n',
         'from pathlib import Path\nimport json\nassert not Path("main.c").exists()\nassert not Path("unrelated.txt").exists()\na=json.loads(Path("measurement.json").read_text())\nb=json.loads(Path("summary.json").read_text())\nassert b["value"]==a["value"]+1 and b["readonly"]\n')]
    project = dict(id=pid, plan=plan.model_dump(), steps=[])
    report = dict(scope=__doc__, client_date='2026-11-03', captured_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  project_dir=str(root), runs=[], negative_checks=[])
    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    for index, (entry, source, checker) in enumerate(sources):
        spec = plan.steps[index].model_dump()
        step = dict(position=index, spec=spec, status='pending', result={})
        project['steps'].append(step)
        require_inputs(project, step)
        action = Action(files=[dict(path=entry, content=source)], checks=[dict(path='check_current.py', content=checker)],
                        entrypoint=entry, expected_files=spec['artifact_outputs'], explanation='Developer fixture only')
        check_action(spec, action)
        fresh_outputs(pid, spec, action)
        for file in action.files + action.checks:
            (workspace/file.path).write_text(file.content, encoding='utf-8')
        result = DockerRuntime().run(pid, 'technical-fixture', spec['language'], [], 20, lambda:False,
            expected_files=action.expected_files, check_paths=['check_current.py'], stdin_text='13\n' if index==0 else '',
            visible_files=[file.path for file in action.files+action.checks]+spec['artifact_inputs'],
            publish_files=spec['artifact_outputs'], readonly_artifacts=spec['artifact_inputs'])
        report['runs'].append(result)
        save()
        assert result['exit_code']==0 and result['checks']==['check_current.py'], result['log']
        result['artifact_contract'] = capture_outputs(workspace, spec, result['produced_files'], snapshot=root/'runs'/result['run_id'])
        step.update(status='done', result=result)
        assert not (workspace/'unpublished.txt').exists()
        if index == 0:
            original = (workspace/'measurement.json').read_bytes()
            (workspace/'measurement.json').write_bytes(b'{"value":999}')
            consumer = dict(position=1, spec=plan.steps[1].model_dump())
            try:
                require_inputs(project, consumer)
            except RuntimeError:
                report['negative_checks'].append('Changed producer data rejected before consumer')
            else:
                raise AssertionError('Changed input accepted')
            # Restore the actual fixture bytes, never a student solution/result.
            (workspace/'measurement.json').write_bytes(original)
        save()
        print(spec['language'] + ' contract: PASS', flush=True)
    assert json.loads((workspace/'summary.json').read_text()) == {'value':27, 'readonly':True}
    report['final_free_gib'] = round(shutil.disk_usage(ROOT).free/2**30,2)
    report['complete'] = True
    save()


if __name__ == '__main__':
    main()
