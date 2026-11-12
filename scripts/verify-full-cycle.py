"""Run a reviewed, synthetic calculation through the real API, model and Docker."""

from pathlib import Path
import argparse
import csv
import io
import json
import time
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from study.schemas import Plan

parser = argparse.ArgumentParser()
parser.add_argument("project")
parser.add_argument("--resume", action="store_true")
args = parser.parse_args()
out = ROOT / "data" / "verification"
with httpx.Client(
    base_url="http://127.0.0.1:18573", timeout=30, trust_env=False
) as client:
    client.headers["x-study-token"] = client.get("/api/session").json()["token"]
    pid = args.project
    project = client.get(f"/api/projects/{pid}").json()
    assert project["subject"] == "Технічні перевірки" and "2, 4, 6" in project["task"]
    original_plan = project["plan"]
    # Plan review removes duplicate generate/run steps and defines the output oracle.
    reviewed = {
        "goal": "Обчислити середнє чисел 2, 4, 6 та підготувати звіт",
        "requirements": [
            "Середнє дорівнює 4",
            "Реальний запуск і незалежна перевірка",
            "CSV та DOCX/PDF",
        ],
        "sources": [],
        "missing": [],
        "conflicts": [],
        "report_sections": ["Мета і метод", "Результати", "Висновки"],
        "steps": [
            {
                "title": "Обчислення й перевірка",
                "kind": "code",
                "language": "python",
                "dependencies": [],
                "instruction": "Обчислити середнє чисел [2,4,6]. Створити results.csv із заголовком mean та одним рядком 4.0. Додати check_results.py через checks для незалежної перевірки значення CSV. Використати тільки стандартну бібліотеку. Не створювати звіт у цьому кроці.",
                "expected": "Створений цим запуском results.csv має mean=4.0; check_results.py та main.py завершуються з кодом 0",
                "timeout_seconds": 60,
            },
            {
                "title": "Звіт",
                "kind": "report",
                "instruction": "Сформувати DOCX і PDF за фактичними результатами запуску",
                "expected": "У звіті власний результат 4, CSV-таблиця та пояснення перевірки",
            },
        ],
    }
    if not args.resume:
        response = client.put(f"/api/projects/{pid}/plan", json=reviewed)
        response.raise_for_status()
        response = client.post(f"/api/projects/{pid}/approve")
        response.raise_for_status()
    else:
        assert (
            project["approved_hash"]
            and project["plan"] == Plan.model_validate(reviewed).model_dump()
        )
    response = client.post(f"/api/projects/{pid}/execute")
    response.raise_for_status()
    jid = response.json()["job_id"]
    started = time.monotonic()
    while True:
        response = client.get(f"/api/projects/{pid}")
        response.raise_for_status()
        project = response.json()
        job = next(j for j in project["jobs"] if j["id"] == jid)
        if job["status"] in ("done", "failed", "cancelled", "interrupted"):
            break
        if time.monotonic() - started > 2400:
            raise TimeoutError(
                "Full-cycle verification timed out; the job remains visible in the app"
            )
        time.sleep(3)
    checks = {
        "completed": project["status"] == "completed",
        "csv_mean": False,
        "real_run": False,
        "docx": False,
        "pdf": False,
    }
    for artifact in project["artifacts"]:
        if artifact["state"] == "stale":
            continue
        if artifact["name"] == "results.csv" and artifact["state"] in (
            "executed",
            "verified",
        ):
            data = client.get(f"/api/projects/{pid}/artifacts/{artifact['id']}")
            data.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(data.content.decode("utf-8-sig"))))
            checks["csv_mean"] = len(rows) == 1 and float(rows[0]["mean"]) == 4.0
        if artifact["name"] in ("report.docx", "report.pdf"):
            checks[artifact["name"].split(".")[-1]] = True
    for step in project["steps"]:
        result = step["result"]
        if step["spec"]["kind"] == "code":
            checks["real_run"] = result.get("exit_code") == 0 and bool(
                result.get("run_id")
            )
    record = {
        "project_id": pid,
        "checks": checks,
        "original_plan": original_plan,
        "reviewed_plan": reviewed,
        "project": project,
        "seconds": time.monotonic() - started,
        "note": "Report visual review is a separate required check. This is a synthetic test with a reviewed plan.",
    }
    (out / "full-cycle.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), "utf-8"
    )
    print(checks, flush=True)
    if not all(checks.values()):
        raise SystemExit(1)
