import json
from pathlib import Path
import pytest
from study import db, config, engine
from study.schemas import Action, Assessment, PythonProgram


def test_code_action_cannot_skip_execution_by_delegating_later_manual_step():
    action=Action(explanation='Open external program',requires_user=True)
    for kind in ('code','write','fetch'):
        with pytest.raises(ValueError,match='requires_user=false'):
            engine.validate_action_kind({'kind':kind},action)
    engine.validate_action_kind({'kind':'manual'},action)
    engine.validate_action_kind({'kind':'code'},Action(explanation='Run code'))


def approve(client, pid, plan):
    assert client.put(f"/api/projects/{pid}/plan", json=plan).status_code == 200
    assert client.post(f"/api/projects/{pid}/approve").status_code == 200


def test_approval_gate(client, project, plan):
    pid = project["id"]
    assert client.post(f"/api/projects/{pid}/execute").status_code == 400
    plan["missing"] = ["Вкажіть дані"]
    client.put(f"/api/projects/{pid}/plan", json=plan)
    assert client.post(f"/api/projects/{pid}/approve").status_code == 400


def test_generated_action_cannot_overwrite_inputs_or_partially_write(project):
    pid = project["id"]
    workspace = config.project_dir(pid) / "workspace"
    source = workspace / "inputs" / "measurements.csv"
    source.parent.mkdir(parents=True)
    source.write_text("value\n4\n", "utf-8")
    proposal = Action(
        files=[
            {"path": "main.py", "content": "print(99)"},
            {"path": "inputs/measurements.csv", "content": "value\n99\n"},
        ],
        explanation="",
    )
    with pytest.raises(ValueError, match="тільки для читання"):
        engine.write_action(pid, None, proposal)
    assert source.read_text("utf-8") == "value\n4\n"
    assert not (workspace / "main.py").exists()


def test_conflict_requires_resolution(client, project, plan):
    plan["conflicts"] = ["Методичка та зразок різняться"]
    pid = project["id"]
    client.put(f"/api/projects/{pid}/plan", json=plan)
    assert client.post(f"/api/projects/{pid}/approve").status_code == 400
    plan["conflicts"] = []
    approve(client, pid, plan)
    assert client.post(f"/api/projects/{pid}/execute").status_code == 200


def test_changed_plan_invalidates_approval(client, project, plan):
    pid = project["id"]
    approve(client, pid, plan)
    plan["goal"] = "Змінена мета"
    client.put(f"/api/projects/{pid}/plan", json=plan)
    assert client.post(f"/api/projects/{pid}/execute").status_code == 400


def test_repeated_approval_does_not_reset_finished_steps(client, project, plan):
    pid = project["id"]
    approve(client, pid, plan)
    db.execute("UPDATE steps SET status='done',attempts=1 WHERE project_id=?", (pid,))
    assert client.post(f"/api/projects/{pid}/approve").status_code == 200
    assert db.project(pid)["steps"][0]["status"] == "done"
    assert db.project(pid)["steps"][0]["attempts"] == 1


def test_pause_during_generation_preserves_retry_budget(
    client, project, plan, monkeypatch
):
    pid = project["id"]
    approve(client, pid, plan)

    def prepare(*args):
        db.execute("UPDATE projects SET status='paused' WHERE id=?", (pid,))
        return Action(explanation="Призупинено")

    monkeypatch.setattr(engine, "prepare_action", prepare)
    engine.execute_project(pid)
    p = db.project(pid)
    assert p["status"] == "paused"
    assert p["steps"][0]["status"] == "pending"
    assert p["steps"][0]["attempts"] == 0


def test_changed_task_requires_explicit_plan_review(client, project, plan):
    pid = project["id"]
    approve(client, pid, plan)
    changed = {key: project[key] for key in ("title", "subject", "task", "variant")}
    changed["variant"] = "99"
    assert client.patch(f"/api/projects/{pid}", json=changed).status_code == 200
    assert client.post(f"/api/projects/{pid}/approve").status_code == 400
    assert client.put(f"/api/projects/{pid}/plan", json=plan).status_code == 200
    assert client.post(f"/api/projects/{pid}/approve").status_code == 200


def test_successful_execution_and_resume(client, project, plan, monkeypatch):
    pid = project["id"]
    approve(client, pid, plan)
    calls = []

    def generate(self, prompt, schema=None, images=None):
        if schema is PythonProgram:
            return PythonProgram(main_code='print((2+4+6)/3)',checker_code='value=2\nassert value+value==4',expected_files=[],explanation='Розрахунок')
        if issubclass(schema, Action):
            return Action(
                files=[{"path": "main.py", "content": "print((2+4+6)/3)"}],
                entrypoint="main.py",
                expected_files=[],
                explanation="Розрахунок",
                checks=[{"path": "check_result.py", "content": "assert 4 == 4"}],
                manual_instructions="Запустіть Python самостійно",
            )
        assert "results.csv:\nmean\n4" in prompt
        return Assessment(satisfied=True, explanation="Результат 4")

    def run(
        self,
        pid,
        sid,
        language,
        packages,
        timeout,
        cancelled,
        expected_files=None,
        check_paths=None,
    ):
        calls.append(sid)
        (config.project_dir(pid) / "workspace/results.csv").write_text(
            "mean\n4\n", "utf-8"
        )
        return {
            "exit_code": 0,
            "reason": "",
            "oom": False,
            "log": "4",
            "run_id": "test",
            "files": ["results.csv"],
            "produced_files": ["results.csv"],
        }

    monkeypatch.setattr(engine.OllamaProvider, "generate", generate)
    monkeypatch.setattr(engine.OllamaProvider, "unload", lambda self: None)
    monkeypatch.setattr(engine.DockerRuntime, "run", run)
    engine.execute_project(pid)
    p = db.project(pid)
    assert p["status"] == "completed", [s["error"] for s in p["steps"]]
    assert p["steps"][0]["status"] == "done"
    engine.execute_project(pid)
    assert len(calls) == 1  # Completed side effects must not replay.


def test_assessment_failure_preserves_success_without_replay(
    client, project, plan, monkeypatch
):
    pid = project["id"]
    approve(client, pid, plan)
    calls = []
    monkeypatch.setattr(
        engine,
        "prepare_action",
        lambda *args: Action(
            files=[{"path": "main.py", "content": "print(4)"}],
            entrypoint="main.py",
            explanation="",
            checks=[],
        ),
    )
    monkeypatch.setattr(engine.OllamaProvider, "unload", lambda self: None)

    def fail(*args, **kwargs):
        raise RuntimeError("review truncated")

    def run(*args, **kwargs):
        calls.append(1)
        return {
            "exit_code": 0,
            "reason": "",
            "oom": False,
            "log": "4",
            "run_id": "successful",
            "files": [],
            "produced_files": [],
        }

    monkeypatch.setattr(engine.OllamaProvider, "generate", fail)
    monkeypatch.setattr(engine.DockerRuntime, "run", run)
    engine.execute_project(pid)
    step = db.project(pid)["steps"][0]
    assert step["status"] == "waiting_user"
    assert step["result"]["run_id"] == "successful"
    assert step["result"]["assessment_pending"]
    # Even worker recovery/resetting status must not rerun the successful command.
    db.execute("UPDATE steps SET status='pending' WHERE id=?", (step["id"],))
    engine.execute_project(pid)
    assert len(calls) == 1
    assert db.project(pid)["steps"][0]["status"] == "waiting_user"


def test_model_criticism_does_not_reexecute_success(client, project, plan, monkeypatch):
    pid = project["id"]
    approve(client, pid, plan)
    calls = []
    monkeypatch.setattr(
        engine,
        "prepare_action",
        lambda *args: Action(
            files=[{"path": "main.py", "content": "print(4)"}],
            entrypoint="main.py",
            explanation="",
        ),
    )
    monkeypatch.setattr(engine.OllamaProvider, "unload", lambda self: None)
    monkeypatch.setattr(
        engine.OllamaProvider,
        "generate",
        lambda *a, **k: Assessment(
            satisfied=False, needs_user=False, explanation="CPU run has CUDA warnings"
        ),
    )

    def run(*a, **k):
        calls.append(1)
        return {
            "exit_code": 0,
            "reason": "",
            "oom": False,
            "log": "done",
            "run_id": "ok",
            "files": [],
            "produced_files": [],
        }

    monkeypatch.setattr(engine.DockerRuntime, "run", run)
    engine.execute_project(pid)
    step = db.project(pid)["steps"][0]
    assert step["status"] == "waiting_user"
    assert step["result"]["assessment"]["satisfied"] is False
    assert not step["result"].get("assessment_pending")
    engine.execute_project(pid)
    assert len(calls) == 1


def test_failed_code_stops_after_three_attempts(client, project, plan, monkeypatch):
    pid = project["id"]
    approve(client, pid, plan)
    monkeypatch.setattr(
        engine,
        "prepare_action",
        lambda *args: Action(
            files=[{"path": "main.py", "content": "1/0"}],
            entrypoint="main.py",
            explanation="",
        ),
    )
    monkeypatch.setattr(engine.OllamaProvider, "unload", lambda self: None)
    count = []

    def run(*args, **kwargs):
        count.append(1)
        return {
            "exit_code": 1,
            "reason": "",
            "oom": False,
            "log": "error " + str(len(count)),
            "files": [],
            "run_id": "x",
        }

    monkeypatch.setattr(engine.DockerRuntime, "run", run)
    engine.execute_project(pid)
    assert len(count) == 3
    assert db.project(pid)["status"] == "needs_user"


def test_runtime_unavailable_preserves_code(client, project, plan, monkeypatch):
    pid = project["id"]
    approve(client, pid, plan)
    monkeypatch.setattr(
        engine,
        "prepare_action",
        lambda *args: Action(
            files=[{"path": "main.py", "content": "print(4)"}],
            entrypoint="main.py",
            explanation="",
        ),
    )
    monkeypatch.setattr(engine.OllamaProvider, "unload", lambda self: None)

    def fail(*args, **kwargs):
        raise RuntimeError("Docker недоступний")

    monkeypatch.setattr(engine.DockerRuntime, "run", fail)
    engine.execute_project(pid)
    assert (config.project_dir(pid) / "workspace/main.py").exists()
    assert db.project(pid)["steps"][0]["attempts"] == 0


def test_manual_confirmation_is_labelled(client, project, plan):
    pid = project["id"]
    approve(client, pid, plan)
    sid = db.project(pid)["steps"][0]["id"]
    db.execute("UPDATE steps SET status='waiting_user' WHERE id=?", (sid,))
    engine.manual_result(pid, sid, "Перевірив результат 4", True)
    assert "користувачем" in db.project(pid)["steps"][0]["result"]["evidence"]


def test_manual_assessment_uses_end_state_and_images(
    client, project, plan, monkeypatch
):
    from PIL import Image
    from study.documents import import_file

    pid = project["id"]
    approve(client, pid, plan)
    sid = db.project(pid)["steps"][0]["id"]
    db.execute("UPDATE steps SET status='waiting_user' WHERE id=?", (sid,))
    source = config.DATA / "proof.png"
    Image.new("RGB", (20, 20), "white").save(source)
    doc_id = import_file(source, "result", project["subject"], pid, invalidate=False)

    def assess(self, prompt, schema, images=None):
        assert "кінцевий результат" in prompt
        assert "без запису процесу реєстрації" in prompt
        assert "Ніколи не вимагай показувати API-ключі" in prompt
        assert len(images) == 1 and images[0].is_file()
        return Assessment(
            satisfied=True, explanation="Evidence matches the expected end state"
        )

    monkeypatch.setattr(engine.OllamaProvider, "generate", assess)
    engine.manual_result(pid, sid, "Published experiment and screenshot", False)
    step = db.project(pid)["steps"][0]
    assert step["status"] == "done"
    assert doc_id in [v["id"] for v in step["result"]["document_versions"]]
    assert "моделлю" in step["result"]["evidence"]


def test_interrupted_work_requires_resume(client, project, plan):
    from study.worker import recover

    pid = project["id"]
    approve(client, pid, plan)
    jid = db.enqueue(pid, "execute")
    db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
    db.execute("UPDATE projects SET status='running' WHERE id=?", (pid,))
    recover()
    assert (
        db.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "interrupted"
    )
    assert db.project(pid)["status"] == "paused"


def test_crash_restores_unfinished_attempt_only(client, project, plan, monkeypatch):
    from study.worker import recover

    pid = project["id"]
    plan["steps"] = plan["steps"] * 2
    approve(client, pid, plan)
    steps = db.project(pid)["steps"]
    db.execute(
        "UPDATE steps SET status='done',attempts=1 WHERE id=?", (steps[0]["id"],)
    )
    db.execute(
        "UPDATE steps SET status='running',attempts=3 WHERE id=?", (steps[1]["id"],)
    )
    # Recovery must not contact or remove real containers during this unit test.
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **kw: type("Result", (), {"stdout": ""})()
    )
    recover()
    recovered = db.project(pid)["steps"]
    assert (recovered[0]["status"], recovered[0]["attempts"]) == ("done", 1)
    assert (recovered[1]["status"], recovered[1]["attempts"]) == ("pending", 2)


def test_exhausted_step_cannot_be_skipped(client, project, plan, monkeypatch):
    pid = project["id"]
    plan["steps"] = plan["steps"] * 2
    approve(client, pid, plan)
    sid = db.project(pid)["steps"][0]["id"]
    db.execute("UPDATE steps SET status='pending',attempts=3 WHERE id=?", (sid,))

    def unexpected(*args, **kwargs):
        pytest.fail("Execution must not advance beyond an exhausted step")

    monkeypatch.setattr(engine, "prepare_action", unexpected)
    engine.execute_project(pid)
    p = db.project(pid)
    assert p["status"] == "needs_user"
    assert [s["status"] for s in p["steps"]] == ["pending", "pending"]
    assert p["steps"][1]["attempts"] == 0


@pytest.mark.parametrize('same_cause,expected_attempts',[(True,2),(False,3)])
def test_repair_loop_stops_repeated_cause_across_new_runs(client,project,plan,monkeypatch,same_cause,expected_attempts):
    pid=project['id'];approve(client,pid,plan);calls=[]
    def failing(*args,**kwargs):
        number=len(calls)+1;calls.append(number)
        run=str(number)*32
        detail='users.email' if same_cause else 'column_'+str(number)
        raise ValueError(json.dumps({'run_id':run,'exit_code':1,'oom':False,'reason':'',
                                     'log':'IntegrityError: '+detail+' /runs/'+run+'/main.sql'}))
    monkeypatch.setattr(engine,'prepare_action',failing)
    engine.execute_project(pid)
    p=db.project(pid)
    assert len(calls)==expected_attempts
    assert p['status']=='needs_user' and p['steps'][0]['status']=='failed'
    assert p['steps'][0]['attempts']==expected_attempts
    assert json.loads(p['steps'][0]['error'])['run_id']==str(expected_attempts)*32
    event=json.loads(db.query("SELECT data FROM events WHERE project_id=? AND kind='repair_stopped'",(pid,))[0]['data'])
    assert event['repeated'] is same_cause


def test_unverified_citation_rejected(client, project, plan):
    plan["sources"] = [
        {"document_id": "invented", "locator": "Сторінка 1", "excerpt": "вигадане"}
    ]
    assert (
        client.put("/api/projects/" + project["id"] + "/plan", json=plan).status_code
        == 400
    )


def test_stale_artifacts_on_change(client, project, plan):
    pid = project["id"]
    approve(client, pid, plan)
    root = config.project_dir(pid) / "workspace"
    root.mkdir()
    (root / "out.txt").write_text("4")
    engine.artifact(pid, None, "out.txt", "executed")
    client.patch(
        "/api/projects/" + pid,
        json={
            "title": "Нова",
            "subject": "Приклад",
            "task": "Змінена умова",
            "variant": "2",
        },
    )
    assert db.project(pid)["artifacts"][0]["state"] == "stale"


def test_report_refuses_incomplete_work(client, project, plan):
    from study.reports import build_report

    pid = project["id"]
    approve(client, pid, plan)
    with pytest.raises(ValueError):
        build_report(pid)
