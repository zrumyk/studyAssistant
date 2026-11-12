"""Semester intake. Preserve originals; assign scope from explicit folders."""

from pathlib import Path
import json
import hashlib
from . import config, db, documents

SUBJECTS = {
    '01_AI': 'Штучний інтелект',
    '02_Computer_Graphics': 'Комп’ютерна графіка',
    '03_HMI': 'Людино-машинна взаємодія',
    '04_Computer_Vision': 'Комп’ютерний зір',
    '05_IoT': 'Інтернет речей',
}
ROLE_FOLDERS = {
    '01_Лекції': 'theory', '02_Лабораторні': 'task',
    '03_Зразки_звітів': 'reference', '04_Дані': 'data',
    'theory': 'theory', 'task': 'task', 'reference': 'reference',
    'data': 'data', 'result': 'result',
}


def root():
    return Path(config.settings()['semester_root']).expanduser().resolve()


def preview():
    folder = root()
    result = {'root': str(folder), 'subjects': SUBJECTS, 'files': [], 'unassigned': [],
              'unsupported': [], 'catalog_unmatched': []}
    if not folder.exists():
        return result
    catalog_file = folder / '_catalog.json'
    catalog = json.loads(catalog_file.read_text('utf-8-sig')) if catalog_file.exists() else {}
    if not isinstance(catalog, dict):
        raise ValueError('_catalog.json має містити об’єкт розподілу файлів за шляхами')
    discovered = set()
    for path in sorted(folder.rglob('*')):
        if (not path.is_file() or path.is_symlink()
                or not path.resolve().is_relative_to(folder)
                or any(part.startswith('.') for part in path.relative_to(folder).parts)
                or path.name.startswith('_README') or path.name == '_catalog.json'):
            continue
        relative = path.relative_to(folder)
        discovered.add(relative.as_posix())
        if path.suffix.lower() not in documents.SUPPORTED:
            result['unsupported'].append(str(relative))
            continue
        assigned = catalog.get(relative.as_posix())
        if assigned:
            if (not isinstance(assigned, dict) or assigned.get('role') not in documents.ROLES
                    or not isinstance(assigned.get('subject'), str) or not assigned['subject'].strip()):
                raise ValueError('Некоректний розподіл файлу у _catalog.json: ' + str(relative))
            result['files'].append({'path':str(path), 'relative':str(relative), 'subject':assigned['subject'],
                                    'role':assigned['role'], 'assignment_source':'catalog'})
            continue
        if len(relative.parts) < 2 or relative.parts[0] == '00_UNSORTED':
            result['unassigned'].append(str(relative))
            continue
        subject = SUBJECTS.get(relative.parts[0], relative.parts[0])
        role = next((ROLE_FOLDERS[part] for part in relative.parts[1:-1] if part in ROLE_FOLDERS), None)
        if role is None:
            # Safe default: a task must be explicitly selected before execution.
            role = 'theory'
        result['files'].append({'path': str(path), 'relative': str(relative), 'subject': subject,
                                'role': role, 'assignment_source':'folders'})
    # Never transfer an old assignment by basename: equal names need not be equal inputs.
    result['catalog_unmatched'] = sorted(set(catalog) - discovered)
    if len(result['files']) + len(result['unassigned']) > 500:
        raise ValueError('Початковий ліміт бібліотеки семестру — 500 файлів; імпортуйте частинами')
    return result


def import_semester():
    manifest = preview()
    if manifest['unassigned']:
        raise ValueError('Спочатку розподіліть файли 00_UNSORTED і кореневої папки за предметами')
    result = {'imported': [], 'duplicates': [], 'errors': [], 'unsupported': manifest['unsupported']}
    for index, item in enumerate(manifest['files'], 1):
        try:
            with Path(item['path']).open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            duplicate = db.query("SELECT id FROM documents WHERE hash=? AND subject=? AND role=? AND project_id IS NULL AND status!='superseded' LIMIT 1", (digest, item['subject'], item['role']))
            if duplicate:
                result['duplicates'].append({'file':item['relative'], 'document_id':duplicate[0]['id']})
                continue
            did = documents.import_file(item['path'], item['role'], item['subject'])
            result['imported'].append(did)
        except Exception as error:
            result['errors'].append({'file': item['relative'], 'error': str(error)[:500]})
        db.event(None, 'semester_progress', {'done':index, 'total':len(manifest['files']), 'file':item['relative']})
    if result['imported']:
        result['index_job'] = db.enqueue(None, 'index')
    db.event(None, 'semester_imported', result)
    return result
