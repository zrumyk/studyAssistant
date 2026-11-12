"""Drive unedited generated plans through the public API; never author lab code."""
import argparse
import hashlib
import json
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('manifest',type=Path)
parser.add_argument('--watch',action='store_true')
parser.add_argument('--resume-after-update',action='store_true',help='Resume technical failures through public APIs; never confirm manual evidence')
args=parser.parse_args()


def version():
    return hashlib.sha256(''.join(str(p.relative_to(ROOT))+hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'backend').rglob('*.py'))).encode()).hexdigest()


def save(manifest):
    temp=args.manifest.with_suffix('.tmp')
    temp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    temp.replace(args.manifest)


with httpx.Client(base_url='http://127.0.0.1:18573',trust_env=False,timeout=60,limits=httpx.Limits(max_keepalive_connections=0)) as client:
    client.headers['x-study-token']=client.get('/api/session').json()['token']
    manifest=json.loads(args.manifest.read_text('utf-8'))
    documents=client.get('/api/documents').json()
    if args.resume_after_update:
        for case in manifest['cases']:
            if not case.get('project_id') or case.get('operator_hold'):
                continue
            pid=case['project_id']
            p=client.get('/api/projects/'+pid).json()
            active=any(j['status'] in ('running','queued') for j in p['jobs'])
            if active or any(s['status']=='waiting_user' for s in p['steps']):
                continue
            if not p['approved_hash'] and p['status']=='error':
                r=client.post(f'/api/projects/{pid}/analyse');r.raise_for_status()
                case['analyse_job']=r.json()['job_id']
            elif p['approved_hash'] and p['status'] in ('paused','needs_user','error'):
                failed=next((s for s in p['steps'] if s['status'] in ('failed','blocked')),None)
                if failed:
                    r=client.post(f'/api/projects/{pid}/steps/{failed["id"]}/retry');r.raise_for_status()
                r=client.post(f'/api/projects/{pid}/execute');r.raise_for_status()
                case.setdefault('previous_execute_jobs',[]).append(case.get('execute_job'))
                case['execute_job']=r.json()['job_id']
            else:
                continue
            case['resumed_after_application_update']=True
            case['resume_backend_sha256']=version()
            save(manifest)
            print('resumed technical case through public API:',case['subject'],flush=True)
    for case in manifest['cases']:
        if 'project_id' in case:
            continue
        if not case.get('task_brief_file'):
            raise ValueError('Every new test requires task_brief_file prepared after reviewing its primary lab instructions')
        if not case.get('task_documents'):
            raise ValueError('Every new test requires primary lab documents')
        if case.get('task_brief_file'):
            brief=Path(case['task_brief_file'])
            if not brief.is_absolute():
                brief=ROOT/brief
            task=brief.read_text('utf-8-sig').strip()
            if not 1<=len(task)<=30000:
                raise ValueError('Prepared task brief must contain 1–30000 characters')
            case['task']=task
            case['task_brief_sha256']=hashlib.sha256(task.encode('utf-8')).hexdigest()
            case['task_brief_origin']='Prepared from the primary lab sources before execution; contains requirements, not lab solutions'
        ids=[]
        for name in case['task_documents']:
            found=[d for d in documents if d['name']==name and d['subject']==case['subject'] and d['status']!='superseded']
            if not found:
                raise RuntimeError('Missing primary source: '+name)
            ids.append(found[0]['id'])
        r=client.post('/api/projects',json={'title':case['title'],'subject':case['subject'],'variant':'1','task':case['task']})
        r.raise_for_status(); pid=r.json()['id'];case['project_id']=pid
        r=client.put(f'/api/projects/{pid}/sources',json={'document_ids':ids});r.raise_for_status()
        case['source_selection']=ids
        case['backend_sha256']=version()
        if case.get('data_folder'):
            r=client.post('/api/import',json={'path':case['data_folder'],'role':'data','subject':case['subject'],'project_id':pid})
            r.raise_for_status();case['input_job']=r.json()['job_id']
        else:
            r=client.post(f'/api/projects/{pid}/analyse');r.raise_for_status()
            case['analyse_job']=r.json()['job_id']
        case['acceptance']='not_accepted'
        save(manifest)
        print('created',case['subject'],pid,flush=True)
    while True:
        terminal=0
        for case in manifest['cases']:
            pid=case['project_id']
            r=client.get('/api/projects/'+pid);r.raise_for_status();p=r.json()
            case['status']=p['status']
            case['fixed_version_unchanged']=case['backend_sha256']==version()
            case['steps']=[{'title':s['spec']['title'],'status':s['status'],'attempts':s['attempts'],'error':s['error'],'run_id':s['result'].get('run_id')} for s in p['steps']]
            if not case.get('analyse_job'):
                job=next((job for job in p['jobs'] if job['id']==case['input_job']),None)
                if job and job['status']=='done':
                    r=client.post(f'/api/projects/{pid}/analyse');r.raise_for_status()
                    case['analyse_job']=r.json()['job_id']
                elif job and job['status'] in ('failed','interrupted'):
                    case['acceptance']='input_import_failed';terminal+=1
                continue
            if p['status']=='awaiting_approval' and not case.get('execute_job'):
                plan=p['plan']
                if plan['missing'] or plan['conflicts']:
                    case['acceptance']='waiting_for_clarification';terminal+=1
                    continue
                if case.get('independent_plan_review_required'):
                    case['acceptance']='awaiting_independent_plan_review';terminal+=1
                    continue
                if not plan['steps'] or plan['steps'][-1]['kind']!='report':
                    case['acceptance']='invalid_generated_plan';terminal+=1
                    continue
                case['approved_plan_sha256']=hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                case['plan_approved_unchanged_by_test_operator']=True
                r=client.post(f'/api/projects/{pid}/approve');r.raise_for_status()
                r=client.post(f'/api/projects/{pid}/execute');r.raise_for_status()
                case['execute_job']=r.json()['job_id']
                print('approved generated plan unchanged:',case['subject'],flush=True)
            if p['status'] in ('completed','needs_user','error'):
                terminal+=1
                case['acceptance']='awaiting_independent_output_review' if p['status']=='completed' else 'not_completed'
        save(manifest)
        if terminal==len(manifest['cases']) or not args.watch:
            print('suite snapshot saved',flush=True)
            break
        time.sleep(5)
