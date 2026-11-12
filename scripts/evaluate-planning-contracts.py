"""Offline structural evaluation; --local-review calls only an installed local Ollama model."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from study import config
from study.planning_evaluation import evaluate_case, summarize
from study.planning_facts import planning_facts
from study.providers import OllamaProvider


def idle():
    with sqlite3.connect(config.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        if conn.execute("SELECT 1 FROM jobs WHERE status IN ('running','queued')").fetchone():
            raise RuntimeError('Stop evaluation while app jobs are active')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT/'docs/verification/planning-contract-cases-v1.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--local-review', action='store_true')
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--max-seconds', type=int, default=300, choices=range(1, 601))
    args = parser.parse_args()
    # Create-exclusive prevents overwriting even a partial evidence record.
    with args.output.open('x', encoding='utf-8') as out:
        out.write('{}\n')
    report = {'scope': 'Synthetic development fixtures, not independent lab/benchmark acceptance',
              'captured_utc': datetime.now(timezone.utc).isoformat(), 'paid_calls': 0,
              'mode': 'local-review' if args.local_review else 'structural-only',
              'cases_file': str(args.cases), 'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              'source_hashes': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (Path(__file__), ROOT/'backend/study/plan_review.py',
                                             ROOT/'backend/study/planning_facts.py', ROOT/'backend/study/planning_evaluation.py')},
              'rows': [], 'finished': False}

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

    save()
    try:
        idle()
        free = shutil.disk_usage(ROOT).free / 2**30
        if free < 11:
            raise RuntimeError('Require at least11 GiB free, preserving10 GiB reserve')
        report['initial_free_gib'] = round(free, 2)
        cases = json.loads(args.cases.read_text('utf-8'))['cases']
        if args.case:
            unknown = set(args.case) - {case['id'] for case in cases}
            if unknown:
                raise ValueError('Unknown case IDs: ' + ','.join(sorted(unknown)))
            cases = [case for case in cases if case['id'] in args.case]
        provider = None
        facts = planning_facts()
        report['platform_facts'] = json.loads(facts)
        if args.local_review:
            import psutil
            if psutil.virtual_memory().available < 5 * 2**30:
                raise RuntimeError('Require5 GiB available RAM before loading local review model')
            report['model'] = config.settings()['model']
            report['context'] = 8192
            with httpx.Client(timeout=10, trust_env=False) as client:
                tags = client.get(config.settings()['ollama_url'] + '/api/tags')
                tags.raise_for_status()
                models = tags.json()['models']
                report['model_identity'] = next((model for model in models if model['name'] == report['model']), None)
                if report['model_identity'] is None:
                    raise RuntimeError('Configured local model must already be installed; no automatic pulls')
            provider = OllamaProvider(context_override=8192, max_output_tokens=384, timeout_seconds=60)
        started = time.monotonic()
        for case in cases:
            idle()
            if time.monotonic() - started >= args.max_seconds:
                report['stop_reason'] = 'total time limit; remaining cases not run'
                break
            row = evaluate_case(case, facts, provider.generate if provider else None)
            report['rows'].append(row)
            report['summary'] = summarize(report['rows'], len(cases))
            save()
            print(json.dumps({k: row[k] for k in ('id', 'structural_match', 'semantic')}
                             | {'semantic_match': row.get('semantic_match')}, ensure_ascii=False), flush=True)
        report['finished'] = True
        report['summary'] = summarize(report['rows'], len(cases))
        save()
        summary = report['summary']
        return int(not summary['complete'] or bool(summary['structural_mismatches'] or summary['false_accepts']
                                                  or summary['false_rejects'] or summary['errors'])
                   or (args.local_review and bool(summary['semantic_not_run'])))
    except (RuntimeError, ValueError, OSError, httpx.HTTPError) as error:
        report['fatal_error'] = str(error)
        save()
        raise


if __name__ == '__main__':
    raise SystemExit(main())
