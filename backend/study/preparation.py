"""Prepare an approved step without writing workspace or starting runtime."""
import hashlib
import json
from pydantic import Field
from . import config, db
from .schemas import StrictModel, ActionCheckpoint


class CandidateFailure(StrictModel):
    candidate_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    log: str = Field(min_length=1,max_length=12000)


def checked_failure(pid,sid,failure):
    if failure is None:return None
    evidence=CandidateFailure.model_validate(failure)
    checkpoint=config.project_dir(pid)/'codegen'/sid/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'))
    if saved.revision!=db.project(pid)['revision']:
        raise ValueError('Кандидат застарів після зміни плану')
    if hashlib.sha256(saved.action.model_dump_json().encode()).hexdigest()!=evidence.candidate_sha256:
        raise ValueError('Діагностика належить іншій версії кандидата')
    return json.dumps({'candidate_failure':True,**evidence.model_dump(),'source':'authenticated_operator_diagnostic'},ensure_ascii=False)


def eligible(pid,sid):
    from .engine import plan_hash
    project=db.project(pid)
    if not project['approved_hash'] or project['approved_hash']!=plan_hash(project['plan']):
        raise ValueError('Спочатку погодьте актуальний план')
    step=next((s for s in project['steps'] if s['id']==sid),None)
    if not step or step['spec']['kind']!='code' or step['status'] not in ('pending','failed','blocked'):
        raise ValueError('Підготовка доступна лише для незавершеного автоматичного кроку code')
    if any(s['status']!='done' for s in project['steps'] if s['position']<step['position']):
        raise ValueError('Спочатку завершіть попередні кроки')
    if step['result'].get('exit_code')==0:
        raise ValueError('Збережений успішний запуск потребує рецензії, не нового коду')
    return project,step


def prepare(pid,sid,revision,approved_hash,failure=None):
    from .engine import prepare_action
    from .prepared import validate
    from .preflight import check_expensive_action
    project,step=eligible(pid,sid)
    from .artifact_contracts import require_inputs
    require_inputs(project, step)
    if project['revision']!=revision or project['approved_hash']!=approved_hash:
        raise ValueError('План змінився після постановки підготовки в чергу')
    event_start=db.one('SELECT COALESCE(MAX(id),0) n FROM events')['n']
    def stopped():
        current=db.project(pid)
        return (current['revision']!=revision or current['approved_hash']!=approved_hash
                or bool(db.query("SELECT id FROM events WHERE project_id=? AND kind='paused' AND id>?",(pid,event_start))))
    folder=config.project_dir(pid)/'codegen'/sid
    checkpoint=folder/'last-action.json'
    if checkpoint.is_file():
        raw=checkpoint.read_bytes()
        history=folder/'history';history.mkdir(exist_ok=True)
        copy=history/(hashlib.sha256(raw).hexdigest()+'.json')
        if not copy.exists():copy.write_bytes(raw)
    previous=checked_failure(pid,sid,failure) if failure is not None else step['error']
    action=prepare_action(pid,step,previous,cancelled_check=stopped)
    if stopped():return {'status':'cancelled','executed':False}
    validate(action,step['spec'],config.project_dir(pid)/'workspace',contract_version=project['plan'].get('artifact_contract_version',0))
    check_expensive_action(pid,sid,step['spec'],action,stopped)
    if stopped():return {'status':'cancelled','executed':False}
    db.event(pid,'step_prepared',{'step_id':sid,'revision':revision,'executed':False})
    return {'status':'ready','step_id':sid,'revision':revision,'executed':False,
            'files':[f.path for f in action.files+action.checks]}
