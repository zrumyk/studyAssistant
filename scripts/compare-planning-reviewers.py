"""Run one predeclared SA-107 development control; no lab analyse/approve/execute."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from study import cloud, config, db
from study.assistance import BudgetedModel
from study.planning_evaluation import evaluate_case, summarize
from study.providers import OllamaProvider
from study.plan_review import plan_review_prompt
from study.schemas import Plan


def guard(project_id=None, max_calls=None):
    with sqlite3.connect(config.DB_PATH.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        if conn.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
            raise RuntimeError('Active app job; control not started')
        if project_id:
            count = conn.execute('SELECT count(*) FROM cloud_requests WHERE project_id=?', (project_id,)).fetchone()[0]
            if count >= max_calls:
                raise RuntimeError('Experiment total paid call ceiling reached; inspect cache, no automatic retry')
            if conn.execute("SELECT 1 FROM cloud_requests WHERE project_id=? AND state!='done'", (project_id,)).fetchone():
                raise RuntimeError('Unconfirmed or failed receipt; stop control and inspect before another paid case')
    if shutil.disk_usage(ROOT).free < 11 * 2**30:
        raise RuntimeError('Preserve10 GiB reserve; require11 GiB free before control')
    if project_id:
        for process in psutil.process_iter(['cmdline']):
            command = ' '.join(process.info['cmdline'] or [])
            if 'study.worker' in command and str(ROOT).casefold() in command.casefold():
                raise RuntimeError('Stop this app worker before inline cloud control to avoid competing consumers')


def receipts(project_id):
    if not project_id:
        return []
    result = []
    for row in db.query('SELECT id,purpose,state,reserved,actual,response,config,created FROM cloud_requests WHERE project_id=? ORDER BY created', (project_id,)):
        snapshot = json.loads(row.pop('config'))
        row['limits'] = {key: snapshot.get(key) for key in ('cloud_provider', 'cloud_model', 'cloud_thinking',
                        'cloud_max_output', 'cloud_budget', 'cloud_request_limit_usd', 'approval_kind')}
        # Paid raw text is preserved even on truncation. Do not export credentials/settings.
        jobs = db.query("SELECT status,result,error FROM jobs WHERE kind='cloud' AND payload=? ORDER BY created DESC LIMIT 1",
                        (json.dumps({'id': row['id']}, ensure_ascii=False),))
        row['job'] = jobs[0] if jobs else None
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, default=ROOT/'docs/verification/planning-reviewer-control-v1.json')
    parser.add_argument('--group', required=True)
    parser.add_argument('--project-id', help='Dedicated technical receipt project, never a lab')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.experiment.read_text('utf-8'))
    group = spec['groups'][args.group]
    baseline_path = ROOT/spec['baseline']['path']
    cases_path = ROOT/spec['cases']['path']
    for path, reference in ((baseline_path, spec['baseline']), (cases_path, spec['cases'])):
        if hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
            raise ValueError('Frozen experiment input changed: '+str(path))
    cases = json.loads(cases_path.read_text('utf-8'))['cases']
    selected = [case for case in cases if case['id'] in group['case_ids']]
    if len(selected) != len(group['case_ids']):
        raise ValueError('Unknown or duplicate case IDs')
    is_cloud = group['mode'] != 'local'
    if is_cloud:
        if not args.project_id:
            raise ValueError('Cloud requires a dedicated technical receipt project')
        project = db.project(args.project_id)
        if (project['title'] != spec['receipt_project_title'] or project['status'] != 'paused'
                or project['approved_hash'] is not None or project['steps']):
            raise ValueError('Receipt project must be paused, unapproved and contain no execution steps')
    elif args.project_id:
        raise ValueError('Local controls do not use a project')
    guard(args.project_id, spec['max_paid_calls_total'])
    baseline = json.loads(baseline_path.read_text('utf-8'))
    facts = json.dumps(baseline['platform_facts'], ensure_ascii=False)
    old_prompts = {row['id']: row['prompt'] for row in baseline['rows'] if 'prompt' in row}
    for case in selected:
        if case['id'] in old_prompts:
            prompt = plan_review_prompt(task=case['task'], variant='', sources=case['sources'],
                                        plan=Plan.model_validate(case['plan']), platform_facts=facts)
            if prompt != old_prompts[case['id']]:
                raise ValueError('Prompt changed from frozen baseline: '+case['id'])
    with args.output.open('x', encoding='utf-8') as out:
        out.write('{}\n')
    report = {'scope': spec['scope'], 'hypothesis': spec['hypothesis'], 'group': args.group,
              'captured_utc': datetime.now(timezone.utc).isoformat(), 'experiment_sha256': hashlib.sha256(args.experiment.read_bytes()).hexdigest(),
              'baseline_sha256': spec['baseline']['sha256'], 'cases_sha256': spec['cases']['sha256'],
              'limits': group, 'platform_facts': baseline['platform_facts'], 'rows': [], 'finished': False,
              'project_id': args.project_id, 'source_hashes': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in (Path(__file__), ROOT/'backend/study/assistance.py', ROOT/'backend/study/plan_review.py',
                           ROOT/'backend/study/planning_evaluation.py', ROOT/'backend/study/providers.py')}}

    def save():
        report['receipts'] = receipts(args.project_id)
        report['summary'] = summarize(report['rows'], len(selected))
        report['paid_request_count_total'] = len(report['receipts'])
        report['paid_estimate_usd_total'] = sum(row['actual'] or 0 for row in report['receipts'])
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    save()
    local = None
    try:
        if is_cloud:
            model = BudgetedModel(args.project_id, OllamaProvider(), 'SA107 frozen reviewer control v1',
                                  prefer_cloud=True, thinking_override=group['thinking'],
                                  bounded_thinking_fallback=group['fallback'], max_output_tokens=group['output_tokens'],
                                  cancelled=lambda: (len(db.query('SELECT id FROM cloud_requests WHERE project_id=?',
                                                                (args.project_id,))) >= spec['max_paid_calls_total']
                                                     or bool(db.query("SELECT id FROM cloud_requests WHERE project_id=? AND state!='done'", (args.project_id,)))))
            review = model.remote_with_fallback
        else:
            if psutil.virtual_memory().available < 5 * 2**30:
                raise RuntimeError('Require5 GiB available RAM before local load')
            if config.settings()['model'] != baseline['model']:
                raise ValueError('Local model differs from baseline')
            tags = httpx.get(config.settings()['ollama_url']+'/api/tags', timeout=10, trust_env=False)
            tags.raise_for_status()
            identity = next((item for item in tags.json()['models'] if item['name'] == baseline['model']), None)
            if not identity or identity['digest'] != baseline['model_identity']['digest']:
                raise ValueError('Local model weights differ from frozen baseline')
            report['model_identity'] = identity
            local = OllamaProvider(context_override=8192, max_output_tokens=group['output_tokens'], timeout_seconds=group['timeout_seconds'])
            review = local.generate
        for case in selected:
            guard(args.project_id, spec['max_paid_calls_total'])
            row = evaluate_case(case, facts, review)
            if is_cloud:
                row['last_request_id'] = model.last_request_id
            report['rows'].append(row)
            save()
            print(json.dumps({'id': row['id'], 'semantic': row['semantic'], 'match': row.get('semantic_match')}, ensure_ascii=False), flush=True)
        report['finished'] = True
        save()
        print(json.dumps(report['summary'], ensure_ascii=False), flush=True)
        summary = report['summary']
        return int(bool(summary['structural_mismatches'] or summary['false_accepts'] or summary['false_rejects'] or summary['errors']))
    except Exception as error:
        report['fatal_error'] = str(error)
        save()
        raise
    finally:
        if local:
            local.unload()


if __name__ == '__main__':
    raise SystemExit(main())
