"""Persist the requirement for a separate probe after a real memory failure."""
import json
from . import config


def marker_path(pid, sid):
    return config.safe_path(config.project_dir(pid), f'codegen/{sid}/resource-probe-required.json')


def remember_oom(pid, sid, result):
    if result.get('oom'):
        path = marker_path(pid, sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'run_id': result.get('run_id'), 'resource_limits': result.get('resource_limits', {})}), 'utf-8')


def require_probe(pid, sid, language, action, previous):
    try:
        failure = json.loads(previous) if previous else {}
    except (ValueError, TypeError):
        failure = {}
    if isinstance(failure, dict) and failure.get('oom'):
        remember_oom(pid, sid, failure)
    if not marker_path(pid, sid).exists():
        return
    if language != 'python' or not any(file.path == 'resource_probe.py' for file in action.files):
        raise ValueError(json.dumps({'oom': True, 'resource_probe_required': True,
            'reason': 'Після OOM основний запуск заборонено без окремого resource_probe.py у files поточного кроку. Проба має виконати один справжній training batch тієї самої моделі/даних з оптимізацією пам’яті та записати resource_probe.json. Не змінюй погоджені архітектуру, вибірку й епохи.'}, ensure_ascii=False))
