from study import db, config, engine
from study.documents import import_file
from study.search import search
from study.schemas import Action


def test_approved_project_pins_source_versions(isolated, client, project, plan):
    p = isolated / "shared.txt"
    p.write_text("Значення параметра дорівнює 10", "utf-8")
    old = import_file(p, "theory", "Приклад")
    engine.save_plan(project["id"], plan)
    engine.approve(project["id"])
    p.write_text("Значення параметра дорівнює 999", "utf-8")
    new = import_file(p, "theory", "Приклад")
    hits = search(project["id"], "Значення параметра", semantic=False)
    assert any(r["document_id"] == old for r in hits)
    assert all(r["document_id"] != new for r in hits)


def test_next_step_main_file_does_not_invalidate_previous_results(
    client, project, plan
):
    pid = project["id"]
    plan["steps"].append(dict(plan["steps"][0], title="Наступний розрахунок"))
    engine.save_plan(pid, plan)
    first, second = db.project(pid)["steps"]
    engine.write_action(
        pid,
        first["id"],
        Action(files=[{"path": "main.py", "content": "print(1)"}], explanation=""),
    )
    root = config.project_dir(pid) / "workspace"
    (root / "result.csv").write_text("value\n1", "utf-8")
    engine.artifact(pid, first["id"], "result.csv", "executed")
    engine.write_action(
        pid,
        second["id"],
        Action(files=[{"path": "main.py", "content": "print(2)"}], explanation=""),
    )
    result = db.one("SELECT state FROM artifacts WHERE name='result.csv'")
    assert result["state"] == "executed"


def test_replacing_consumed_manual_input_requires_review(isolated, project, plan):
    pid = project["id"]
    engine.save_plan(pid, plan)
    engine.approve(pid)
    source = isolated / "observations.txt"
    source.write_text("value=4", "utf-8")
    first = import_file(source, "result", "Приклад", pid)
    sid = db.project(pid)["steps"][0]["id"]
    db.execute("UPDATE steps SET status='waiting_user' WHERE id=?", (sid,))
    engine.manual_result(pid, sid, "observations.txt додано", confirmed=True)
    assert db.project(pid)["approved_hash"]
    assert db.project(pid)["steps"][0]["result"]["document_versions"][0]["id"] == first
    source.write_text("value=999", "utf-8")
    import_file(source, "result", "Приклад", pid)
    assert db.project(pid)["approved_hash"] is None
    assert db.project(pid)["status"] == "analysis"
