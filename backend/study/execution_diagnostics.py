"""Keep the actual failure visible instead of a large output-file inventory."""
import json
import re
import hashlib


def failure_signature(previous):
    """Identify a repeated runtime failure without its delivery/run identity.

    Keep error text, source locations, actual values and resource limits.
    Unknown/plain diagnostics are preserved rather than heuristically stripping
    numbers, hashes or source lines that could distinguish a different defect.
    """
    try:payload=json.loads(previous)
    except (ValueError,TypeError):payload=previous
    if isinstance(payload,dict):
        run=payload.get('run_id')
        if isinstance(run,str) and re.fullmatch(r'[a-f0-9]{32}',run) and 'exit_code' in payload:
            def normalize(value):
                if isinstance(value,str):return value.replace(run,'<run>')
                if isinstance(value,list):return [normalize(v) for v in value]
                if isinstance(value,dict):return {k:normalize(v) for k,v in value.items()}
                return value
            payload=normalize({k:v for k,v in payload.items() if k not in ('run_id','files','output_file_count')})
    canonical=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


def recover_runtime_failure(pid,sid,revision,workspace,previous):
    """Recover a lost traceback only from a run with this exact action source.

    A later transport/generation error must not erase the diagnostic needed to
    repair the code. This is historical context, never execution acceptance.
    """
    from . import config
    from .schemas import ActionCheckpoint
    if not previous or '"run_id"' in previous:
        return previous
    checkpoint=config.project_dir(pid)/'codegen'/sid/'last-action.json'
    try:
        saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'))
        if saved.revision!=revision or not saved.written_to_workspace:
            return previous
        expected={file.path:config.safe_path(workspace,file.path).read_bytes() for file in saved.action.files+saved.action.checks}
        if not expected or sum(map(len,expected.values()))>500000:
            return previous
        runs=config.project_dir(pid)/'runs'
        for folder in sorted((p for p in runs.iterdir() if p.is_dir()),key=lambda p:p.stat().st_mtime,reverse=True)[:40]:
            result_path=folder/'result.json'
            if not result_path.is_file():continue
            if not all(config.safe_path(folder,path).is_file() and config.safe_path(folder,path).read_bytes()==content for path,content in expected.items()):
                continue
            result=json.loads(result_path.read_text('utf-8'))
            if result.get('exit_code')==0 and not result.get('reason') and not result.get('oom'):
                return previous
            recovered=json.loads(runtime_failure_message(result))
            recovered['repair_error']=previous[:1000]
            return json.dumps(recovered,ensure_ascii=False)
    except (OSError,ValueError):
        pass
    return previous


def recover_successful_run(pid,sid,revision,workspace,previous):
    """Reconcile a successful run rejected by an output-contract check.

    Reuse only unchanged source AND output bytes. All sandbox checks must have
    passed; a real runtime/check failure is never converted into success.
    """
    if not previous.startswith('Цей запуск не створив очікуваний результат:'):
        return None
    import hashlib
    from . import config
    from .schemas import ActionCheckpoint
    from .output_validation import validate_expected_outputs
    def digest(file):
        with file.open('rb') as stream:return hashlib.file_digest(stream,'sha256').digest()
    try:
        saved=ActionCheckpoint.model_validate_json((config.project_dir(pid)/'codegen'/sid/'last-action.json').read_text('utf-8'))
        if saved.revision!=revision or not saved.written_to_workspace:return None
        sources=saved.action.files+saved.action.checks
        if not sources:return None
        runs=config.project_dir(pid)/'runs'
        for folder in sorted((p for p in runs.iterdir() if p.is_dir()),key=lambda p:p.stat().st_mtime,reverse=True)[:40]:
            if not (folder/'result.json').is_file():continue
            result=json.loads((folder/'result.json').read_text('utf-8'))
            if result.get('exit_code')!=0 or result.get('oom') or result.get('reason') or not result.get('checks'):continue
            from .execution_protocol import stdin_identity
            protocol=result.get('execution_protocol',{})
            expected_input=stdin_identity(saved.action.stdin_text)
            if protocol.get('stdin') != expected_input:
                # Historical results predate finite input; reuse only with no input.
                if saved.action.stdin_text or protocol:continue
            if protocol:
                input_artifact=protocol.get('stdin_artifact')
                if input_artifact!=folder.name+'.stdin':continue
                input_path=folder.parent/input_artifact
                if input_path.is_symlink() or not input_path.is_file() or digest(input_path).hex()!=expected_input['sha256']:continue
                from .execution_protocol import stream_identity
                if protocol.get('version')!=2:continue
                streams=protocol.get('streams')
                if (not isinstance(streams,dict) or set(streams)!= {'stdout','stderr'}
                        or any(streams[name] is None or stream_identity(folder/('.study_main.'+name))!=streams[name]
                               for name in ('stdout','stderr'))):continue
            if sorted(result['checks'])!=sorted(file.path for file in saved.action.checks):continue
            if not all(config.safe_path(folder,file.path).is_file() and config.safe_path(workspace,file.path).is_file() and config.safe_path(folder,file.path).read_text('utf-8')==file.content==config.safe_path(workspace,file.path).read_text('utf-8') for file in sources):continue
            if not all(config.safe_path(workspace,path).is_file() and config.safe_path(folder,path).is_file() and digest(config.safe_path(workspace,path))==digest(config.safe_path(folder,path)) for path in result.get('files',[])):continue
            validate_expected_outputs(workspace,result,saved.action.expected_files)
            result['recovered_successful_run']=True
            return saved.action,result
    except (OSError,ValueError):
        pass
    return None


def runtime_failure_message(result):
    data={key:result.get(key) for key in ('run_id','exit_code','oom','reason')}
    data['reason']=str(data.get('reason') or '')[:300]
    if isinstance(result.get('resource_limits'),dict):
        data['resource_limits']={key:result['resource_limits'][key] for key in ('memory_mb','cpus','timeout_seconds') if key in result['resource_limits']}
    data['log']=str(result.get('log') or '')[-5000:]
    data['output_file_count']=len(result.get('files',[]))
    data['diagnostic_files']=[path for path in result.get('files',[]) if re.search(r'check|valid|diagnostic|error',path,re.I) and path.lower().endswith(('.json','.csv','.txt','.log'))][:8]
    text=json.dumps(data,ensure_ascii=False)
    while len(text)>6500 and len(data['log'])>1:
        data['log']=data['log'][-max(1,len(data['log'])//2):]
        text=json.dumps(data,ensure_ascii=False)
    return text


def failure_context(previous,limit=5000):
    if not previous:
        return ''
    try:
        payload=json.loads(previous)
    except ValueError:
        payload=None
        # Older stored failures may have been cut inside the file inventory.
        # Recover only the intact JSON string value of log, never invent text.
        if previous.lstrip().startswith('{') and '"run_id"' in previous[:200]:
            match=re.search(r'"log"\s*:',previous)
            if match:
                try:
                    log,_=json.JSONDecoder().raw_decode(previous[match.end():].lstrip())
                    if isinstance(log,str):payload={'log':log}
                except ValueError:
                    pass
    if isinstance(payload,dict) and isinstance(payload.get('log'),str):
        summary={key:payload[key] for key in ('run_id','exit_code','oom','reason','resource_limits','repair_error','diagnostic_files') if key in payload}
        prefix='Діагностика запуску: '+json.dumps(summary,ensure_ascii=False)+'\nФактичний журнал помилки:\n'
        return prefix+payload['log'][-max(1,limit-len(prefix)):]
    if len(previous)<=limit:
        return previous
    return previous[:limit//3]+'\n[середину діагностики пропущено]\n'+previous[-limit*2//3:]
