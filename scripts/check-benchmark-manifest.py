"""Check pinned benchmark inputs without executing them or contacting providers."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from study.benchmark_manifest import check_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'docs/benchmarks/generalization/manifest.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = check_manifest(json.loads(args.manifest.read_text(encoding='utf-8')), ROOT)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        report = {'errors': [type(error).__name__ + ': invalid or unreadable manifest'], 'cases': []}
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(content + '\n', encoding='utf-8')
    print(content)
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
