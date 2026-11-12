"""Read-only acceptance audit; this script cannot repair project artifacts."""

import argparse
import csv
import json
from pathlib import Path
from study import db, config

parser = argparse.ArgumentParser()
parser.add_argument("project_id")
parser.add_argument(
    "--expectations",
    type=Path,
    required=True,
    help="JSON {relative_csv: {column: expected_number}}; independent oracle",
)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
project = db.project(args.project_id)
errors = []
if project["status"] != "completed" or any(
    s["status"] != "done" for s in project["steps"]
):
    errors.append("Project did not finish the approved workflow")
developer_events = db.query(
    "SELECT kind FROM events WHERE project_id=? AND kind IN ('reviewed_action_staged','run_recovered','run_reviewed','manual_reviewed')",
    (args.project_id,),
)
if developer_events:
    errors.append("Developer intervention events found")
for artifact in project["artifacts"]:
    proof = (
        json.loads(artifact["provenance"])
        if isinstance(artifact["provenance"], str)
        else artifact["provenance"]
    )
    if str(proof.get("type", "")).startswith("developer"):
        errors.append("Developer-authored artifact found: " + artifact["name"])
for step in project["steps"]:
    if step["spec"]["kind"] == "code" and not step["result"].get("checks"):
        errors.append("No executed checker: " + step["spec"]["title"])
workspace = config.project_dir(args.project_id) / "workspace"
for relative, expected in json.loads(args.expectations.read_text("utf-8")).items():
    try:
        with config.safe_path(workspace, relative).open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        for column, value in expected.items():
            assert abs(float(rows[0][column]) - value) < 1e-8, column
    except (OSError, AssertionError, KeyError, ValueError) as error:
        errors.append("Independent CSV check failed: " + relative + ": " + str(error))
names = {a["name"] for a in project["artifacts"] if a["state"] != "stale"}
if not {"report.docx", "report.pdf"} <= names:
    errors.append("DOCX/PDF not generated")
if not db.query(
    "SELECT id FROM events WHERE project_id=? AND kind='automatic_report_review'",
    (args.project_id,),
):
    errors.append("No automatic report review recorded")
requests = db.query(
    "SELECT id,state,actual FROM cloud_requests WHERE project_id=?", (args.project_id,)
)
report = {
    "project_id": args.project_id,
    "execution_checks_passed": not errors,
    "report_content_independently_accepted": False,
    "errors": errors,
    "status": project["status"],
    "developer_intervention_events": len(developer_events),
    "cloud_requests": requests,
    "kind": "Technical acceptance case, not proof of universal lab autonomy",
}
report["code_generation_sources"] = {}
for step in project["steps"]:
    candidate = (
        config.project_dir(args.project_id)
        / "codegen"
        / step["id"]
        / "last-action.json"
    )
    if candidate.exists():
        report["code_generation_sources"][step["id"]] = json.loads(
            candidate.read_text("utf-8")
        )["model"]
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(bool(errors))
