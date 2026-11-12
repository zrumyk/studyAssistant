"""Read-only integrity checks; these do not certify semantic correctness or secrecy."""
import hashlib
import re
from pathlib import Path, PurePosixPath


GATES = ('permissions', 'approval', 'budget', 'isolation', 'state_preservation', 'honest_stop')


def check_manifest(manifest: dict, root: Path) -> dict:
    root = root.resolve()
    errors = []
    rows = []
    seen = set()

    def ref(value, label):
        if not isinstance(value, dict):
            return [f'{label}: missing file reference']
        name, digest = value.get('path'), value.get('sha256')
        if not isinstance(name, str) or not name:
            return [f'{label}: missing path']
        path = PurePosixPath(name)
        if (path.is_absolute() or '\\' in name or ':' in name or '..' in path.parts
                or any(p.startswith('.') for p in path.parts)):
            return [f'{label}: unsafe path']
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            return [f'{label}: path escapes repository']
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            return [f'{label}: missing/invalid sha256']
        if not target.is_file():
            return [f'{label}: missing file']
        hasher = hashlib.sha256()
        with target.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
        return [] if hasher.hexdigest() == digest else [f'{label}: sha256 mismatch']

    if manifest.get('schema_version') != 1:
        errors.append('unsupported schema_version')
    cases = manifest.get('cases')
    if not isinstance(cases, list) or not cases:
        return {'errors': errors + ['cases must be a nonempty list'], 'cases': [], 'evidence_complete': 0}
    for case in cases:
        if not isinstance(case, dict):
            errors.append('case must be an object')
            continue
        ident = case.get('id')
        if not isinstance(ident, str) or not ident or ident in seen:
            errors.append('missing/duplicate case id')
        else:
            seen.add(ident)
        status = case.get('status')
        issues = []
        declared = case.get('missing', [])
        if not isinstance(declared, list) or any(not isinstance(item, str) or not item.strip() for item in declared):
            errors.append(f'{ident}: missing must be a list of nonempty blocker descriptions')
        else:
            issues.extend('declared blocker: ' + item for item in declared)
        if status not in ('planned', 'ready', 'failed', 'passed'):
            errors.append(f'{ident}: unknown status')
        if case.get('kind') not in ('lab', 'mechanism'):
            errors.append(f'{ident}: unknown kind')
        if case.get('exposure') not in ('development', 'reserved_unseen', 'independent'):
            errors.append(f'{ident}: unknown exposure')
        for field in ('family', 'profile', 'criterion', 'next_action'):
            if not isinstance(case.get(field), str) or not case[field].strip():
                errors.append(f'{ident}: missing {field}')
        sources = case.get('sources')
        if not isinstance(sources, list) or not sources:
            issues.append('sources: missing primary inputs')
            sources = []
        for index, source in enumerate(sources):
            issues.extend(ref(source, f'source[{index}]'))
        for field in ('brief', 'oracle', 'resources'):
            issues.extend(ref(case.get(field), field))
        freeze = case.get('freeze')
        issues.extend(ref(freeze, 'freeze'))
        # A shared oracle file/hash in producer inputs is an observable leak.
        oracle = case.get('oracle')
        if isinstance(oracle, dict):
            for source in [*sources, case.get('brief')]:
                if isinstance(source, dict) and any(
                    oracle.get(key) and oracle.get(key) == source.get(key)
                    for key in ('path', 'sha256')
                ):
                    issues.append('oracle overlaps producer input')
        if case.get('exposure') == 'reserved_unseen':
            issues.append('input still reserved; independence not established')
        if case.get('exposure') == 'independent':
            # A pinned custody record must explain post-freeze selection and interventions.
            issues.extend(ref(case.get('selection_record'), 'selection_record'))
        ready = not issues
        evidence_issues = []
        if status in ('failed', 'passed'):
            evidence_issues.extend(ref(case.get('result'), 'result'))
        if status == 'passed':
            evidence_issues.extend(ref(case.get('independent_review'), 'independent_review'))
            gates = case.get('gates', {})
            if not isinstance(gates, dict):
                gates = {}
            for gate in GATES:
                evidence_issues.extend(ref(gates.get(gate), f'gate:{gate}'))
        if status in ('ready', 'passed') and issues:
            errors.append(f'{ident}: claimed {status} without complete readiness')
        if status == 'passed' and evidence_issues:
            errors.append(f'{ident}: claimed passed without complete evidence')
        if status == 'failed' and evidence_issues:
            errors.append(f'{ident}: failed attempt missing pinned result')
        rows.append({'id': ident, 'status': status, 'ready': ready,
                     'issues': issues + evidence_issues,
                     'evidence_complete': status == 'passed' and ready and not evidence_issues
                     and case.get('kind') == 'lab'})
    return {'errors': errors, 'cases': rows,
            'evidence_complete': sum(row['evidence_complete'] for row in rows),
            'limitation': 'File integrity only. No semantic acceptance, oracle independence, '
                          'holdout secrecy, or version equivalence is certified.'}
