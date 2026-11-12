"""Real sandbox mechanism fixtures; never a lab result or independent acceptance."""
import argparse
import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from study import config
from study.runtime import DockerRuntime

C_PROGRAM = '#include <stdio.h>\nint main(void){int n;if(scanf("%d",&n)!=1){puts("EOF");return 0;}printf("%d\\n",n*2);return 0;}\n'
JS_PROGRAM = 'const fs=require("fs");process.stdout.write(fs.readFileSync(0,"utf8"));\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Preserve existing evidence; choose a fresh output path')
    with sqlite3.connect(config.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True) as con:
        if con.execute("SELECT count(*) FROM jobs WHERE status IN ('running','queued')").fetchone()[0]:
            raise ValueError('Active jobs; do not start technical fixtures')
    if shutil.disk_usage(ROOT).free < 18 * 1024**3:
        raise ValueError('Need18GiB for the conservative two-image resource assessment')
    report = dict(scope=__doc__, started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  initial_free_gib=round(shutil.disk_usage(ROOT).free / 2**30, 2), results=[])

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    runtime = DockerRuntime()
    for language in ('c', 'javascript'):
        print('Preparing shared-checker image: ' + language, flush=True)
        image = runtime.prepare(language, [], python_checker=True)
        report.setdefault('images', {})[language] = image
        save()

    def run(label, language, source, value, checker_sources, expected, timeout=15, cancel=False):
        pid = 'protocolcheck' + uuid.uuid4().hex
        root = config.project_dir(pid)
        workspace = root / 'workspace'
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ('main.c' if language == 'c' else 'main.js')).write_text(source, encoding='utf-8')
        for name, content in checker_sources.items():
            (workspace / name).write_text(content, encoding='utf-8')
        started = time.monotonic()
        result = runtime.run(pid, 'technical-fixture', language, [], timeout,
                             lambda: cancel and time.monotonic() - started > 2,
                             check_paths=list(checker_sources), stdin_text=value)
        valid = expected(result, root)
        report['results'].append(dict(label=label, language=language, project_dir=str(root),
                                      verified=valid, result=result))
        save()
        print(label + ': ' + ('PASS' if valid else 'FAIL'), flush=True)
        if not valid:
            raise AssertionError(label + ': preserve first failure before changing fixtures')

    def checker(text):
        return 'from pathlib import Path\nassert Path(".study_main.stdout").read_text("utf-8") == ' + repr(text) + '\n'

    passed = lambda result, root: result['exit_code'] == 0 and not result['reason'] and len(result['checks']) == 1
    run('c-finite-stdin', 'c', C_PROGRAM, '13\n', {'check_value.py': checker('26\n')}, passed)
    run('c-eof', 'c', C_PROGRAM, '', {'check_eof.py': checker('EOF\n')}, passed)
    injection = "український текст '; $(touch injected)\nsecond line\n"
    run('javascript-unicode-literal-input', 'javascript', JS_PROGRAM, injection,
        {'check_text.py': checker(injection) + 'assert not Path("injected").exists()\n'}, passed)
    run('javascript-eof', 'javascript', JS_PROGRAM, None, {'check_eof.py': checker('')}, passed)
    run('c-wrong-result', 'c', C_PROGRAM, '13\n', {'check_wrong.py': checker('27\n')},
        lambda result, root: result['exit_code'] != 0 and not result['checks']
        and result['checker_results'][0]['exit_code'] != 0 and 'AssertionError' in result['log'])
    run('javascript-uncalled-checker', 'javascript', JS_PROGRAM, 'data\n',
        {'check_uncalled.py': 'def test():\n    assert 2+2==4\n'},
        lambda result, root: result['exit_code'] != 0 and not result['checks'] and 'assert' in result['log'])
    run('c-failed-producer-skips-checker', 'c', 'int main(void){return 7;}\n', '',
        {'check_skipped.py': 'assert 2+2==4\n'},
        lambda result, root: result['exit_code'] == 7 and not result['checks']
        and not result['checker_results'][0]['completed'])
    run('c-checker-short-circuit', 'c', C_PROGRAM, '13\n',
        {'check_a.py': checker('26\n'), 'check_b.py': checker('27\n'), 'check_c.py': checker('26\n')},
        lambda result, root: result['exit_code'] != 0 and result['checks'] == ['check_a.py']
        and not result['checker_results'][2]['completed'])
    run('c-timeout', 'c', '#include <unistd.h>\nint main(void){while(1)sleep(1);}\n', None,
        {'check_unused.py': 'assert 2+2==4\n'},
        lambda result, root: bool(result['reason']) and not result['checks'], timeout=2)
    run('javascript-cancel', 'javascript', 'setInterval(()=>{},1000);\n', None,
        {'check_unused.py': 'assert 2+2==4\n'},
        lambda result, root: result['reason'] == 'Зупинено користувачем' and not result['checks'], cancel=True)
    report['final_free_gib'] = round(shutil.disk_usage(ROOT).free / 2**30, 2)
    report['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save()


if __name__ == '__main__':
    main()
