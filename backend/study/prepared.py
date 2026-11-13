"""Stage a completed, separately approved cloud answer for local execution."""

import ast
import hashlib
import json
from pathlib import Path
from pydantic import Field
from . import config, db
from .schemas import Action, PythonAction, StrictModel
from .codegen import checkpoint_write, save_candidate, validate_source
from .runtime import RUNTIMES


class PreparedAction(StrictModel):
    project_id: str
    step_id: str
    revision: int
    approved_hash: str
    request_id: str
    provider: str
    model: str
    response_sha256: str
    action: Action
    consumed: bool = False
    local_edits: list[str] = Field(default_factory=list)


def path_for(pid, sid):
    return config.safe_path(config.project_dir(pid), f"codegen/{sid}/prepared.json")


def parse_response(response):
    response = response.strip()
    if response.startswith("```") and response.endswith("```"):
        response = response.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    from .codegen import normalize_checks

    return normalize_checks(Action.model_validate_json(response))


def validate(action, spec, workspace, *, contract_version=0):
    if contract_version == 1:
        from .artifact_contracts import check_action, note_unpublished
        # workspace is always <project_dir>/workspace; the project id is its parent folder name.
        note_unpublished(Path(workspace).parent.name, spec, check_action(spec, action))
    if spec["kind"] != "code" or action.requires_user:
        raise ValueError(
            "Підготовлений код застосовується тільки до автоматичного кроку code"
        )
    if action.entrypoint != RUNTIMES[spec["language"]][1]:
        raise ValueError("Некоректний основний файл програми")
    paths = []
    for file in action.files + action.checks:
        path = config.safe_path(workspace, file.path)
        rel = path.relative_to(workspace.resolve())
        if rel.parts[0].casefold() == "inputs" or any(
            part.startswith(".") for part in rel.parts
        ):
            raise ValueError(
                "Підготовлений код не може змінювати inputs або приховані файли"
            )
        paths.append(str(path).casefold())
    if len(paths) != len(set(paths)) or not any(
        f.path == action.entrypoint for f in action.files
    ):
        raise ValueError("Потрібні унікальні файли та основна програма")
    for name in action.expected_files:
        path = config.safe_path(workspace, name)
        if str(path).casefold() in paths:
            raise ValueError('expected_files має містити лише вихідні дані, не реалізацію чи перевірки')
        if path.relative_to(workspace.resolve()).parts[0].casefold() == "inputs":
            raise ValueError("inputs не є вихідним результатом")
    from .execution_protocol import CHECKER_PROFILES
    if spec["language"] in CHECKER_PROFILES:
        if spec['language'] == 'python':
            PythonAction.model_validate(action.model_dump())
        elif not action.checks:
            raise ValueError('Потрібен незалежний Python checker для цього producer profile')
        for file in action.files + action.checks:
            if Path(file.path).suffix == ".py":
                tree = validate_source(file.path, file.content)
                if file in action.checks and not any(
                    isinstance(n, ast.Assert) for n in ast.walk(tree)
                ):
                    raise ValueError("Незалежна перевірка повинна містити assert")
            elif file in action.checks:
                raise ValueError("Незалежні перевірки Python мають бути .py")
    return action


def stage(pid, sid, rid):
    from .engine import plan_hash

    project = db.project(pid)
    if not project["approved_hash"] or project["approved_hash"] != plan_hash(
        project["plan"]
    ):
        raise ValueError("Спочатку погодьте актуальний план")
    step = next((s for s in project["steps"] if s["id"] == sid), None)
    if not step or step["status"] not in ("pending", "failed", "blocked"):
        raise ValueError("Крок не очікує підготовленого коду")
    request = db.one(
        "SELECT * FROM cloud_requests WHERE id=? AND project_id=?", (rid, pid)
    )
    jobs = db.query(
        "SELECT result FROM jobs WHERE kind='cloud' AND status='done' AND payload=? ORDER BY created DESC LIMIT 1",
        (json.dumps({"id": rid}, ensure_ascii=False),),
    )
    if (
        request["state"] != "done"
        or not jobs
        or not json.loads(jobs[0]["result"]).get("complete")
    ):
        raise ValueError("Хмарна відповідь не завершена або запит не виконаний")
    action = parse_response(request["response"])
    validate(action, step["spec"], config.project_dir(pid) / "workspace", contract_version=project["plan"].get("artifact_contract_version",0))
    settings = json.loads(request["config"])
    if (
        settings.get("project_revision") != project["revision"]
        or settings.get("approved_plan_hash") != project["approved_hash"]
    ):
        raise ValueError("План або джерела змінилися після підготовки хмарного запиту")
    saved = PreparedAction(
        project_id=pid,
        step_id=sid,
        revision=project["revision"],
        approved_hash=project["approved_hash"],
        request_id=rid,
        provider=settings.get("cloud_provider", "gemini"),
        model=request["model"],
        response_sha256=hashlib.sha256(request["response"].encode()).hexdigest(),
        action=action,
    )
    path = path_for(pid, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_write(path, saved)
    save_candidate(
        pid,
        sid,
        project["revision"],
        "cloud/" + saved.provider + "/" + saved.model,
        action,
    )
    db.execute("UPDATE steps SET status='pending',attempts=0 WHERE id=?", (sid,))
    db.execute("UPDATE projects SET status='paused' WHERE id=?", (pid,))
    db.event(
        pid,
        "cloud_action_staged",
        {"request_id": rid, "step_id": sid, "files": [f.path for f in action.files]},
    )
    return {
        "message": "Код підготовлено. Локальний запуск — через Виконати / продовжити.",
        "request_id": rid,
    }


def stage_reviewed(pid, sid, action, author, reason):
    """Internal developer/import workflow; never relabel an incomplete cloud reply."""
    from .engine import plan_hash

    project = db.project(pid)
    step = next(s for s in project["steps"] if s["id"] == sid)
    if project["approved_hash"] != plan_hash(project["plan"]):
        raise ValueError("Потрібен погоджений актуальний план")
    if step["status"] not in ("pending", "failed", "blocked"):
        raise ValueError("Крок не очікує підготовленого коду")
    if not author.strip() or not reason.strip():
        raise ValueError("Потрібен автор та опис перевірки")
    validate(action, step["spec"], config.project_dir(pid) / "workspace", contract_version=project["plan"].get("artifact_contract_version",0))
    saved = PreparedAction(
        project_id=pid,
        step_id=sid,
        revision=project["revision"],
        approved_hash=project["approved_hash"],
        request_id="",
        provider="local_review",
        model=author,
        response_sha256=hashlib.sha256(action.model_dump_json().encode()).hexdigest(),
        action=action,
        local_edits=[reason],
    )
    path = path_for(pid, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_write(path, saved)
    db.event(
        pid,
        "reviewed_action_staged",
        {"step_id": sid, "author": author, "reason": reason},
    )


def load(pid, step, project):
    path = path_for(pid, step["id"])
    if not path.exists():
        return None
    saved = PreparedAction.model_validate_json(path.read_text("utf-8"))
    if saved.consumed:
        return None
    if (
        saved.project_id != pid
        or saved.step_id != step["id"]
        or saved.revision != project["revision"]
        or saved.approved_hash != project["approved_hash"]
    ):
        raise ValueError("Підготовлений код застарів після зміни плану або даних")
    return validate(saved.action, step["spec"], config.project_dir(pid) / "workspace", contract_version=project["plan"].get("artifact_contract_version",0))


def amend(pid, sid, action, reason):
    """Retain the original cloud reply and explicitly record a reviewed local fix."""
    path = path_for(pid, sid)
    saved = PreparedAction.model_validate_json(path.read_text("utf-8"))
    project = db.project(pid)
    step = next(s for s in project["steps"] if s["id"] == sid)
    if (
        saved.consumed
        or saved.revision != project["revision"]
        or saved.approved_hash != project["approved_hash"]
    ):
        raise ValueError("Підготовлений код уже використаний або застарів")
    validate(action, step["spec"], config.project_dir(pid) / "workspace", contract_version=project["plan"].get("artifact_contract_version",0))
    saved.action = action
    saved.local_edits.append(reason)
    checkpoint_write(path, saved)
    save_candidate(
        pid, sid, saved.revision, "cloud/" + saved.provider + "/" + saved.model, action
    )
    db.event(
        pid,
        "cloud_action_amended",
        {"request_id": saved.request_id, "step_id": sid, "reason": reason},
    )


def consume(pid, sid, action):
    path = path_for(pid, sid)
    if path.exists():
        saved = PreparedAction.model_validate_json(path.read_text("utf-8"))
        if not saved.consumed and saved.action.model_dump() == action.model_dump():
            saved.consumed = True
            checkpoint_write(path, saved)
            db.event(
                pid,
                "reviewed_action_used"
                if saved.provider == "local_review"
                else "cloud_action_used",
                {"request_id": saved.request_id, "step_id": sid},
            )


def provenance(pid, sid, action):
    path = path_for(pid, sid)
    if path.exists():
        saved = PreparedAction.model_validate_json(path.read_text("utf-8"))
        if saved.action.model_dump() == action.model_dump():
            return {
                "type": "developer_reviewed"
                if saved.provider == "local_review"
                else "cloud_generated",
                "request_id": saved.request_id,
                "provider": saved.provider,
                "model": saved.model,
                "response_sha256": saved.response_sha256,
                "local_edits": saved.local_edits,
            }
    return {"type": "generated"}
