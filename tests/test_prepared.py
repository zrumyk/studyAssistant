import json
import pytest
from study import db, engine, cloud, prepared
from study.schemas import Action


def completed_cloud(project, plan, complete=True, override=None):
    pid = project["id"]
    engine.save_plan(pid, plan)
    engine.approve(pid)
    action = Action(
        files=[
            {
                "path": "main.py",
                "content": "from pathlib import Path\nPath('answer.txt').write_text('4')",
            }
        ],
        checks=[
            {
                "path": "check_result.py",
                "content": "from pathlib import Path\nassert Path('answer.txt').read_text() == '4'",
            }
        ],
        entrypoint="main.py",
        expected_files=["answer.txt"],
        explanation="Перевірити число",
    )
    if override:
        action = action.model_copy(update=override)
    q = cloud.quote(pid, "Return code", "Code")
    jid = cloud.approve_and_enqueue(q["id"])
    db.execute(
        "UPDATE cloud_requests SET state='done',response=? WHERE id=?",
        (action.model_dump_json(), q["id"]),
    )
    db.execute(
        "UPDATE jobs SET status='done',result=? WHERE id=?",
        (json.dumps({"complete": complete}), jid),
    )
    step = db.project(pid)["steps"][0]
    db.execute("UPDATE steps SET status='failed',attempts=3 WHERE id=?", (step["id"],))
    return pid, step["id"], q["id"], action


def test_local_review_does_not_claim_cloud_generation(project, plan):
    pid, sid, rid, action = completed_cloud(project, plan, complete=False)
    prepared.stage_reviewed(
        pid, sid, action, "Reviewer", "Independent local implementation"
    )
    p = db.project(pid)
    assert prepared.load(pid, p["steps"][0], p) == action
    proof = prepared.provenance(pid, sid, action)
    assert proof["type"] == "developer_reviewed"
    assert proof["request_id"] == ""
    assert (
        db.one("SELECT response FROM cloud_requests WHERE id=?", (rid,))["response"]
        == action.model_dump_json()
    )
    prepared.consume(pid, sid, action)
    assert prepared.load(pid, p["steps"][0], p) is None


def test_cloud_code_is_used_once_without_another_generation(project, plan, monkeypatch):
    pid, sid, rid, action = completed_cloud(project, plan)
    prepared.stage(pid, sid, rid)

    def forbidden(*args, **kwargs):
        pytest.fail("Staged code must not trigger another model call")

    monkeypatch.setattr(engine.OllamaProvider, "generate", forbidden)
    monkeypatch.setattr(cloud.httpx, "Client", forbidden)
    p = db.project(pid)
    step = p["steps"][0]
    ready = engine.prepare_action(pid, step, "")
    assert ready == action
    engine.write_action(pid, sid, ready)
    assert prepared.load(pid, step, db.project(pid)) is None
    rows = db.query("SELECT provenance FROM artifacts WHERE project_id=?", (pid,))
    assert all(json.loads(r["provenance"])["request_id"] == rid for r in rows)


def test_incomplete_cloud_reply_never_becomes_executable(project, plan):
    pid, sid, rid, _ = completed_cloud(project, plan, complete=False)
    with pytest.raises(ValueError, match="не завершена"):
        prepared.stage(pid, sid, rid)
    assert not prepared.path_for(pid, sid).exists()


def test_prepared_code_is_invalid_after_source_revision_changes(project, plan):
    pid, sid, rid, _ = completed_cloud(project, plan)
    prepared.stage(pid, sid, rid)
    db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (pid,))
    p = db.project(pid)
    with pytest.raises(ValueError, match="застарів"):
        prepared.load(pid, p["steps"][0], p)


def test_unapproved_plan_cannot_apply_cloud_code(project, plan):
    pid, sid, rid, _ = completed_cloud(project, plan)
    db.execute("UPDATE projects SET approved_hash=NULL WHERE id=?", (pid,))
    with pytest.raises(ValueError, match="погодьте"):
        prepared.stage(pid, sid, rid)


def test_invalid_static_code_rejected_without_workspace_writes(project, plan):
    pid, sid, rid, action = completed_cloud(project, plan)
    action.files[0].content = "x = missing_variable"
    db.execute(
        "UPDATE cloud_requests SET response=? WHERE id=?",
        (action.model_dump_json(), rid),
    )
    with pytest.raises(ValueError, match="Undefined name"):
        prepared.stage(pid, sid, rid)
    assert not prepared.path_for(pid, sid).exists()


def test_prepared_reply_cannot_overwrite_inputs(project, plan):
    pid, sid, rid, action = completed_cloud(project, plan)
    action.files[0].path = "inputs/main.py"
    db.execute(
        "UPDATE cloud_requests SET response=? WHERE id=?",
        (action.model_dump_json(), rid),
    )
    with pytest.raises(ValueError, match="inputs"):
        prepared.stage(pid, sid, rid)


def test_available_cloud_code_list_excludes_partial_results(client, project, plan):
    pid, sid, rid, _ = completed_cloud(project, plan)
    assert client.get(f"/api/projects/{pid}/cloud/results").json()[0]["id"] == rid
    db.execute(
        "UPDATE jobs SET result=? WHERE kind='cloud'",
        (json.dumps({"complete": False}),),
    )
    assert client.get(f"/api/projects/{pid}/cloud/results").json() == []


def test_old_cloud_reply_cannot_be_applied_after_plan_revision_changes(project, plan):
    pid, sid, rid, _ = completed_cloud(project, plan)
    db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (pid,))
    with pytest.raises(ValueError, match="змінилися"):
        prepared.stage(pid, sid, rid)
    assert not prepared.path_for(pid, sid).exists()


def test_checker_returned_in_files_is_normalized_without_inventing_code():
    action = Action(
        files=[
            {"path": "main.py", "content": "print(4)"},
            {"path": "check_result.py", "content": "assert 2+2==4"},
        ],
        entrypoint="main.py",
        explanation="",
    )
    parsed = prepared.parse_response(action.model_dump_json())
    assert [f.path for f in parsed.files] == ["main.py"]
    assert parsed.checks[0].content == "assert 2+2==4"


def test_local_amendment_preserves_original_cloud_response(project, plan):
    pid, sid, rid, action = completed_cloud(project, plan)
    original = db.one("SELECT response FROM cloud_requests WHERE id=?", (rid,))[
        "response"
    ]
    prepared.stage(pid, sid, rid)
    action.files[0].content += "\nprint('locally reviewed')"
    prepared.amend(pid, sid, action, "Local compatibility fix")
    assert (
        db.one("SELECT response FROM cloud_requests WHERE id=?", (rid,))["response"]
        == original
    )
    assert prepared.provenance(pid, sid, action)["local_edits"] == [
        "Local compatibility fix"
    ]
