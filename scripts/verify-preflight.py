"""Bounded review of one unchanged candidate; never executes or repairs lab code.

Stop the application's worker first. This command owns its worker lock while
BudgetedModel performs any authorized child requests inline. Original source,
plan, step status and attempt count must remain unchanged.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from study import config, db, preflight  # noqa: E402
from study.schemas import ActionCheckpoint  # noqa: E402


def fingerprint():
    files=sorted((ROOT/'backend').rglob('*.py'))
    return hashlib.sha256(''.join(str(p.relative_to(ROOT))+hashlib.sha256(p.read_bytes()).hexdigest() for p in files).encode()).hexdigest()


def state(pid,sid):
    return {'project':db.one('SELECT status,revision,approved_hash,plan FROM projects WHERE id=?',(pid,)),
            'step':db.one('SELECT status,attempts,error,result FROM steps WHERE id=? AND project_id=?',(sid,pid))}


def verify(pid,sid,output):
    with socket.socket() as lock:
        # A running worker owns this port. Never compete for its cloud jobs.
        lock.bind(('127.0.0.1',18574))
        if db.query("SELECT id FROM jobs WHERE status IN ('running','queued')"):
            raise RuntimeError('Active jobs exist; review not started')
        settings=config.settings()
        if settings['cloud_consent_mode']!='budgeted' or settings['cloud_budget']>5 or settings['cloud_request_limit_usd']>0.03:
            raise RuntimeError('Expected standing budget <= $5/month and <= $0.03/request')
        before=state(pid,sid)
        project=db.project(pid)
        step=next(s for s in project['steps'] if s['id']==sid)
        spec=step['spec']
        if spec['kind']!='code' or spec['timeout_seconds']<1800:
            raise ValueError('Candidate does not use expensive-action preflight')
        folder=config.project_dir(pid)/'codegen'/sid
        checkpoint=folder/'last-action.json'
        original=checkpoint.read_bytes()
        saved=ActionCheckpoint.model_validate_json(original)
        if not saved.written_to_workspace or saved.revision!=project['revision']:
            raise ValueError('Expected current written candidate')
        workspace=config.project_dir(pid)/'workspace'
        for file in saved.action.files+saved.action.checks:
            if config.safe_path(workspace,file.path).read_text('utf-8')!=file.content:
                raise ValueError('Workspace differs from candidate: '+file.path)
        sources=preflight.review_sources(pid,saved.action)
        source_hashes={name:hashlib.sha256(content.encode()).hexdigest() for name,content in sources.items()}
        request_ids={r['id'] for r in db.query('SELECT id FROM cloud_requests')}
        result={'captured_utc':datetime.now(timezone.utc).isoformat(),'backend_sha256':fingerprint(),
                'project_id':pid,'step_id':sid,'scope':'Preflight only; no code repair, Docker build or lab execution.',
                'source_sha256':source_hashes,'candidate_sha256':hashlib.sha256(original).hexdigest()}
        try:
            review=preflight.check_expensive_action(pid,sid,spec,saved.action)
            result.update(outcome='no_confirmed_conflicts',review=review.model_dump())
        except ValueError as error:
            try:
                failure=json.loads(str(error))
            except ValueError:
                result.update(outcome='review_error',error=str(error))
            else:
                result.update(outcome='blocked' if failure.get('preflight') else 'review_error',review=failure)
        except Exception as error:
            result.update(outcome='review_error',error_type=type(error).__name__,error=str(error)[:1500])
        if result['outcome']!='review_error':
            reusable,normalized=preflight.recheck_before_repair(pid,sid,spec,project['revision'],workspace,step['error'])
            result['repair_reuses_unchanged_action']=reusable is not None
            result['normalized_previous_failure']=json.loads(normalized) if normalized else None
        after=state(pid,sid)
        result['project_and_step_state_unchanged']=before==after
        result['candidate_unchanged']=checkpoint.read_bytes()==original
        result['sources_unchanged']=all(config.safe_path(workspace,name).is_file() and hashlib.sha256(config.safe_path(workspace,name).read_text('utf-8',errors='replace').encode()).hexdigest()==digest for name,digest in source_hashes.items())
        result['new_cloud_requests']=[r for r in db.query('SELECT id,state,month,actual,reserved FROM cloud_requests') if r['id'] not in request_ids]
        result['adjudication_files']=[str(p.relative_to(ROOT)) for p in folder.glob('adjudication-*.json')]
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if not all(result[k] for k in ('project_and_step_state_unchanged','candidate_unchanged','sources_unchanged')):
            raise RuntimeError('Preflight modified protected lab state')
        return 1 if result['outcome']=='review_error' else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True)
    parser.add_argument('--step',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(verify(args.project,args.step,args.output))
