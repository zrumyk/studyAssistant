"""G02 external oracle. Run submitted code ONLY inside an explicitly mounted Docker sandbox.

Self-test creates synthetic observations, never a working university.py solution.
Do not import this grader or its expected observations into producer inputs/RAG.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

TABLES = ('course', 'department', 'room', 'enrolled', 'majors_in', 'student')
FLAGS = ('--show-students', '--show-enrollments', '--add-student', '--enroll-student', '--declare-major')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        names = sorted(row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        if names != sorted(TABLES):
            raise ValueError('Unexpected table set')
        schema, data = {}, {}
        for name in TABLES:
            schema[name] = {'columns': [list(row) for row in conn.execute(f'PRAGMA table_info("{name}")')],
                            'foreign_keys': [list(row) for row in conn.execute(f'PRAGMA foreign_key_list("{name}")')],
                            'strict': next(row[-1] for row in conn.execute('PRAGMA table_list') if row[1] == name)}
            data[name] = sorted([list(row) for row in conn.execute(f'SELECT * FROM "{name}"')], key=lambda row: json.dumps(row))
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Database integrity failure')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Foreign-key violation persisted')
        return {'schema': schema, 'data': data}


def displayed_students(state):
    result = []
    for sid, name in state['student']:
        majors = [dept for student, dept in state['majors_in'] if student == sid]
        result += [[str(sid), name, dept] for dept in majors or ['Undeclared']]
    return result


def displayed_enrollments(state):
    counts = Counter(course for _, course in state['enrolled'])
    return [[number, str(counts[number])] for number, *_ in state['course']]


def scenarios(seed):
    state = copy.deepcopy(seed['data'])
    cases = []

    def add(ident, args, *, kind='update', error=False):
        for name in TABLES:
            state[name].sort(key=lambda row: json.dumps(row))
        cases.append({'id': ident, 'args': args, 'kind': kind, 'error_expected': error,
                      'expected': {'schema': seed['schema'], 'data': copy.deepcopy(state)}})

    add('help', ['--help'], kind='help')
    add('initial-students', ['--show-students'], kind='students')
    add('initial-enrollments', ['--show-enrollments'], kind='enrollments')
    fresh = max(row[0] for row in state['student']) + 101
    missing = fresh + 10001
    course = state['course'][0][0]
    dept = state['department'][0][0]
    literal = "D'Arcy; DROP TABLE student;--"
    state['student'].append([fresh, literal])
    add('literal-name-persisted', ['--add-student', str(fresh), literal])
    state['enrolled'].append([fresh, course])
    add('enroll-persisted', ['--enroll-student', str(fresh), course])
    state['majors_in'].append([fresh, dept])
    add('major-persisted', ['--declare-major', str(fresh), dept])
    add('after-students', ['--show-students'], kind='students')
    add('after-enrollments', ['--show-enrollments'], kind='enrollments')
    add('duplicate-id', ['--add-student', str(fresh), 'replacement'], error=True)
    add('duplicate-enrollment', ['--enroll-student', str(fresh), course], error=True)
    add('duplicate-major', ['--declare-major', str(fresh), dept], error=True)
    add('fk-enrollment-student', ['--enroll-student', str(missing), course], error=True)
    add('fk-enrollment-course', ['--enroll-student', str(fresh), 'UNKNOWN_COURSE'], error=True)
    add('fk-major-student', ['--declare-major', str(missing), dept], error=True)
    add('fk-major-department', ['--declare-major', str(fresh), 'UNKNOWN_DEPT'], error=True)
    add('injection-student', ['--enroll-student', f'{fresh} OR 1=1', course], error=True)
    add('injection-course', ['--enroll-student', str(fresh), "x' OR '1'='1"], error=True)
    state['student'].append([fresh+1, 'Recovery after rejected updates'])
    add('recovery-persisted', ['--add-student', str(fresh+1), 'Recovery after rejected updates'])
    add('final-students', ['--show-students'], kind='students')
    add('final-enrollments', ['--show-enrollments'], kind='enrollments')
    return cases


def row_errors(case, actual):
    errors = []
    if actual.get('id') != case['id'] or actual.get('args') != case['args']:
        errors.append('case identity/arguments differ')
    if actual.get('timed_out') or actual.get('output_limited'):
        errors.append('timeout/output limit')
    if actual.get('snapshot') != case['expected']:
        errors.append('schema/state/persistence differs')
    stdout, stderr = actual.get('stdout', ''), actual.get('stderr', '')
    if case['error_expected']:
        # Source requires a readable error, not a particular exit code or wording.
        if not (stdout+stderr).strip() or 'Traceback (most recent call last)' in stdout+stderr:
            errors.append('missing/unreadable rejection')
    else:
        if actual.get('exit_code') != 0:
            errors.append('successful operation did not exit0')
        if case['kind'] in ('students', 'enrollments'):
            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            header = 'ID|Name|Major' if case['kind'] == 'students' else 'Number|Enrollment'
            expected = displayed_students(case['expected']['data']) if case['kind'] == 'students' else displayed_enrollments(case['expected']['data'])
            if not lines or lines[0] != header or Counter(tuple(line.split('|')) for line in lines[1:]) != Counter(map(tuple, expected)):
                errors.append('display rows/header differ (outer joins, duplicates or counts)')
        elif case['kind'] == 'help' and not all(flag in stdout for flag in FLAGS):
            errors.append('incomplete command-line interface')
    return errors


def check(cases, observations):
    errors = []
    if len(observations) != len(cases):
        errors.append('missing/extra observation')
    for case, actual in zip(cases, observations):
        errors += [case['id']+': '+error for error in row_errors(case, actual)]
    return errors


def self_test(seed):
    cases = scenarios(seed)
    valid = []
    for case in cases:
        stdout = ''
        if case['error_expected']:
            stdout = 'Readable constraint error\n'
        elif case['kind'] == 'help':
            stdout = ' '.join(FLAGS)
        elif case['kind'] in ('students', 'enrollments'):
            header, rows = ('ID|Name|Major', displayed_students(case['expected']['data'])) if case['kind'] == 'students' else ('Number|Enrollment', displayed_enrollments(case['expected']['data']))
            stdout = header+'\n'+'\n'.join('|'.join(row) for row in rows)+'\n'
        valid.append({'id': case['id'], 'args': case['args'], 'snapshot': copy.deepcopy(case['expected']),
                      'stdout': stdout, 'stderr': '', 'exit_code': 0, 'timed_out': False})
    assert not check(cases, valid)
    mutants = {}
    for name in ('missing', 'extra', 'wrong-args', 'timeout', 'zero-course-omitted', 'undeclared-omitted',
                 'duplicate-row', 'wrong-count', 'lost-commit', 'missing-error', 'foreign-key-removed', 'literal-corrupted'):
        records = copy.deepcopy(valid)
        if name == 'missing': records.pop()
        elif name == 'extra': records.append(copy.deepcopy(records[0]))
        elif name == 'wrong-args': records[0]['args'] = []
        elif name == 'timeout': records[0]['timed_out'] = True
        elif name == 'zero-course-omitted': records[2]['stdout'] = '\n'.join(line for line in records[2]['stdout'].splitlines() if not line.endswith('|0'))
        elif name == 'undeclared-omitted': records[1]['stdout'] = '\n'.join(line for line in records[1]['stdout'].splitlines() if not line.endswith('|Undeclared'))
        elif name == 'duplicate-row': records[1]['stdout'] += records[1]['stdout'].splitlines()[1]+'\n'
        elif name == 'wrong-count': records[2]['stdout'] += 'wrong|99\n'
        elif name == 'lost-commit': records[3]['snapshot'] = copy.deepcopy(seed)
        elif name == 'missing-error': records[8]['stdout'] = ''
        elif name == 'foreign-key-removed': records[0]['snapshot']['schema']['enrolled']['foreign_keys'] = []
        elif name == 'literal-corrupted':
            next(row for row in records[3]['snapshot']['data']['student'] if row[0] == int(cases[3]['args'][1]))[1] += 'changed'
        errors = check(cases, records)
        assert errors, name
        mutants[name] = errors[0]
    return {'scope': 'Oracle self-test only, synthetic observations; no student program executed',
            'cases': len(cases), 'rejected_mutations': mutants}


def execute(program, seed, output):
    if os.name != 'posix' or not Path('/.dockerenv').is_file():
        raise RuntimeError('Never execute submitted code on host. Mount this oracle/program/seed readonly in a constrained Docker sandbox.')
    if output.exists():
        raise ValueError('Never overwrite evidence')
    original = digest(seed)
    expected = snapshot(seed)
    cases = scenarios(expected)
    record = {'scope': 'External oracle replay in sandbox; not a course submission or complete lab acceptance',
              'program_sha256': digest(program), 'seed_sha256': original, 'python': sys.version,
              'sqlite': sqlite3.sqlite_version, 'observations': [], 'errors': []}
    with tempfile.TemporaryDirectory() as temp:
        database = Path(temp)/'working.sqlite'
        database.write_bytes(seed.read_bytes())
        for case in cases:
            timed_out = False
            try:
                run = subprocess.run([sys.executable, str(program.resolve()), str(database), *case['args']],
                                     stdin=subprocess.DEVNULL, capture_output=True, timeout=5, cwd=program.parent)
                stdout, stderr, code = run.stdout, run.stderr, run.returncode
            except subprocess.TimeoutExpired as error:
                timed_out = True
                stdout, stderr, code = error.stdout or b'', error.stderr or b'', None
            limited = len(stdout)+len(stderr) > 1024*1024
            actual = {'id': case['id'], 'args': case['args'], 'exit_code': code, 'timed_out': timed_out,
                      'output_limited': limited, 'stdout': stdout[:524288].decode('utf-8', 'replace'),
                      'stderr': stderr[:524288].decode('utf-8', 'replace')}
            try: actual['snapshot'] = snapshot(database)
            except (sqlite3.Error, ValueError) as error: actual['snapshot_error'] = str(error)
            record['observations'].append(actual)
            record['errors'] += [case['id']+': '+error for error in row_errors(case, actual)]
            output.write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if digest(seed) != original:
        record['errors'].append('source seed changed')
    record['complete'] = True
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return bool(record['errors'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--program', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(snapshot(args.seed)), ensure_ascii=False))
        return 0
    if not args.program or not args.output:
        parser.error('--program and --output required for sandbox replay')
    return execute(args.program, args.seed, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
