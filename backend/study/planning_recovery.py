"""Explicit bounded continuation of a proven complete, unapproved plan checkpoint."""
import hashlib
import json
import re
from pathlib import Path

from . import config, db
from .plan_patches import PlanPatch, apply_plan_patch, canonical, draft_hash
from .schemas import PlanDraft

MAX_RECOVERIES = 2


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_state(pid):
    from .planning_sources import planning_sources
    from .search import scope
    from .planning_facts import planning_facts
    p = db.project(pid)
    _, _, coverage = planning_sources(pid, p['task'])
    if coverage['blockers']:
        raise ValueError('Неповні поточні джерела: ' + '; '.join(coverage['blockers']))
    condition, args = scope(pid)
    files = []
    for doc in db.query('SELECT d.id,d.path,d.hash,d.profile FROM documents d WHERE ' + condition + ' ORDER BY d.id', args):
        path = Path(doc['path'])
        if path.is_symlink() or not path.resolve().is_relative_to((config.DATA / 'documents').resolve()):
            raise ValueError('Некоректний шлях джерела')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != doc['hash']:
            raise ValueError('Байти джерела змінилися; потрібен повторний імпорт')
        files.append([doc['id'], actual, doc['profile']])
    inputs = []
    root = config.project_dir(pid) / 'workspace' / 'inputs'
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink у planning inputs')
        if path.is_file():
            with path.open('rb') as stream:
                inputs.append([path.relative_to(root).as_posix(), hashlib.file_digest(stream, 'sha256').hexdigest()])
    docs = [{k: d[k] for k in ('document_id','name','role','status','index_sha256','workspace_path','warnings')}
            for d in coverage['documents']]
    settings = config.settings()
    policy = {k: settings[k] for k in ('cloud_consent_mode','cloud_provider','cloud_model','cloud_request_limit_usd','cloud_budget',
                                     'cloud_thinking','cloud_max_output','cloud_input_price','cloud_output_price','cloud_cache_input_price',
                                     'cloud_prices_date','cloud_deepseek_offpeak','model','coding_model','context','author','group','institution')}
    if (policy['cloud_consent_mode'] != 'budgeted' or not 0 < policy['cloud_request_limit_usd'] <= .03
            or not 0 < policy['cloud_budget'] <= 5):
        raise ValueError('Немає чинного бюджетного дозволу на planning recovery')
    code = [[path.name, hashlib.sha256(path.read_bytes()).hexdigest()] for path in sorted(Path(__file__).parent.glob('*.py'))]
    recent = [m['content'] for m in p['messages'] if m['role']=='user'][-8:]
    return {'project': [p[k] for k in ('revision','title','subject','task','variant')], 'documents': docs,
            'files': files, 'inputs': inputs, 'policy': policy, 'code': code,
            'facts': planning_facts(pid), 'recent_user_messages': recent}


def _complete_outcome(pid, job):
    uses = db.query("SELECT data FROM events WHERE project_id=? AND kind='automatic_assistance' AND created>=? AND created<=? ORDER BY id DESC",
                    (pid,job['created'],job['updated']))
    uses = [json.loads(use['data']) for use in uses]
    if not uses:
        raise ValueError('Немає підтвердженого planning outcome')
    row = db.one('SELECT * FROM cloud_requests WHERE id=? AND project_id=?',(uses[0]['request_id'],pid))
    if (row['state'] != 'done' or uses[0].get('complete') is not True
            or db.query("SELECT id FROM cloud_requests WHERE project_id=? AND created>=? AND created<=? AND state IN ('sending','uncertain')",
                        (pid,job['created'],job['updated']))):
        raise ValueError('Останній planning outcome unknown/truncated;recovery не відкривається')
    jobs = db.query("SELECT result FROM jobs WHERE project_id=? AND kind='cloud' AND status='done' AND json_extract(payload,'$.id')=?",(pid,row['id']))
    if len(jobs) != 1 or json.loads(jobs[0]['result']).get('complete') is not True:
        raise ValueError('Остання model квитанція не підтверджує complete outcome')
    return row, json.loads(jobs[0]['result']), uses


def _terminal_candidate(pid, job, checkpoint_sha256, depth):
    """A new explicit operation, never automatic replay or a cap reset."""
    if job['kind'] != 'recover_plan' or not job['error'].startswith('Потрібне окреме перепланування: '):
        raise ValueError('Потрібен останній checkpoint цього failed job')
    intent = db.one('SELECT * FROM planning_recoveries WHERE project_id=? AND job_id=?',(pid,job['id']))
    if intent['checkpoint_sha256'] != checkpoint_sha256 or intent['revision'] != db.project(pid)['revision']:
        raise ValueError('Terminal recovery має інший checkpoint/revision')
    draft, _, state = candidate(pid,intent['source_job_id'],checkpoint_sha256,_depth=depth+1)
    row, receipt, _ = _complete_outcome(pid,job)
    if json.loads(row['config']).get('project_revision') != intent['revision']:
        raise ValueError('Terminal receipt revision змінилася')
    from .assistance import structured_response
    patch = structured_response(receipt['text'].strip(),PlanPatch)
    if (not patch.requires_replan or patch.edits or patch.base_sha256 != draft_hash(draft)
            or job['error'] != 'Потрібне окреме перепланування: ' + patch.explanation):
        raise ValueError('Terminal requires_replan не підтверджений exact complete model receipt')
    from .artifact_repair import graph_policy_marker
    marker = graph_policy_marker(draft)
    if not marker or marker in row['prompt']:
        raise ValueError('Немає нової enforced graph policy для переоцінки terminal outcome;повтор заборонено')
    # A policy is a concrete local ownership witness + enforcement identity;
    # incidental model/settings/source changes alone never grant another try.
    return draft, job['error'], state


def candidate(pid, source_job_id, checkpoint_sha256, *, _depth=0):
    if _depth >= MAX_RECOVERIES:
        raise ValueError('Planning recovery ancestry перевищує чинний cap')
    if not re.fullmatch('[a-f0-9]{64}', checkpoint_sha256):
        raise ValueError('Некоректний checkpoint SHA256')
    p = db.project(pid)
    if p['approved_hash'] or p['steps'] or p['plan']:
        raise ValueError('Recovery дозволене лише для незбереженої непогодженої чернетки без кроків')
    job = db.one('SELECT * FROM jobs WHERE id=? AND project_id=?', (source_job_id,pid))
    if job['kind'] not in ('analyse','recover_plan') or job['status'] != 'failed' or not job['error']:
        raise ValueError('Потрібен failed planning job із підтвердженою причиною')
    if job['error'].startswith(('Хмарна відповідь незавершена', 'Попередній хмарний запит має непідтверджений')):
        raise ValueError('Unknown/truncated planning failure не є підставою checkpoint recovery')
    applied = db.query("SELECT data FROM events WHERE project_id=? AND kind='plan_patch_applied' AND created>=? AND created<=? ORDER BY id DESC LIMIT 1",(pid,job['created'],job['updated']))
    if not applied:
        return _terminal_candidate(pid,job,checkpoint_sha256,_depth)
    if Path(json.loads(applied[0]['data'])['path'].replace('\\','/')).stem != checkpoint_sha256:
        raise ValueError('Потрібен останній checkpoint цього failed job')
    path = config.project_dir(pid) / 'planning-patches' / (checkpoint_sha256 + '.json')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1000000:
        raise ValueError('Checkpoint відсутній або неприпустимий')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != checkpoint_sha256:
        raise ValueError('Checkpoint bytes/hash не збігаються')
    record = json.loads(raw)
    if record.get('version') != 1 or record.get('state') != 'schema_valid_only;unapproved;whole_plan_checks_follow':
        raise ValueError('Непідтримуваний checkpoint state')
    row = db.one('SELECT * FROM cloud_requests WHERE id=? AND project_id=?', (record['request_id'],pid))
    snapshot = json.loads(row['config'])
    if (row['state'] != 'done' or snapshot.get('project_revision') != p['revision']
            or row['created'] > job['updated']):
        raise ValueError('Stale/unknown/foreign planning receipt')
    _, _, uses = _complete_outcome(pid,job)
    if not uses or not any(use.get('request_id') == row['id'] and use.get('complete') is True for use in uses):
        raise ValueError('Receipt не використано цим failed job')
    for key in ('author','group','institution'):
        if snapshot.get(key) != config.settings()[key]:
            raise ValueError('Глобальні реквізити змінилися після checkpoint')
    receipts = db.query("SELECT result FROM jobs WHERE project_id=? AND kind='cloud' AND status='done' AND json_extract(payload,'$.id')=?", (pid,row['id']))
    if len(receipts) != 1:
        raise ValueError('Немає однозначної завершеної квитанції')
    receipt = json.loads(receipts[0]['result'])
    if receipt.get('complete') is not True:
        raise ValueError('Truncated receipt не можна відновлювати як complete patch')
    from .assistance import structured_response
    patch = structured_response(receipt['text'].strip(), PlanPatch)
    base = PlanDraft.model_validate(record['base'])
    result = apply_plan_patch(base, patch)
    if (canonical(patch.model_dump()) != canonical(record['patch']) or draft_hash(base) != record['base_sha256']
            or draft_hash(result) != record['result_sha256'] or canonical(result.model_dump()) != canonical(record['result'])):
        raise ValueError('Checkpoint provenance/result mismatch')
    state = source_state(pid)
    matched = False
    events = db.query("SELECT data FROM events WHERE project_id=? AND kind='planning_source_coverage' AND created>=? AND created<=?", (pid,job['created'],job['updated']))
    for event in events:
        saved = config.DATA / json.loads(event['data'])['path']
        if not saved.resolve().is_relative_to((config.project_dir(pid)/'planning-coverage').resolve()) or saved.is_symlink():
            continue
        raw_coverage = saved.read_bytes()
        # Historical pretty-printed receipts were written with Windows CRLF,
        # while their address was computed from the original LF text.
        if (hashlib.sha256(raw_coverage).hexdigest() != saved.stem
                and hashlib.sha256(saved.read_text(encoding='utf-8').encode()).hexdigest() != saved.stem):
            continue
        coverage = json.loads(raw_coverage)
        length = coverage.get('prompt_characters',0)
        if not length or hashlib.sha256(row['prompt'][:length].encode()).hexdigest() != coverage.get('prompt_sha256'):
            continue
        old_docs = [{k:d[k] for k in ('document_id','name','role','status','index_sha256','workspace_path','warnings')} for d in coverage['documents']]
        matched = canonical(old_docs) == canonical(state['documents'])
        if matched: break
    if not matched:
        raise ValueError('Source coverage checkpoint застарілий або не підтверджений')
    return result, job['error'], state


def enqueue(pid, source_job_id, checkpoint_sha256):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        existing = c.execute('SELECT job_id FROM planning_recoveries WHERE project_id=? AND source_job_id=? AND checkpoint_sha256=?', (pid,source_job_id,checkpoint_sha256)).fetchone()
        if existing: return {'job_id':existing['job_id'],'existing':True}
        p = db.project(pid)
        if p['status'] not in ('error','paused') or db.query("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')",(pid,)):
            raise ValueError('Проєкт не готовий до явного planning recovery')
        latest = db.one("SELECT id FROM jobs WHERE project_id=? AND kind IN ('analyse','recover_plan') ORDER BY created DESC LIMIT 1",(pid,))
        if latest['id'] != source_job_id:
            raise ValueError('Потрібен останній failed planning job')
        _, _, state = candidate(pid, source_job_id, checkpoint_sha256)
        count = c.execute('SELECT count(*) FROM planning_recoveries WHERE project_id=? AND revision=?',(pid,p['revision'])).fetchone()[0]
        if count >= MAX_RECOVERIES:
            raise ValueError('Ліміт2 explicit planning recoveries цієї revision вичерпано')
        rid,jid=db.uid(),db.uid();now=db.now()
        c.execute('INSERT INTO planning_recoveries VALUES(?,?,?,?,?,?,?,?)',(rid,pid,p['revision'],source_job_id,checkpoint_sha256,digest(state),jid,now))
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(jid,pid,'recover_plan','queued',json.dumps({'recovery_id':rid}),None,None,now,now))
        c.execute("UPDATE projects SET status='analysis_running',updated=? WHERE id=?",(now,pid))
    return {'job_id':jid,'existing':False}


def execute(pid, recovery_id):
    intent=db.one('SELECT * FROM planning_recoveries WHERE id=? AND project_id=?',(recovery_id,pid))
    def guard():
        p=db.project(pid);job=db.one('SELECT status FROM jobs WHERE id=?',(intent['job_id'],))
        if (p['status']!='analysis_running' or p['approved_hash'] or p['steps'] or p['plan']
                or p['revision']!=intent['revision'] or job['status']!='running'
                or digest(source_state(pid))!=intent['context_sha256']):
            raise RuntimeError('Planning recovery скасовано або context/revision/source/permission змінилися')
    guard()
    draft,error,_=candidate(pid,intent['source_job_id'],intent['checkpoint_sha256'])
    from . import engine
    return engine.analyse(pid,recovery={'draft':draft,'error':error,'guard':guard})
