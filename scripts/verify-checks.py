"""Prove the real sandbox runs an explicitly named nested checker."""

from pathlib import Path
import json
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from study import config
from study.providers import OllamaProvider
from study.runtime import DockerRuntime

OllamaProvider().unload()
pid = "checkerprobe" + uuid.uuid4().hex
workspace = config.project_dir(pid) / "workspace"
(workspace / "checks").mkdir(parents=True)
(workspace / "inputs").mkdir()
(workspace / "inputs/source.txt").write_text("source", "utf-8")
(workspace / "main.py").write_text(
    "from pathlib import Path\n"
    "assert Path('inputs/source.txt').read_text() == 'source'\n"
    "try:\n    Path('inputs/source.txt').write_text('changed')\n"
    "except OSError:\n    pass\n"
    "else:\n    raise AssertionError('INPUT_WRITABLE')\n"
    "Path('answer.txt').write_text('4')\n",
    "utf-8",
)
results = []
for valid in (False, True):
    (workspace / "checks/probe.py").write_text(
        "from pathlib import Path\nassert Path('answer.txt').read_text() == '"
        + ("4" if valid else "99")
        + "', 'CHECK_SENTINEL'\n",
        "utf-8",
    )
    run = DockerRuntime().run(
        pid,
        "checker",
        "python",
        [],
        30,
        lambda: False,
        expected_files=["answer.txt"],
        check_paths=["checks/probe.py"],
    )
    passed = (
        run["exit_code"] == 0
        if valid
        else run["exit_code"] != 0 and "CHECK_SENTINEL" in run["log"]
    )
    passed = passed and run["checks"] == ["checks/probe.py"]
    results.append({"positive_case": valid, "passed": passed, "run": run})
target = config.DATA / "verification/checker-smoke.json"
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps(results, ensure_ascii=False, indent=2), "utf-8")
print([(r["positive_case"], r["passed"]) for r in results])
if not all(r["passed"] for r in results):
    raise SystemExit(1)
