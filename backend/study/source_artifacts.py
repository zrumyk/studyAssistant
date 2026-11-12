"""Re-register source snapshots from an accepted run, without executing code."""
import json
import re
from . import config, db
from .schemas import ActionCheckpoint
from .execution_protocol import stream_identity


def reconcile(pid, sid):
    from .engine import artifact, plan_hash
    project=db.project(pid)
    if not project['approved_hash'] or project['approved_hash']!=plan_hash(project['plan']):
        raise ValueError('Погодження актуального плану відсутнє')
    if db.query("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')",(pid,)):
        raise ValueError('Спочатку дочекайтеся завершення активного job')
    step=next((s for s in project['steps'] if s['id']==sid),None)
    if not step or step['spec']['kind']!='code' or step['status']!='done':
        raise ValueError('Потрібний прийнятий code-крок')
    result=step['result'];runid=result.get('run_id','')
    if not re.fullmatch('[a-f0-9]{32}',runid) or result.get('exit_code')!=0 or result.get('oom') or result.get('reason'):
        raise ValueError('Успішний run не підтверджено')
    if result.get('assessment',{}).get('satisfied') is not True:
        raise ValueError('Оцінка кроку не підтверджена')
    base=config.project_dir(pid);snapshot=config.safe_path(base,'runs/'+runid)
    saved=ActionCheckpoint.model_validate_json((base/'codegen'/sid/'last-action.json').read_text('utf-8'))
    if saved.revision!=project['revision'] or not saved.written_to_workspace:
        raise ValueError('Source checkpoint не належить актуальній revision')
    from .prepared import validate
    validate(saved.action, step['spec'], base/'workspace', contract_version=project['plan'].get('artifact_contract_version',0))
    actual=json.loads((snapshot/'result.json').read_text('utf-8'))
    for key in ('run_id','exit_code','oom','reason','checks','checker_results','execution_protocol','artifact_visibility'):
        if actual.get(key)!=result.get(key):raise ValueError('Run receipt mismatch: '+key)
    checks=result.get('checker_results',[])
    if not checks or any(c.get('completed') is not True or c.get('exit_code')!=0 for c in checks):
        raise ValueError('Checker не підтверджений')
    if sorted(result.get('checks',[]))!=sorted(f.path for f in saved.action.checks):
        raise ValueError('Checker source mismatch')
    files=saved.action.files+saved.action.checks
    for f in files:
        if config.safe_path(snapshot,f.path).read_text('utf-8')!=f.content:
            raise ValueError('Source bytes mismatch: '+f.path)
    receipt=result.get('artifact_contract',{})
    if receipt.get('version')!=1 or not receipt.get('outputs'):
        raise ValueError('Потрібний artifact receipt v1')
    for name,identity in receipt['outputs'].items():
        if stream_identity(config.safe_path(snapshot,name))!=identity or stream_identity(config.safe_path(base/'workspace',name))!=identity:
            raise ValueError('Published output mismatch: '+name)
    restored=[]
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('SELECT revision,approved_hash FROM projects WHERE id=?',(pid,)).fetchone()
        status=c.execute('SELECT status,result FROM steps WHERE id=? AND project_id=?',(sid,pid)).fetchone()
        if current['revision']!=project['revision'] or current['approved_hash']!=project['approved_hash'] or status['status']!='done' or json.loads(status['result'])!=result:
            raise ValueError('Стан змінився перед звірянням')
        for f in files:
            proof={'type':'accepted_run_source','run_id':runid,'workspace_path':f.path,
                   'sha256':stream_identity(config.safe_path(snapshot,f.path))['sha256'],
                   'source_role':'checker' if f in saved.action.checks else 'driver' if f.path==saved.action.entrypoint else 'source'}
            rows=c.execute("SELECT path,provenance FROM artifacts WHERE project_id=? AND step_id=? AND state='executed' AND revision=? AND json_extract(provenance,'$.workspace_path')=?",(pid,sid,project['revision'],f.path)).fetchall()
            if any(json.loads(row['provenance']).get('run_id')==runid and config.safe_path(base,row['path']).read_bytes()==config.safe_path(snapshot,f.path).read_bytes() for row in rows):
                continue
            artifact(pid,sid,f.path,'executed',proof,root=snapshot,connection=c)
            restored.append(f.path)
        c.execute('INSERT INTO events(project_id,kind,data,created) VALUES(?,?,?,?)',(pid,'source_artifacts_reconciled',json.dumps({'step_id':sid,'run_id':runid,'restored':restored,'computation_replayed':False}),db.now()))
    return {'step_id':sid,'run_id':runid,'restored':restored,'computation_replayed':False}
