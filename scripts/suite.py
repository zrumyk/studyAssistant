"""End-to-end task suite: drives unedited generated plans through the public API and grades results with oracles.

The operator only approves generated plans without edits. Generated code, numbers, plans and DB states are never changed.
"""
import argparse
import datetime
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'suite' / 'tasks'
DB = ROOT / 'data' / 'study.sqlite3'
OUT = ROOT / 'data' / 'verification' / 'suite'
API = 'http://127.0.0.1:18573'
STAGES = ['created', 'planned', 'approved', 'executed', 'oracle', 'report', 'defense']
TERMINAL = {'completed', 'needs_user', 'error', 'paused'}


def load_oracle(task_dir):
    spec = importlib.util.spec_from_file_location('oracle_' + task_dir.name.replace('-', '_'), task_dir / 'oracle.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tasks(only=None):
    found = {p.name: p for p in TASKS.iterdir() if (p / 'task.json').exists()}
    return [found[name] for name in only if name in found] if only else [found[k] for k in sorted(found)]


def git_state():
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'backend', 'frontend', 'scripts', 'suite'],
                           cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return commit, bool(dirty)


def spend(pid):
    """Estimated spend of one project in USD: actual cost, or the reserve while the outcome is unknown."""
    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as c:
        row = c.execute("SELECT COALESCE(SUM(CASE WHEN actual IS NOT NULL THEN actual "
                        "WHEN state IN ('sent','uncertain') THEN reserved ELSE 0 END),0), COUNT(*) "
                        "FROM cloud_requests WHERE project_id=?", (pid,)).fetchone()
    return float(row[0]), int(row[1])


def jobs_of(pid, kind=None):
    """All non-cloud jobs of a project from the DB; the API returns only the 10 newest, crowded out by model calls."""
    with sqlite3.connect(f'file:{DB}?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT id,kind,status,error,created FROM jobs WHERE project_id=? AND kind!='cloud' "
                         "AND (? IS NULL OR kind=?) ORDER BY created DESC", (pid, kind, kind)).fetchall()
    return [dict(r) for r in rows]


def find_outputs(pid, names):
    """Newest file with each expected name produced by the project, never from imported inputs/."""
    base = ROOT / 'data' / 'projects' / pid
    found = {}
    for name in names:
        candidates = [p for p in base.rglob(name) if p.is_file() and 'inputs' not in p.relative_to(base).parts]
        if candidates:
            found[name] = max(candidates, key=lambda p: p.stat().st_mtime)
    return found


def newest(pid, pattern):
    files = [p for p in (ROOT / 'data' / 'projects' / pid).rglob(pattern) if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml').decode('utf-8', 'replace')
    import re
    return re.sub(r'<[^>]+>', '', re.sub(r'</w:p>', '\n', xml))


def grade(task_dir, pid, settings):
    """Oracle, negative control and report checks for a finished project. Reads files only."""
    meta = json.loads((task_dir / 'task.json').read_text('utf-8'))
    oracle = load_oracle(task_dir)
    files = find_outputs(pid, meta['outputs'])
    passed, problems, keys = oracle.evaluate(files)
    negative_ok = None
    if passed:
        with tempfile.TemporaryDirectory() as tmp:
            negative_ok = not oracle.evaluate(oracle.mutate(files, Path(tmp)))[0]
    report = newest(pid, 'report.docx')
    report_pdf = newest(pid, 'report.pdf')
    defense = newest(pid, 'defense.docx')
    report_checks = {}
    if report:
        text = docx_text(report)
        report_checks = {
            'author': bool(settings.get('author')) and settings['author'] in text,
            'key_numbers': [k for k in keys if k in text or k.replace('.', ',') in text],
            'missing_numbers': [k for k in keys if k not in text and k.replace('.', ',') not in text],
            'pdf': bool(report_pdf and report_pdf.stat().st_size > 1000),
        }
    return {
        'outputs': {k: str(v.relative_to(ROOT)) for k, v in files.items()},
        'oracle_passed': passed,
        'oracle_problems': problems[:10],
        'negative_control_rejected': negative_ok,
        'report': str(report.relative_to(ROOT)) if report else None,
        'report_checks': report_checks,
        'defense': str(defense.relative_to(ROOT)) if defense else None,
    }


def passed_fully(result):
    """Full pass ends at the report while the defense is switched off (user decision 13.11)."""
    final = 'defense' if result.get('defense_enabled', True) else 'report'
    return STAGES.index(result['stage']) >= STAGES.index(final)


def stage_of(result):
    stage = result.get('stage', 'created')
    g = result.get('grading') or {}
    if result.get('executed'):
        stage = 'executed'
        if g.get('oracle_passed') and g.get('negative_control_rejected'):
            stage = 'oracle'
            rc = g.get('report_checks') or {}
            if g.get('report') and rc.get('pdf') and rc.get('author') and not rc.get('missing_numbers'):
                stage = 'report'
                if g.get('defense'):
                    stage = 'defense'
    return stage


def stop_reason(p):
    for step in p['steps']:
        if step['status'] in ('failed', 'blocked', 'waiting_user'):
            return f"step {step['position']} {step['spec'].get('kind')} {step['status']}: {(step['error'] or '')[:400]}"
    for job in p['jobs']:
        if job['status'] in ('failed', 'interrupted') and job.get('error'):
            return f"job {job['kind']} {job['status']}: {job['error'][:400]}"
    return p['status']


def get_json(client, url):
    """GET with retries: a dropped local connection must not abort a paid run."""
    for attempt in range(6):
        try:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))


def run_task(client, task_dir, settings, run_id, budget_left, timeout_min, attach=None):
    meta = json.loads((task_dir / 'task.json').read_text('utf-8'))
    task_text = (task_dir / meta['task_file']).read_text('utf-8-sig').strip()
    result = {'task': task_dir.name, 'raw_input': meta.get('raw', False), 'stage': 'created', 'executed': False,
              'started': datetime.datetime.now().isoformat(timespec='seconds')}
    imports = []
    analysed = approved = False
    if attach:
        pid = attach
        p = get_json(client, f'/api/projects/{pid}')
        analysed = True
        approved = bool(p['approved_hash'])
        result['attached'] = True
        if approved:
            result['stage'] = 'approved'
    else:
        r = client.post('/api/projects', json={'title': f"{meta['title']} [{run_id}]", 'subject': meta['subject'],
                                               'variant': meta.get('variant', ''), 'task': task_text})
        r.raise_for_status()
        pid = r.json()['id']
        for doc in meta.get('documents', []):
            r = client.post('/api/import', json={'path': str((task_dir / doc['file']).resolve()), 'role': doc['role'],
                                                  'subject': meta['subject'], 'project_id': pid})
            r.raise_for_status()
            imports.append(r.json()['job_id'])
            # The API accepts one job per project at a time; wait before importing the next file.
            while any(j['id'] == imports[-1] and j['status'] in ('queued', 'running') for j in jobs_of(pid, 'import')):
                time.sleep(2)
    result['project_id'] = pid
    deadline = time.time() + timeout_min * 60
    while True:
        time.sleep(5)
        p = get_json(client, f'/api/projects/{pid}')
        cost, calls = spend(pid)
        result.update(cost_usd=round(cost, 6), model_calls=calls, status=p['status'])
        all_jobs = jobs_of(pid)
        p['jobs'] = all_jobs
        jobs = {j['id']: j for j in all_jobs}
        active = any(j['status'] in ('queued', 'running') for j in all_jobs)
        if cost > budget_left:
            client.post(f'/api/projects/{pid}/pause')
            result['reason'] = f'budget: suite limit reached at ${cost:.4f}'
            break
        if time.time() > deadline:
            client.post(f'/api/projects/{pid}/pause')
            result['reason'] = f'timeout {timeout_min} min'
            break
        if not analysed:
            if all(jobs.get(j, {}).get('status') == 'done' for j in imports) or not imports:
                client.post(f'/api/projects/{pid}/analyse').raise_for_status()
                analysed = True
            elif any(jobs.get(j, {}).get('status') in ('failed', 'interrupted') for j in imports):
                result['reason'] = 'import failed: ' + stop_reason(p)
                break
            continue
        if active:
            continue
        if p['status'] == 'awaiting_approval' and not approved:
            result['stage'] = 'planned'
            plan = p['plan']
            result['plan_steps'] = [f"{s['kind']}:{s.get('language', '')}:{s['title']}" for s in plan.get('steps', [])]
            if plan.get('missing') or plan.get('conflicts'):
                result['reason'] = 'clarification: ' + json.dumps({'missing': plan.get('missing'), 'conflicts': plan.get('conflicts')}, ensure_ascii=False)[:600]
                break
            client.post(f'/api/projects/{pid}/approve').raise_for_status()
            client.post(f'/api/projects/{pid}/execute').raise_for_status()
            approved = True
            result['stage'] = 'approved'
            continue
        if p['status'] in TERMINAL and approved:
            done = [s for s in p['steps'] if s['spec'].get('kind') != 'report']
            result['executed'] = bool(done) and all(s['status'] == 'done' for s in done)
            if p['status'] == 'completed':
                defense_jobs = [j for j in p['jobs'] if j['kind'] == 'defense']
                if not defense_jobs and settings.get('auto_defense', True):
                    continue  # defense is enqueued right after completion
                if not defense_jobs:
                    result['reason'] = 'completed (захист вимкнено в налаштуваннях)'
                elif defense_jobs[0]['status'] == 'failed':
                    result['reason'] = 'defense failed: ' + (defense_jobs[0].get('error') or '')[:400]
                else:
                    result['reason'] = 'completed'
            else:
                result['reason'] = stop_reason(p)
            break
        if p['status'] in TERMINAL and not approved:
            result['reason'] = 'planning: ' + stop_reason(p)
            break
    result['finished'] = datetime.datetime.now().isoformat(timespec='seconds')
    try:
        result['grading'] = grade(task_dir, pid, settings)
    except Exception as error:  # an oracle defect must not stop the rest of the paid run
        result['grading'] = {'oracle_error': f'{type(error).__name__}: {error}'}
    result['stage'] = stage_of(result)
    result['defense_enabled'] = settings.get('auto_defense', True)
    result['full_pass'] = passed_fully(result)
    return result


def summary(results):
    counts = {s: sum(STAGES.index(r['stage']) >= STAGES.index(s) for r in results) for s in STAGES}
    return {'tasks': len(results), 'reached': counts, 'full_pass': sum(r.get('full_pass', False) for r in results),
            'cost_usd': round(sum(r.get('cost_usd', 0) for r in results), 6)}


def cmd_run(args):
    import httpx
    commit, dirty = git_state()
    run_id = datetime.datetime.now().strftime('%Y%m%d-%H%M') + '-' + commit + ('-dirty' if dirty else '')
    folder = OUT / run_id
    folder.mkdir(parents=True, exist_ok=True)
    record = {'run_id': run_id, 'commit': commit, 'dirty': dirty, 'budget_usd': args.budget, 'results': []}
    attach = dict(item.split('=', 1) for item in (args.attach or []))
    limits = httpx.Limits(max_keepalive_connections=0)
    with httpx.Client(base_url=API, trust_env=False, timeout=60, limits=limits) as client:
        client.headers['x-study-token'] = client.get('/api/session').json()['token']
        settings = client.get('/api/settings').json()
        record['model'] = {k: settings.get(k) for k in ('cloud_provider', 'cloud_model', 'cloud_thinking', 'model', 'coding_model')}
        spent = 0.0
        for task_dir in tasks(args.only):
            if spent >= args.budget:
                record['results'].append({'task': task_dir.name, 'stage': 'created', 'reason': 'skipped: suite budget exhausted'})
                continue
            print('start', task_dir.name, flush=True)
            res = run_task(client, task_dir, settings, run_id, args.budget - spent, args.timeout, attach.get(task_dir.name))
            spent += res.get('cost_usd', 0)
            record['results'].append(res)
            record['summary'] = summary(record['results'])
            (folder / 'results.json').write_text(json.dumps(record, ensure_ascii=False, indent=1), 'utf-8')
            print(task_dir.name, res['stage'], res.get('reason', '')[:200], f"${res.get('cost_usd', 0):.4f}", flush=True)
    record['summary'] = summary(record['results'])
    (folder / 'results.json').write_text(json.dumps(record, ensure_ascii=False, indent=1), 'utf-8')
    print(json.dumps(record['summary'], ensure_ascii=False), flush=True)
    print('results:', folder / 'results.json')


def cmd_check(args):
    import httpx
    record = json.loads(Path(args.results).read_text('utf-8'))
    with httpx.Client(base_url=API, trust_env=False, timeout=60) as client:
        client.headers['x-study-token'] = client.get('/api/session').json()['token']
        settings = client.get('/api/settings').json()
    for res in record['results']:
        if res.get('project_id'):
            res['grading'] = grade(TASKS / res['task'], res['project_id'], settings)
            res['stage'] = stage_of(res)
            res['full_pass'] = passed_fully(res)
    record['summary'] = summary(record['results'])
    Path(args.results).write_text(json.dumps(record, ensure_ascii=False, indent=1), 'utf-8')
    print(json.dumps(record['summary'], ensure_ascii=False))


def cmd_selftest(args):
    ok = True
    for task_dir in tasks(args.only):
        oracle = load_oracle(task_dir)
        with tempfile.TemporaryDirectory() as tmp:
            ref = oracle.reference(Path(tmp))
            good = oracle.evaluate(ref)
            bad = oracle.evaluate(oracle.mutate(ref, Path(tmp)))
            missing = oracle.evaluate({})
        line_ok = good[0] and not bad[0] and not missing[0]
        ok &= line_ok
        print(f"{task_dir.name}: reference={good[0]} mutated_rejected={not bad[0]} empty_rejected={not missing[0]} keys={good[2]}"
              + ('' if good[0] else f' problems={good[1]}'))
    sys.exit(0 if ok else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    run = sub.add_parser('run')
    run.add_argument('--only', nargs='*')
    run.add_argument('--budget', type=float, default=0.50, help='suite spend limit in USD')
    run.add_argument('--timeout', type=int, default=90, help='minutes per task')
    run.add_argument('--attach', nargs='*', help='task=project_id: continue watching an already created project')
    check = sub.add_parser('check')
    check.add_argument('results')
    selftest = sub.add_parser('selftest')
    selftest.add_argument('--only', nargs='*')
    args = parser.parse_args()
    {'run': cmd_run, 'check': cmd_check, 'selftest': cmd_selftest}[args.cmd](args)


if __name__ == '__main__':
    main()
