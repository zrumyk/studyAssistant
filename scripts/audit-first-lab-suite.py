"""Read-only evidence inventory. Never labels a lab correct from status alone."""
import argparse
import hashlib
import json
from pathlib import Path
from study import config,db

parser=argparse.ArgumentParser()
parser.add_argument('manifest',type=Path)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
manifest=json.loads(args.manifest.read_text('utf-8'))
audit={'kind':'Independent evidence inventory, not semantic acceptance','cases':[]}
for case in manifest['cases']:
    if not case.get('project_id'):
        continue
    p=db.project(case['project_id'])
    interventions=db.query("SELECT kind,data FROM events WHERE project_id=? AND kind IN ('reviewed_action_staged','run_recovered','run_reviewed','manual_reviewed')",(p['id'],))
    files=[]
    workspace=config.project_dir(p['id'])/'workspace'
    for path in sorted(workspace.rglob('*')):
        if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve())
                or path.relative_to(workspace).parts[0]=='inputs'):
            continue
        size=path.stat().st_size
        files.append({'path':path.relative_to(workspace).as_posix(),'bytes':size,
                      'sha256':hashlib.sha256(path.read_bytes()).hexdigest() if size<5_000_000 else 'large file; not hashed in this inventory'})
    audit['cases'].append({
        'subject':case['subject'],'project_id':p['id'],'status':p['status'],
        'whole_lab_independently_accepted':False,
        'fixed_version_unchanged':case.get('fixed_version_unchanged'),
        'known_developer_interventions':interventions,
        'steps':[{'title':s['spec']['title'],'status':s['status'],'error':s['error'],
                  'run_id':s['result'].get('run_id'),'exit_code':s['result'].get('exit_code'),
                  'checks':s['result'].get('checks',[]),
                  'assert_execution_receipts':[line for line in s['result'].get('log','').splitlines() if line.startswith('STUDY_ASSERTS_EXECUTED')],
                  'manual_instructions':s['result'].get('instructions','') if s['status']=='waiting_user' else ''}
                 for s in p['steps']],
        'artifacts':[{'name':a['name'],'state':a['state'],'path':a['path']} for a in p['artifacts']],
        'workspace_files':files,
    })
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(audit,ensure_ascii=False,indent=2),'utf-8')
print(args.output)
