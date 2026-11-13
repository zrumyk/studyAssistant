"""Explicit persistent file dependencies. No inference from subject names or prose."""
from pathlib import PurePosixPath
import uuid

from . import config
from .execution_protocol import stream_identity


class ArtifactOwnershipError(ValueError):
    """A local ownership conflict, eligible for graph-scoped model repair."""


def covers(parent, child):
    return child == parent or child.startswith(parent + '/')


def overlaps(a, b):
    return covers(a.casefold(), b.casefold()) or covers(b.casefold(), a.casefold())


def artifact_path(name):
    if not isinstance(name, str) or not name or len(name) > 500:
        raise ValueError('Artifact path must be a nonempty relative path, at most500 characters')
    path = PurePosixPath(name)
    if name.casefold() == 'result.json':
        raise ValueError('Root result.json is runtime metadata; use a declared data subdirectory or another filename')
    if (not path.parts or path.is_absolute() or '\\' in name or ':' in name or path.as_posix() != name
            or any(ord(char) < 32 for char in name)
            or any(part in ('.', '..') or part.startswith('.') for part in path.parts)):
        raise ValueError('Артефакт потребує канонічний відносний шлях; /tmp, host paths і private capture не передаються: ' + name)
    return name


def validate_contract(plan, pid=None, *, require=False):
    version = plan.artifact_contract_version
    if version == 0:
        if require:
            raise ValueError('Новий згенерований план потребує artifact_contract_version=1 та явні artifact_inputs/artifact_outputs')
        if any(step.artifact_inputs or step.artifact_outputs for step in plan.steps):
            raise ValueError('Artifact declarations require artifact_contract_version=1')
        return
    owners = []
    workspace = config.project_dir(pid) / 'workspace' if pid else None
    for index, step in enumerate(plan.steps):
        inputs = [artifact_path(name) for name in step.artifact_inputs]
        outputs = [artifact_path(name) for name in step.artifact_outputs]
        if step.kind == 'code':
            from .runtime import RUNTIMES
            entrypoint = RUNTIMES[step.language][1]
            if any(overlaps(name, entrypoint) for name in inputs):
                raise ValueError(f'Крок {index+1}: службовий entrypoint {entrypoint} належить поточному кроку й не може бути immutable artifact_input. Передай модуль за окремим шляхом і створи власний driver.')
        if len({p.casefold() for p in inputs}) != len(inputs) or len({p.casefold() for p in outputs}) != len(outputs):
            raise ValueError('Повторні шляхи артефактів у кроці ' + str(index+1))
        if outputs and step.kind not in ('code', 'write', 'fetch'):
            raise ValueError('Явні file outputs підтримані для code/write/fetch; report/manual використовують штатні evidence mechanisms')
        if step.kind == 'fetch' and (len(outputs) != 1 or not outputs[0].startswith('inputs/downloads/')):
            raise ValueError('fetch публікує рівно один файл під inputs/downloads/, не весь inputs namespace')
        for name in inputs:
            if any(covers(path, name) for path, _ in owners):
                continue
            if name.split('/')[0] == 'inputs':
                if workspace is not None:
                    path = config.safe_path(workspace, name)
                    if not path.exists():
                        raise ValueError('Імпортований input відсутній: ' + name)
                continue
            raise ValueError(f'Крок {index+1}: input не має попереднього producer або inputs/ source: {name}')
        for name in outputs:
            if name.split('/')[0].casefold() == 'inputs' and step.kind != 'fetch':
                raise ValueError('inputs/ доступні лише для читання; тільки fetch може додати погоджене завантаження')
            if any(overlaps(name, path) for path, _ in owners) or any(overlaps(name, other) for other in inputs):
                raise ArtifactOwnershipError('Артефакт має одного producer й не перезаписує input: ' + name)
            if any(name != other and overlaps(name, other) for other in outputs):
                raise ArtifactOwnershipError('Перекриття output paths: ' + name)
            owners.append((name, index))


def drop_undeclared_outputs(spec, action):
    """Unpublish extra files a code step plans to create beyond its declared outputs.

    Later steps receive only declared outputs, so an extra scratch file (for example a
    database built to verify a schema) cannot leak into the plan. Rejecting it used to
    fail the whole step after three repairs (13.11 suite, S3). Paths that touch declared
    inputs or the read-only inputs/ namespace are still rejected by check_action.
    Mutates action.expected_files and returns the dropped paths.
    """
    if spec['kind'] != 'code':
        return []
    outputs = spec.get('artifact_outputs', [])
    inputs = spec.get('artifact_inputs', [])
    dropped = [path for path in action.expected_files
               if not any(covers(name, path) for name in outputs)
               and path.split('/')[0].casefold() != 'inputs'
               and not any(overlaps(path, name) for name in inputs)]
    if dropped:
        action.expected_files = [path for path in action.expected_files if path not in dropped]
    return dropped


def note_unpublished(pid, spec, dropped):
    """Record unpublished undeclared outputs; called wherever check_action actually drops them."""
    if dropped:
        from . import db
        db.event(pid, 'undeclared_outputs_unpublished',
                 {'step': spec.get('title'), 'paths': dropped,
                  'note': 'Файли, не оголошені в плані, не публікуються й не передаються наступним крокам'})


def check_action(spec, action):
    """Validate an action against its step contract. Returns undeclared code outputs that were unpublished."""
    outputs = spec.get('artifact_outputs', [])
    inputs = spec.get('artifact_inputs', [])
    dropped = drop_undeclared_outputs(spec, action)
    if spec['kind'] == 'fetch' and action.expected_files != outputs:
        raise ValueError('fetch expected_files має точно збігатися з єдиним artifact_output')
    for file in action.files + action.checks:
        artifact_path(file.path)
        if any(covers(name.casefold(), file.path.casefold()) for name in inputs):
            raise ValueError('Action не може перезаписати declared input: ' + file.path)
        if spec['kind'] == 'write' and not any(covers(name, file.path) for name in outputs):
            raise ValueError('Write action не оголошений у artifact_outputs: ' + file.path)
    for path in action.expected_files:
        artifact_path(path)
        if not any(covers(name, path) for name in outputs):
            raise ValueError('expected_files не оголошений у artifact_outputs: ' + path)
    if spec['kind'] == 'code':
        sources = {f.path for f in action.files + action.checks}
        for path in outputs:
            if path not in sources and not any(covers(path, name) or covers(name, path) for name in action.expected_files):
                raise ValueError('Data output має бути у expected_files, source deliverable — у files: ' + path)
    return dropped


def capture_outputs(workspace, spec, produced, *, snapshot=None):
    manifest = {}
    produced = set(produced)
    for name in spec.get('artifact_outputs', []):
        path = config.safe_path(workspace, name)
        candidates = [path] if path.is_file() else sorted(path.rglob('*')) if path.is_dir() else []
        count = 0
        for file in candidates:
            if not file.is_file() or file.is_symlink():
                continue
            relative = file.relative_to(workspace).as_posix()
            if relative not in produced:
                raise ValueError('Цей крок не створив declared output: ' + relative)
            identity = stream_identity(config.safe_path(workspace, relative))
            if identity is None:
                raise ValueError('Немає regular artifact: ' + relative)
            if snapshot is not None and stream_identity(config.safe_path(snapshot, relative)) != identity:
                raise ValueError('Output не збігається з фактичним run snapshot: ' + relative)
            manifest[relative] = identity
            count += 1
            if len(manifest) > 10000:
                raise ValueError('Artifact manifest перевищує10000 files; use an archive or split the step')
        if not count:
            raise ValueError('Відсутній або порожній declared output: ' + name)
    return {'version': 1, 'outputs': manifest}


def fresh_outputs(pid, spec, action):
    """Archive this step's old namespace, preserving all bytes and current model source."""
    workspace = config.project_dir(pid) / 'workspace'
    history = config.safe_path(config.project_dir(pid), 'history/artifact-attempt-' + uuid.uuid4().hex)
    for name in spec.get('artifact_outputs', []):
        raw = workspace / name
        if raw.is_symlink() or any(parent.is_symlink() for parent in raw.parents if parent.is_relative_to(workspace)):
            raise RuntimeError('Refuse linked output namespace: ' + name)
        source = config.safe_path(workspace, name)
        if source.exists():
            destination = config.safe_path(history, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
    for file in action.files + action.checks:
        if any(covers(name, file.path) for name in spec.get('artifact_outputs', [])):
            target = config.safe_path(workspace, file.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file.content, encoding='utf-8')


def require_inputs(project, step):
    if project['plan'].get('artifact_contract_version', 0) != 1:
        return
    workspace = config.project_dir(project['id']) / 'workspace'
    earlier = [s for s in project['steps'] if s['position'] < step['position']]
    for name in step['spec'].get('artifact_inputs', []):
        path = config.safe_path(workspace, name)
        owner = next((s for s in earlier if any(covers(output, name) for output in s['spec'].get('artifact_outputs', []))), None)
        if owner is None:
            if name.split('/')[0] != 'inputs' or not path.exists():
                raise RuntimeError('Відсутній імпортований input: ' + name)
            continue
        receipt = owner['result'].get('artifact_contract', {})
        if owner['status'] != 'done' or receipt.get('version') != 1:
            raise RuntimeError('Producer ще не має підтвердженого artifact receipt: ' + name)
        expected = {p: value for p, value in receipt.get('outputs', {}).items() if covers(name, p)}
        actual = [path] if path.is_file() else list(path.rglob('*')) if path.is_dir() else []
        actual = {file.relative_to(workspace).as_posix(): file for file in actual if file.is_file() and not file.is_symlink()}
        if not expected or set(actual) != set(expected) or any(stream_identity(config.safe_path(workspace, p)) != value for p, value in expected.items()):
            raise RuntimeError('Input змінився/відсутній після producer: ' + name)
