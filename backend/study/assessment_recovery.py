"""Explicit, bounded new appraisal after a proven failed service call. Never rerun code."""
from datetime import datetime, timezone
import hashlib
import json
import re

from . import config, db

MAX_RECOVERIES = 2
COOLDOWN_SECONDS = 60
SERVICE_ERROR = re.compile(r'Хмарний сервіс повернув HTTP (5\d\d); автоматичного повтору не буде')


def service_receipt(pid, rid, error):
    if not SERVICE_ERROR.fullmatch(error or '') or not rid:
        return None
    row = db.query("SELECT r.id,r.created FROM cloud_requests r JOIN jobs j "
                   "ON j.kind='cloud' AND json_extract(j.payload,'$.id')=r.id "
                   "WHERE r.id=? AND r.project_id=? AND r.state='uncertain' "
                   "AND j.status='failed' AND j.error=?", (rid, pid, error))
    return row[0] if len(row) == 1 else None


def failed_service(project, step):
    result = step['result']
    history = result.get('assessment_recoveries', [])
    if history:
        last = history[-1]
        if last['state'] != 'transport_failed':
            return None
        return service_receipt(project['id'], last.get('request_id'), last.get('error'))
    # Legacy results have no request ID: require the same step's completed
    # reassess job window plus a real failed cloud job, never just error text.
    error = result.get('reassessment_error', '')
    if result.get('review_request_run_id') == result.get('run_id'):
        return service_receipt(project['id'], result.get('review_request_id'),
                               error or result.get('assessment_error', ''))
    if not SERVICE_ERROR.fullmatch(error):
        return None
    matches = db.query(
        "SELECT DISTINCT r.id,r.created FROM jobs parent JOIN cloud_requests r "
        "ON r.project_id=parent.project_id AND r.created>=parent.created AND r.created<=parent.updated "
        "JOIN jobs child ON child.kind='cloud' AND json_extract(child.payload,'$.id')=r.id "
        "JOIN events e ON e.project_id=parent.project_id AND e.kind='result_reassessed' "
        "AND e.created>=parent.created AND e.created<=parent.updated "
        "WHERE parent.project_id=? AND parent.kind='reassess' AND parent.status='done' "
        "AND json_extract(parent.payload,'$.step_id')=? AND json_extract(parent.result,'$.accepted')=0 "
        "AND r.state='uncertain' AND r.purpose LIKE '%/Повторна перевірка збереженого результату' "
        "AND json_extract(e.data,'$.step_id')=? AND json_extract(e.data,'$.run_id')=? "
        "AND child.status='failed' AND child.error=?",
        (project['id'], step['id'], step['id'], result.get('run_id'), error))
    return matches[0] if len(matches) == 1 else None


def availability(project, step):
    from .engine import plan_hash
    result = step['result']
    history = result.get('assessment_recoveries', [])
    reason = ''
    receipt = None
    if project['status'] == 'paused':
        reason = 'Проєкт призупинено'
    elif not project['approved_hash'] or project['approved_hash'] != plan_hash(project['plan']):
        reason = 'Погодьте актуальний план'
    elif step['status'] != 'waiting_user' or step['spec']['kind'] != 'code' or result.get('exit_code') != 0 or result.get('oom') or result.get('reason'):
        reason = 'Немає збереженого успішного запуску, що очікує оцінки'
    elif result.get('review_only_attempts', 0) != 1:
        reason = 'Спершу використайте штатну повторну перевірку'
    elif len(history) >= MAX_RECOVERIES:
        reason = 'Ліміт відновлення оцінки цього запуску вичерпано'
    elif any(entry['state'] in ('requested', 'completed', 'interrupted') for entry in history):
        reason = 'Оцінка вже виконана або її результат не підтверджений; нового запиту немає'
    else:
        receipt = failed_service(project, step)
        if not receipt:
            reason = 'Немає підтвердженої відмови хмарного сервісу; ліміт не обходиться'
        elif (datetime.now(timezone.utc)-datetime.fromisoformat(receipt['created'])).total_seconds() < COOLDOWN_SECONDS:
            reason = 'Зачекайте щонайменше хвилину після відмови сервісу'
    return {'available': not reason, 'reason': reason, 'remaining': max(0, MAX_RECOVERIES-len(history)),
            'previous_request_id': receipt['id'] if receipt else None}


def validate_snapshot(project, step):
    from .artifact_contracts import capture_outputs, require_inputs
    runid = step['result'].get('run_id', '')
    if not re.fullmatch('[a-f0-9]{32}', runid):
        raise ValueError('Немає справжнього знімка запуску')
    folder = config.safe_path(config.project_dir(project['id']), 'runs/'+runid)
    raw = (folder/'result.json').read_bytes()
    snapshot = json.loads(raw)
    if snapshot.get('exit_code') != 0 or snapshot.get('oom') or snapshot.get('reason') or not snapshot.get('checks'):
        raise ValueError('Знімок не підтверджує успішне виконання та перевірки')
    if project['plan'].get('artifact_contract_version', 0) == 1:
        require_inputs(project, step)
        capture_outputs(config.project_dir(project['id'])/'workspace', step['spec'],
                        step['result'].get('produced_files', []), snapshot=folder)
    # Pin the saved receipt/check source bytes across queue/worker boundaries.
    hashes = {'result.json': hashlib.sha256(raw).hexdigest()}
    for name in snapshot['checks']:
        hashes[name] = hashlib.sha256(config.safe_path(folder, name).read_bytes()).hexdigest()
    return hashes


def enqueue(pid, sid):
    from .assistance import BudgetedModel
    from .providers import OllamaProvider
    project = db.project(pid)
    step = next((s for s in project['steps'] if s['id'] == sid), None)
    if step is None:
        raise ValueError('Крок не знайдено')
    eligible = availability(project, step)
    if not eligible['available']:
        raise ValueError(eligible['reason'])
    if not BudgetedModel(pid, OllamaProvider(), 'Відновлення оцінки').available():
        raise ValueError('Хмарна оцінка недоступна за чинним дозволом; нового запиту немає')
    hashes = validate_snapshot(project, step)
    recovery_id, jid = db.uid(), db.uid()
    result = step['result']
    result.setdefault('assessment_recoveries', []).append({
        'id': recovery_id, 'state': 'requested', 'created': db.now(), 'run_id': result['run_id'],
        'previous_request_id': eligible['previous_request_id'], 'snapshot_hashes': hashes,
        'approved_hash': project['approved_hash']})
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT status,approved_hash FROM projects WHERE id=?', (pid,)).fetchone()
        if current['status'] == 'paused' or current['approved_hash'] != project['approved_hash']:
            raise ValueError('Проєкт призупинено або погодження змінилося')
        if c.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)).fetchone():
            raise ValueError('Зачекайте завершення поточної дії')
        # Also reject stale concurrent callers even before a worker claims.
        previous = c.execute('SELECT result FROM steps WHERE id=?', (sid,)).fetchone()['result']
        if json.loads(previous).get('assessment_recoveries', []) != step['result']['assessment_recoveries'][:-1]:
            raise ValueError('Стан оцінки вже змінився')
        c.execute('UPDATE steps SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), sid))
        c.execute("UPDATE projects SET status='running' WHERE id=?", (pid,))
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                  (jid, pid, 'recover_assessment', 'queued', json.dumps({'step_id': sid, 'recovery_id': recovery_id}), None, None, db.now(), db.now()))
    return {'job_id': jid}


def execute(pid, sid, recovery_id):
    from . import engine
    from .run_review import review_successful_run
    project = db.project(pid)
    step = next(s for s in project['steps'] if s['id'] == sid)
    result = step['result']
    entry = next(e for e in result['assessment_recoveries'] if e['id'] == recovery_id)
    if entry['state'] != 'requested':
        raise ValueError('Це відновлення вже оброблене; нового запиту немає')
    accepted = False
    try:
        if engine.cancelled(pid) or project['approved_hash'] != entry['approved_hash'] or engine.plan_hash(project['plan']) != entry['approved_hash']:
            raise ValueError('Погодження змінилося або проєкт призупинено')
        if validate_snapshot(project, step) != entry['snapshot_hashes']:
            raise ValueError('Збережений знімок змінився після постановки в чергу')
        def receipt(rid):
            entry['request_id'] = rid
        check = review_successful_run(pid, step['spec'], result, result.get('assessment_error', ''),
                                      lambda: engine.cancelled(pid), recovery_id=recovery_id, receipt_callback=receipt)
        entry.update(state='completed', assessment=check.model_dump(), finished=db.now())
        result['assessment_before_recovery'] = result.get('assessment')
        result['assessment'] = check.model_dump()
        result.pop('assessment_pending', None)
        accepted = check.satisfied and not check.needs_user
    except Exception as error:
        entry.update(state='transport_failed' if service_receipt(pid, entry.get('request_id'), str(error)) else 'error',
                     error=str(error)[:1500], finished=db.now())
    if accepted:
        result.pop('instructions', None)
    else:
        result['instructions'] = 'Оцінку збереженого запуску не прийнято. Код не виконувався повторно. '+str(entry.get('error') or result.get('assessment', {}).get('explanation', ''))
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = c.execute('SELECT status,approved_hash FROM projects WHERE id=?', (pid,)).fetchone()
        if current['approved_hash'] != entry['approved_hash']:
            accepted = False
            entry['approval_changed'] = True
            result['instructions'] = 'Погодження змінилося; нова оцінка не приймає інший план'
        may_continue = current['status'] == 'running' and current['approved_hash'] == entry['approved_hash']
        c.execute('UPDATE steps SET status=?,result=? WHERE id=?',
                  ('done' if accepted else 'waiting_user', json.dumps(result, ensure_ascii=False), sid))
        if may_continue:
            c.execute('UPDATE projects SET status=? WHERE id=?', ('running' if accepted else 'needs_user', pid))
            if accepted:
                c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                          (db.uid(), pid, 'execute', 'queued', '{}', None, None, db.now(), db.now()))
    db.event(pid, 'assessment_recovered', {'step_id': sid, 'run_id': result['run_id'],
                                         'recovery_id': recovery_id, 'accepted': accepted, 'program_executed_again': False})
    return {'accepted': accepted, 'program_executed_again': False}
