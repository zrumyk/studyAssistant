import pytest
from study import config, db
from study.api import app
from fastapi.testclient import TestClient


@pytest.fixture
def patch_reply():
    """Controlled model replies for workflow tests; never production plan edits."""
    import json
    from study.plan_patches import PlanPatch, PlanEdit, draft_hash
    from study.schemas import PlanDraft
    def reply(prompt, path=None, value=None, insert_step=None):
        raw = prompt.split('\nФактичний попередній план: ', 1)[1].split('\nКонтекст ремонту з походженням', 1)[0]
        base = PlanDraft.model_validate_json(raw)
        if insert_step is not None:
            edit = PlanEdit(op='insert_step', path=f'/steps/{len(base.steps)}', before_json='null',
                            after_json=json.dumps(insert_step, ensure_ascii=False))
        else:
            path = path or '/warnings'
            parts = path.split('/')[1:]
            before = base.model_dump()
            for part in parts:
                before = before[int(part)] if isinstance(before, list) else before[part]
            after = value if value is not None else [*base.warnings, 'Controlled repaired warning']
            edit = PlanEdit(op='replace', path=path, before_json=json.dumps(before, ensure_ascii=False),
                            after_json=json.dumps(after, ensure_ascii=False))
        return PlanPatch(base_sha256=draft_hash(base), requires_replan=False, explanation='Controlled fixture repair', edits=[edit])
    return reply


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.sqlite3")
    db.init()
    yield tmp_path


@pytest.fixture
def client():
    with TestClient(app) as c:
        token = c.get("/api/session").json()["token"]
        c.headers["x-study-token"] = token
        yield c


@pytest.fixture
def project(client):
    return client.post(
        "/api/projects",
        json={
            "title": "Робота",
            "task": "Обчислити середнє значення чисел 2,4,6 та зберегти CSV",
            "subject": "Приклад",
            "variant": "",
        },
    ).json()


@pytest.fixture
def plan():
    return {
        "goal": "Обчислити середнє",
        "requirements": ["Правильне середнє дорівнює 4"],
        "sources": [],
        "missing": [],
        "conflicts": [],
        "report_sections": ["Мета", "Результати"],
        "steps": [
            {
                "title": "Розрахунок",
                "instruction": "Обчислити середнє 2,4,6",
                "kind": "code",
                "language": "python",
                "expected": "results.csv містить 4",
                "timeout_seconds": 30,
                "dependencies": [],
            }
        ],
    }
