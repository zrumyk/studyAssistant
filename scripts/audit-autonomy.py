"""Read-only inventory of source, durable workflow state and all runtime logs.

Writes only the requested audit file; never repairs or changes a lab's state.
No settings, credentials, prompts or document contents are exported.
"""
import argparse
import ast
import collections
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit():
    inventory = []
    source_errors = []
    for folder in ('backend', 'frontend/src', 'tests', 'scripts'):
        for path in sorted((ROOT / folder).rglob('*')):
            if not path.is_file() or path.suffix not in ('.py', '.tsx', '.ts', '.css', '.ps1'):
                continue
            content = path.read_bytes()
            inventory.append({'path': path.relative_to(ROOT).as_posix(), 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
            if path.suffix == '.py':
                try:
                    ast.parse(content.decode('utf-8-sig'), filename=str(path))
                except (SyntaxError, UnicodeError) as error:
                    source_errors.append({'path': path.relative_to(ROOT).as_posix(), 'error': str(error)})
    signatures = collections.Counter()
    outcomes = collections.Counter()
    corrupt = []
    for path in sorted((ROOT / 'data/projects').glob('*/runs/*/result.json')):
        try:
            result = json.loads(path.read_text('utf-8'))
        except (OSError, ValueError):
            corrupt.append(path.relative_to(ROOT).as_posix())
            continue
        outcome = 'oom' if result.get('oom') else ('cancelled' if result.get('reason') == 'Зупинено користувачем' else ('failed' if result.get('exit_code') or result.get('reason') else 'runtime_success'))
        outcomes[outcome] += 1
    logs = list((ROOT / 'data/logs').glob('*.log')) + list((ROOT / 'data/projects').glob('*/runs/*.log'))
    total_bytes = 0
    for path in logs:
        total_bytes += path.stat().st_size
        # Stream all lines without copying private data or huge logs to the audit.
        with path.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                for token in ('Traceback', 'AssertionError', 'KeyError', 'TypeError', 'ValueError', 'MemoryError', 'ModuleNotFoundError', 'ImportError', 'SyntaxError', 'Killed', 'STUDY_ASSERTS_EXECUTED'):
                    if token in line:
                        signatures[token] += 1
    with sqlite3.connect((ROOT / 'data/study.sqlite3').as_uri() + '?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        def rows(sql):
            return [dict(row) for row in c.execute(sql)]
        database = {
            'job_counts': rows('SELECT kind,status,COUNT(*) count FROM jobs GROUP BY kind,status'),
            'step_counts': rows('SELECT status,COUNT(*) count FROM steps GROUP BY status'),
            'event_counts': rows('SELECT kind,COUNT(*) count FROM events GROUP BY kind'),
            'cloud_counts': rows('SELECT state,COUNT(*) count,SUM(actual) estimated_usd FROM cloud_requests GROUP BY state'),
            'projects': rows('SELECT id,title,status,revision FROM projects ORDER BY updated DESC'),
            'pending_jobs': rows("SELECT id,project_id,kind,status FROM jobs WHERE status IN ('running','queued')"),
        }
    return {
        'kind': 'Read-only source and runtime audit; not independent acceptance of lab content',
        'source_inventory': inventory, 'source_parse_errors': source_errors,
        'run_outcomes': dict(outcomes), 'corrupt_run_records': corrupt,
        'logs_scanned': len(logs), 'log_bytes_scanned': total_bytes,
        'log_signature_line_counts': dict(signatures), 'database': database,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({key: report[key] for key in ('run_outcomes', 'logs_scanned', 'log_bytes_scanned', 'source_parse_errors', 'corrupt_run_records')}, ensure_ascii=False))
