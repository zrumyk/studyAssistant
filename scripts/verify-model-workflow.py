"""Real local planner checks. Creates two test projects, never approves or executes them."""

from pathlib import Path
import json
import sys
import time
import httpx
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from study import config
from study.providers import OllamaProvider

parser = argparse.ArgumentParser()
parser.add_argument("--skip-embedding", action="store_true")
args = parser.parse_args()

cases = [
    (
        "calculation",
        "Обчислити середнє чисел 2, 4, 6. Зберегти CSV та звіт. Номер варіанта для цього тесту не потрібен.",
    ),
    (
        "missing",
        "Обчислити площу прямокутника. Довжину та ширину не надано; їх має уточнити користувач.",
    ),
]
out = config.DATA / "verification"
out.mkdir(exist_ok=True)
results = []
with httpx.Client(
    base_url="http://127.0.0.1:18573", timeout=30, trust_env=False
) as client:
    client.headers["x-study-token"] = client.get("/api/session").json()["token"]
    for name, task in cases:
        response = client.post(
            "/api/projects",
            json={
                "title": "Перевірка локального планувальника — " + name,
                "task": task,
                "subject": "Технічні перевірки",
                "variant": "",
            },
        )
        response.raise_for_status()
        pid = response.json()["id"]
        response = client.post(f"/api/projects/{pid}/analyse")
        response.raise_for_status()
        job_id = response.json()["job_id"]
        started = time.monotonic()
        while True:
            project = client.get(f"/api/projects/{pid}").json()
            matching = [j for j in project["jobs"] if j["id"] == job_id]
            if matching and matching[0]["status"] in (
                "done",
                "failed",
                "interrupted",
                "cancelled",
            ):
                break
            if time.monotonic() - started > 1000:
                raise TimeoutError(
                    "Planner verification timed out; inspect the persisted job"
                )
            time.sleep(2)
        plan = project.get("plan", {})
        gate = client.post(f"/api/projects/{pid}/execute")
        checks = {
            "plan_saved": bool(plan.get("steps")),
            "not_approved": project.get("approved_hash") is None,
            "execution_blocked": gate.status_code in (400, 409),
            "missing_handled": bool(plan.get("missing"))
            if name == "missing"
            else not plan.get("missing"),
            "no_execution": all(
                s["status"] == "pending" for s in project.get("steps", [])
            ),
            "report_tool": bool(plan.get("steps"))
            and plan["steps"][-1]["kind"] == "report",
            "automatic_calculation": name != "calculation"
            or (
                bool(plan.get("steps"))
                and not any(s["kind"] == "manual" for s in plan["steps"])
                and any(
                    s["kind"] == "code" and not s["dependencies"] for s in plan["steps"]
                )
            ),
        }
        results.append(
            {
                "case": name,
                "project_id": pid,
                "checks": checks,
                "plan": plan,
                "jobs": matching,
                "seconds": time.monotonic() - started,
            }
        )
        (out / "model-workflow.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), "utf-8"
        )
        print(name, checks, flush=True)

if args.skip_embedding:
    raise SystemExit(0 if all(all(r["checks"].values()) for r in results) else 1)

provider = OllamaProvider()
provider.unload()
vectors = provider.embed(
    ["Температура датчика", "Показання температурного сенсора", "Креслення трикутника"]
)
import numpy as np

matrix = np.array(vectors)
matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
embedding_check = {
    "shape": list(matrix.shape),
    "finite": bool(np.isfinite(matrix).all()),
    "related_similarity": float(matrix[0] @ matrix[1]),
    "unrelated_similarity": float(matrix[0] @ matrix[2]),
}
(out / "embedding.json").write_text(json.dumps(embedding_check, indent=2), "utf-8")
print("embedding", embedding_check, flush=True)
if not all(all(r["checks"].values()) for r in results):
    raise SystemExit(1)
