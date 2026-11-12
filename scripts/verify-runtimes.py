"""Real Docker smoke checks, one at a time, with the same sandbox as user jobs."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from study import config
from study.providers import OllamaProvider
from study.runtime import DockerRuntime, RUNTIMES

PROGRAMS = {
    "python": "from pathlib import Path\nassert sum([2,4,6])/3 == 4\nPath('answer.txt').write_text('4')\nprint('SMOKE_OK')\n",
    "javascript": "require('fs').writeFileSync('answer.txt', String((2+4+6)/3)); console.log('SMOKE_OK');",
    "typescript": "import * as fs from 'fs'; const answer: number=(2+4+6)/3; fs.writeFileSync('answer.txt',String(answer)); console.log('SMOKE_OK');",
    "c": '#include <stdio.h>\nint main(){FILE *f=fopen("answer.txt","w");if(!f)return 1;fprintf(f,"4");fclose(f);puts("SMOKE_OK");return 0;}\n',
    "cpp": '#include <fstream>\n#include <iostream>\nint main(){std::ofstream("answer.txt")<<4;std::cout<<"SMOKE_OK";return 0;}\n',
    "java": 'import java.nio.file.*; public class Main {public static void main(String[] a)throws Exception{Files.writeString(Path.of("answer.txt"),"4");System.out.println("SMOKE_OK");}}',
    "csharp": 'System.IO.File.WriteAllText("/work/answer.txt", "4"); System.Console.WriteLine("SMOKE_OK");',
    "r": 'writeLines("4", "answer.txt"); cat("SMOKE_OK")',
    "sql": "CREATE TABLE numbers(value INTEGER); INSERT INTO numbers VALUES(2),(4),(6); SELECT AVG(value) AS mean FROM numbers;",
    "octave": 'fid=fopen("answer.txt","w"); fprintf(fid,"4"); fclose(fid); disp("SMOKE_OK");',
}
parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path)
parser.add_argument(
    "--languages",
    nargs="+",
    choices=list(PROGRAMS),
    default=["python", "javascript", "sql"],
)
args = parser.parse_args()
OllamaProvider().unload()
out = config.DATA / "verification"
out.mkdir(exist_ok=True)
target = args.output or out / "runtime-smoke.json"
target.parent.mkdir(parents=True, exist_ok=True)
results = []
for language in args.languages:
    # Fresh test folder each time; SQL cannot accidentally reuse an old database.
    import uuid

    pid = "runtimecheck" + uuid.uuid4().hex
    workspace = config.project_dir(pid) / "workspace"
    workspace.mkdir(parents=True)
    (workspace / RUNTIMES[language][1]).write_text(PROGRAMS[language], "utf-8")
    expected = "results.json" if language == "sql" else "answer.txt"
    run = None
    try:
        run = DockerRuntime().run(
            pid, "smoke", language, [], 60, lambda: False, [expected]
        )
        if language == "sql":
            answer = json.loads((workspace / expected).read_text("utf-8"))[0]["rows"][
                0
            ][0]
            content_ok = answer == 4
        else:
            content_ok = (workspace / expected).read_text("utf-8").strip() == "4"
        passed = run["exit_code"] == 0 and not run["oom"] and content_ok
        results.append(
            {
                "language": language,
                "passed": passed,
                "run": run,
                "project_folder": str(workspace.parent),
            }
        )
    except Exception as error:
        results.append(
            {
                "language": language,
                "passed": False,
                "error": str(error),
                "run": run,
                "project_folder": str(workspace.parent),
            }
        )
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
    print(language, "PASS" if results[-1]["passed"] else "FAIL", flush=True)
if not all(r["passed"] for r in results):
    raise SystemExit(1)
