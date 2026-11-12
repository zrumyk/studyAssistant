import asyncio
from contextlib import asynccontextmanager, closing
from pathlib import Path
import json
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import (
    JSONResponse,
    FileResponse,
    StreamingResponse,
    HTMLResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from . import config, db, engine, cloud
from .schemas import ProjectCreate, ImportRequest, Plan

TOKEN = secrets.token_urlsafe(32)


@asynccontextmanager
async def lifespan(app):
    db.init()
    yield


app = FastAPI(
    title="Навчальний помічник",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)


@app.middleware("http")
async def guard(request, call_next):
    if request.url.path.startswith("/api"):
        origin = request.headers.get("origin")
        if origin and origin not in (
            "http://127.0.0.1:18573",
            "http://localhost:18573",
            "http://testserver",
        ):
            return JSONResponse({"detail": "Недозволене джерело запиту"}, 403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Сторонні запити заборонені"}, 403)
        if request.url.path != "/api/session" and not secrets.compare_digest(
            request.cookies.get("study_session", ""), TOKEN
        ):
            return JSONResponse(
                {"detail": "Відкрийте застосунок у цьому браузері"}, 401
            )
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get("x-study-token") != TOKEN
        ):
            return JSONResponse({"detail": "Відсутній токен дії"}, 403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"
    )
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, 400)


@app.get("/api/session")
def session():
    r = JSONResponse({"token": TOKEN})
    r.set_cookie("study_session", TOKEN, httponly=True, samesite="strict")
    return r


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def documentation():
    import html

    rows = "".join(
        f"<tr><td>{html.escape(', '.join(sorted(getattr(route, 'methods', []) or [])))}</td><td>{html.escape(route.path)}</td></tr>"
        for route in app.routes
        if route.path.startswith("/api")
    )
    return (
        '<!doctype html><html lang="uk"><meta charset="utf-8"><title>API Майстерні</title><style>body{font:15px system-ui;max-width:950px;margin:40px auto;padding:20px}td{padding:9px;border-bottom:1px solid #ddd}code{background:#eee}</style><h1>API Майстерні навчання</h1><p>Відкрийте застосунок для отримання локальної сесії. Зміни потребують заголовка <code>x-study-token</code> із /api/session.</p><p><a href="/openapi.json">Повна OpenAPI-схема з типами запитів і відповідей</a></p><table>'
        + rows
        + "</table></html>"
    )


def assert_idle(pid):
    db.one("SELECT id FROM projects WHERE id=?", (pid,))
    if db.query(
        "SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')",
        (pid,),
    ):
        raise ValueError("Зачекайте завершення поточної дії або призупиніть виконання")


@app.get("/api/health")
def health():
    from .documents import binary
    from .providers import OllamaProvider
    from .runtime import docker_available
    import psutil

    result = {
        "ollama": OllamaProvider().health(),
        "docker": docker_available(),
        "tools": {k: binary(k) for k in ("soffice", "poppler", "tesseract")},
        "ram": {
            "total_gb": round(psutil.virtual_memory().total / 1024**3, 1),
            "available_gb": round(psutil.virtual_memory().available / 1024**3, 1),
        },
        "data_dir": str(config.DATA),
        "disks": {},
        "python": __import__("sys").version.split()[0],
    }
    for drive in ("C:/", "D:/"):
        if Path(drive).exists():
            result["disks"][drive] = round(shutil.disk_usage(drive).free / 1024**3, 2)
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader",
            ],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
        )
        result["gpu"] = r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        result["gpu"] = "Недоступно"
    return result


@app.get("/api/settings")
def settings():
    s = config.settings()
    try:
        s["has_cloud_key"] = bool(cloud.key())
    except Exception:
        s["has_cloud_key"] = False
    with db.connect() as c:
        s["spent_usd"] = cloud.spent(c, cloud.month())
    return s


@app.put("/api/settings")
def update_settings(values: dict):
    key = values.pop("cloud_api_key", None)
    provider = values.get("cloud_provider", config.settings()["cloud_provider"])
    if key is not None and provider == "deepseek":
        raise ValueError("Ключ DeepSeek змінюється тільки в .env: DEEPSEEK_API_KEY")
    if key is not None:
        import keyring

        if key:
            keyring.set_password(cloud.SERVICE, "api-key", key)
        elif cloud.key():
            keyring.delete_password(cloud.SERVICE, "api-key")
    return config.save_settings(values)


@app.post("/api/models/pull")
def pull_model(payload: dict):
    if Path("C:/").exists() and shutil.disk_usage("C:/").free < 10 * 1024**3:
        raise ValueError(
            "Перед завантаженням моделей звільніть на C: щонайменше 10 ГБ. Моделі зберігатимуться на D:."
        )
    return {"job_id": db.enqueue(None, "pull", payload)}


@app.get("/api/projects")
def projects():
    return db.query(
        "SELECT id,title,subject,variant,status,updated FROM projects ORDER BY updated DESC"
    )


@app.post("/api/projects")
def create_project(p: ProjectCreate):
    pid = db.uid()
    db.execute(
        "INSERT INTO projects(id,title,subject,task,variant,status,created,updated) VALUES(?,?,?,?,?,?,?,?)",
        (pid, p.title, p.subject, p.task, p.variant, "analysis", db.now(), db.now()),
    )
    config.project_dir(pid)
    return db.project(pid)


@app.get("/api/projects/{pid}")
def project(pid: str):
    from .assessment_recovery import availability
    result = db.project(pid)
    for step in result['steps']:
        if step['status'] == 'waiting_user' and step['spec']['kind'] == 'code':
            step['result']['assessment_recovery'] = availability(result, step)
    return result


@app.patch("/api/projects/{pid}")
def update_project(pid: str, p: ProjectCreate):
    assert_idle(pid)
    db.execute(
        "UPDATE projects SET title=?,subject=?,task=?,variant=?,approved_hash=NULL,revision=revision+1,status='analysis',updated=? WHERE id=?",
        (p.title, p.subject, p.task, p.variant, db.now(), pid),
    )
    db.execute("UPDATE artifacts SET state='stale' WHERE project_id=?", (pid,))
    return db.project(pid)


@app.post("/api/projects/{pid}/analyse")
def analyse(pid: str):
    assert_idle(pid)
    db.execute("UPDATE projects SET status='analysis_running' WHERE id=?", (pid,))
    return {"job_id": db.enqueue(pid, "analyse")}


class PlanningRecoveryRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    source_job_id: str = Field(pattern='^[a-f0-9]{32}$')
    checkpoint_sha256: str = Field(pattern='^[a-f0-9]{64}$')


@app.post('/api/projects/{pid}/recover-planning')
def recover_planning(pid: str, payload: PlanningRecoveryRequest):
    from .planning_recovery import enqueue
    return enqueue(pid, payload.source_job_id, payload.checkpoint_sha256)


class SourceSelection(BaseModel):
    document_ids: list[str] | None = Field(default=None, max_length=500)


@app.get("/api/projects/{pid}/sources")
def project_sources(pid: str):
    p = db.project(pid)
    available = db.query("SELECT id,name,role FROM documents WHERE project_id IS NULL AND subject=? AND role!='result' AND status!='superseded' ORDER BY role,name", (p['subject'],))
    selected = db.query('SELECT document_ids FROM source_selections WHERE project_id=?', (pid,))
    return {'available':available, 'document_ids':json.loads(selected[0]['document_ids']) if selected else None}


@app.put("/api/projects/{pid}/sources")
def select_project_sources(pid: str, payload: SourceSelection):
    assert_idle(pid)
    p = db.project(pid)
    ids = sorted(set(payload.document_ids)) if payload.document_ids is not None else None
    for did in ids or []:
        if not db.query("SELECT id FROM documents WHERE id=? AND project_id IS NULL AND subject=? AND role!='result' AND status!='superseded'", (did,p['subject'])):
            raise ValueError('Матеріал не належить бібліотеці цього предмета')
    with db.connect() as c:
        c.execute('DELETE FROM source_selections WHERE project_id=?', (pid,))
        if ids is not None:
            c.execute('INSERT INTO source_selections VALUES(?,?)', (pid,json.dumps(ids)))
        c.execute("UPDATE projects SET approved_hash=NULL,status='analysis',revision=revision+1,updated=? WHERE id=?", (db.now(),pid))
        c.execute('DELETE FROM source_pins WHERE project_id=?', (pid,))
        c.execute("UPDATE artifacts SET state='stale' WHERE project_id=?", (pid,))
    db.event(pid,'source_selection',{'document_ids':ids})
    return project_sources(pid)


@app.put("/api/projects/{pid}/plan")
def update_plan(pid: str, plan: Plan):
    assert_idle(pid)
    engine.save_plan(pid, plan.model_dump())
    return db.project(pid)


@app.post("/api/projects/{pid}/approve")
def approve(pid: str):
    assert_idle(pid)
    engine.approve(pid)
    return db.project(pid)


@app.post("/api/projects/{pid}/execute")
def execute_project(pid: str):
    assert_idle(pid)
    p = db.project(pid)
    if p["approved_hash"] != engine.plan_hash(p["plan"]):
        raise ValueError("Погодьте актуальний план перед запуском")
    return {"job_id": db.enqueue(pid, "execute")}


@app.post("/api/projects/{pid}/pause")
def pause(pid: str):
    db.execute("UPDATE projects SET status='paused' WHERE id=?", (pid,))
    db.execute(
        "UPDATE jobs SET status='cancelled' WHERE project_id=? AND status='queued' AND kind IN ('execute','prepare','recover_plan')",
        (pid,),
    )
    db.event(pid, "paused", {})
    return {"status": "paused"}


@app.post("/api/projects/{pid}/steps/{sid}/manual")
def manual(pid: str, sid: str, payload: dict):
    assert_idle(pid)
    return {
        "job_id": db.enqueue(
            pid,
            "manual",
            {
                "step_id": sid,
                "text": str(payload.get("text", "")),
                "confirmed": bool(payload.get("confirmed", False)),
            },
        )
    }


@app.post('/api/projects/{pid}/steps/{sid}/reassess')
def reassess_step(pid:str,sid:str):
    assert_idle(pid)
    p=db.project(pid)
    if not p['approved_hash'] or p['approved_hash']!=engine.plan_hash(p['plan']):
        raise ValueError('Спочатку погодьте актуальний план')
    step=next((s for s in p['steps'] if s['id']==sid),None)
    if not step or step['status']!='waiting_user' or step['spec']['kind']!='code' or step['result'].get('exit_code')!=0 or step['result'].get('review_only_attempts',0)>=1:
        raise ValueError('Немає збереженого успішного запуску, що очікує повторної перевірки')
    db.execute("UPDATE projects SET status='running' WHERE id=?",(pid,))
    return {'job_id':db.enqueue(pid,'reassess',{'step_id':sid})}


@app.post('/api/projects/{pid}/steps/{sid}/recover-assessment')
def recover_assessment(pid: str, sid: str):
    assert_idle(pid)
    from .assessment_recovery import enqueue
    return enqueue(pid, sid)


@app.post("/api/projects/{pid}/steps/{sid}/reconcile-source-artifacts")
def reconcile_source_artifacts(pid: str, sid: str):
    assert_idle(pid)
    from .source_artifacts import reconcile
    return reconcile(pid, sid)


@app.post("/api/projects/{pid}/steps/{sid}/retry")
def retry_step(pid: str, sid: str):
    assert_idle(pid)
    step = db.one("SELECT status FROM steps WHERE id=? AND project_id=?", (sid, pid))
    if step["status"] not in ("failed", "blocked"):
        raise ValueError("Повтор доступний тільки для зупиненого кроку")
    db.execute("UPDATE steps SET attempts=0,status='pending' WHERE id=?", (sid,))
    db.execute("UPDATE projects SET status='paused' WHERE id=?", (pid,))
    return {"message": "Крок підготовлено до повтору. Натисніть Виконати / продовжити."}


@app.post('/api/projects/{pid}/steps/{sid}/prepare')
def prepare_step(pid:str,sid:str,payload:dict|None=None):
    assert_idle(pid)
    from .preparation import eligible,checked_failure
    project,_=eligible(pid,sid)
    checked_failure(pid,sid,payload)
    return {'job_id':db.enqueue(pid,'prepare',{'step_id':sid,'revision':project['revision'],'approved_hash':project['approved_hash'],'failure':payload})}


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


@app.post("/api/projects/{pid}/chat")
def chat(pid: str, payload: TextRequest):
    assert_idle(pid)
    db.message(pid, "user", payload.text)
    return {"job_id": db.enqueue(pid, "chat", {"text": payload.text})}


@app.post("/api/import")
def import_material(payload: ImportRequest):
    if payload.project_id:
        assert_idle(payload.project_id)
    return {"job_id": db.enqueue(payload.project_id, "import", payload.model_dump())}


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    project_id: str = Form(""),
    subject: str = Form(""),
    role: str = Form("theory"),
):
    from .documents import SUPPORTED, ROLES

    if role not in ROLES:
        raise ValueError("Непідтримуваний тип матеріалу")
    if project_id:
        assert_idle(project_id)
    name = Path(file.filename or "file").name
    if Path(name).suffix.lower() not in SUPPORTED:
        raise ValueError("Непідтримуваний формат")
    folder = config.DATA / "inbox" / db.uid()
    folder.mkdir(parents=True)
    dest = config.safe_path(folder, name)
    size = 0
    try:
        with dest.open("wb") as f:
            while block := await file.read(1024 * 1024):
                size += len(block)
                if size > 100_000_000:
                    raise ValueError("Ліміт файлу — 100 МБ")
                f.write(block)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return {
        "job_id": db.enqueue(
            project_id or None,
            "import",
            {"path": str(dest), "role": role, "subject": subject},
        )
    }


@app.get("/api/documents")
def documents(pid: str | None = None):
    if pid:
        p = db.project(pid)
        return db.query(
            "SELECT id,name,role,subject,status,warnings,origin,created,(SELECT count(*) FROM chunks WHERE document_id=d.id AND embedding IS NOT NULL) as indexed FROM documents d WHERE project_id=? OR (project_id IS NULL AND subject=?) ORDER BY created DESC",
            (pid, p["subject"]),
        )
    return db.query(
        "SELECT id,name,role,subject,status,warnings,origin,created,(SELECT count(*) FROM chunks WHERE document_id=d.id AND embedding IS NOT NULL) as indexed FROM documents d ORDER BY created DESC"
    )


@app.post("/api/library/refresh")
def refresh_library():
    if db.query("SELECT id FROM jobs WHERE status IN ('running','queued')"):
        raise ValueError(
            "Дочекайтеся завершення поточної черги перед оновленням бібліотеки"
        )
    return {"job_id": db.enqueue(None, "refresh_library")}


@app.get("/api/documents/{did}/download")
def document_download(did: str):
    d = db.one("SELECT path,name FROM documents WHERE id=?", (did,))
    path = Path(d["path"]).resolve()
    if not path.is_relative_to(config.DATA / "documents"):
        raise ValueError("Некоректний шлях")
    return FileResponse(path, filename=d["name"])


@app.post("/api/documents/{did}/reparse")
def reparse_document(did: str):
    d = db.one("SELECT project_id FROM documents WHERE id=?", (did,))
    if d["project_id"]:
        assert_idle(d["project_id"])
    return {"job_id": db.enqueue(d["project_id"], "reparse", {"id": did})}


@app.get("/api/documents/{did}/pages/{page}")
def document_page(did: str, page: int):
    d = db.one("SELECT path FROM documents WHERE id=?", (did,))
    path = Path(d["path"])
    if path.suffix.lower() != ".pdf" or page < 1:
        raise ValueError("Потрібен PDF і правильний номер сторінки")
    from .documents import render_pdf

    out = path.parent / f"page-{page}.png"
    if not out.exists():
        render_pdf(path, page, out)
    return FileResponse(out, media_type="image/png")


@app.post("/api/projects/{pid}/index")
def index(pid: str):
    assert_idle(pid)
    return {"job_id": db.enqueue(pid, "index")}


@app.get("/api/projects/{pid}/search")
def search(pid: str, q: str):
    from .search import search

    heavy = bool(db.query("SELECT id FROM jobs WHERE status='running'"))
    return search(pid, q, semantic=not heavy)


@app.post("/api/projects/{pid}/report")
def report(pid: str):
    assert_idle(pid)
    return {"job_id": db.enqueue(pid, "report")}


@app.post("/api/projects/{pid}/defense")
def defense(pid: str):
    assert_idle(pid)
    from .defense import enqueue
    return {"job_id": enqueue(pid)}


@app.get("/api/semester")
def semester_preview():
    from .semester import preview
    return preview()


@app.post("/api/semester/import")
def semester_import():
    from .semester import preview
    manifest = preview()
    if manifest['unassigned']:
        raise ValueError("Є нерозсортовані файли. Перенесіть їх у папку предмета перед імпортом")
    if not manifest['files']:
        raise ValueError("У папці семестру поки немає матеріалів")
    if db.query("SELECT id FROM jobs WHERE status IN ('running','queued')"):
        raise ValueError("Дочекайтеся завершення поточної черги")
    return {"job_id": db.enqueue(None, "semester_import")}


@app.get("/api/projects/{pid}/artifacts/{aid}")
def download_artifact(pid: str, aid: str):
    a = db.one("SELECT * FROM artifacts WHERE id=? AND project_id=?", (aid, pid))
    root = config.project_dir(pid)
    path = config.safe_path(
        root if a["path"].startswith(("reports", "artifacts")) else root / "workspace",
        a["path"],
    )
    if not path.is_file():
        raise ValueError("Файл відсутній")
    return FileResponse(path, filename=a["name"])


@app.post("/api/projects/{pid}/artifacts/{aid}/pdf")
def retry_artifact_pdf(pid: str, aid: str):
    from .report_pdf import saved_docx

    assert_idle(pid)
    saved_docx(pid, aid)
    return {"job_id": db.enqueue(pid, "retry_pdf", {"artifact_id": aid})}


@app.get("/api/projects/{pid}/export")
def export(pid: str):
    db.project(pid)
    root = config.project_dir(pid)
    folder = config.DATA / "exports"
    folder.mkdir(exist_ok=True)
    out = folder / (pid + "-" + db.uid() + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "project.json", json.dumps(db.project(pid), ensure_ascii=False, indent=2)
        )
        for base in ("workspace", "reports", "artifacts"):
            for path in (root / base).rglob("*"):
                if path.is_file():
                    safe = config.safe_path(root, path.relative_to(root))
                    z.write(safe, str(path.relative_to(root)))
    return FileResponse(out, filename=pid + ".zip")


@app.post("/api/backup")
def backup():
    folder = config.DATA / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / ("backup-" + db.uid() + ".zip")
    import sqlite3

    with tempfile.TemporaryDirectory(dir=config.DATA) as tmp:
        with (
            db.connect() as source,
            closing(sqlite3.connect(Path(tmp) / "study.sqlite3")) as dest,
        ):
            source.backup(dest)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(Path(tmp) / "study.sqlite3", "study.sqlite3")
            for name in ("projects", "documents"):
                for p in (config.DATA / name).rglob("*"):
                    if p.is_file() and not p.is_symlink():
                        z.write(p, str(p.relative_to(config.DATA)))
            if (config.DATA / "settings.json").exists():
                z.write(config.DATA / "settings.json", "settings.json")
    return {
        "path": str(target),
        "message": "Резервна копія створена; API-ключ не входить до неї",
    }


@app.post("/api/cache/clear")
def clear_cache():
    if db.query("SELECT id FROM jobs WHERE status IN ('queued','running')"):
        raise ValueError("Спочатку дочекайтеся завершення черги")
    cache = (config.ROOT / ".cache").resolve()
    reclaimed = 0
    for name in ("pip", "npm", "uv"):
        target = (cache / name).resolve()
        if not target.is_relative_to(cache) or target == cache:
            raise ValueError("Некоректний шлях кешу")
        if target.is_dir():
            reclaimed += sum(
                p.stat().st_size
                for p in target.rglob("*")
                if p.is_file() and not p.is_symlink()
            )
            shutil.rmtree(target)
    return {
        "reclaimed_mb": round(reclaimed / 1024**2, 1),
        "message": "Очищено тільки кеші завантаження пакетів. Роботи, моделі, джерела та журнали збережені.",
    }


@app.get("/api/jobs")
def jobs():
    return db.query(
        "SELECT id,project_id,kind,status,result,error,updated FROM jobs ORDER BY created DESC LIMIT 30"
    )


@app.get("/api/events")
async def events(request: Request):
    try:
        cursor = int(request.headers.get("last-event-id", "0"))
    except ValueError:
        cursor = 0

    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            rows = db.query(
                "SELECT * FROM events WHERE id>? ORDER BY id LIMIT 100", (cursor,)
            )
            for row in rows:
                cursor = row["id"]
                yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@app.post("/api/projects/{pid}/cloud/quote")
def cloud_quote(pid: str, payload: TextRequest):
    assert_idle(pid)
    p = db.project(pid)
    from .search import context

    prompt = f"Завдання: {p['task']}\nВаріант: {p['variant']}\nЗапит: {payload.text}\nВибрані матеріали:\n{context(pid, payload.text, 3)}"
    return cloud.quote(pid, prompt, payload.text[:200])


@app.post("/api/cloud/{rid}/send")
def cloud_send(rid: str):
    r = db.one("SELECT project_id,state FROM cloud_requests WHERE id=?", (rid,))
    assert_idle(r["project_id"])
    if r["state"] != "quoted":
        raise ValueError("Дозвіл уже використано")
    if db.query(
        "SELECT id FROM jobs WHERE kind='cloud' AND payload=? AND status IN ('queued','running')",
        (json.dumps({"id": rid}, ensure_ascii=False),),
    ):
        raise ValueError("Запит уже в черзі")
    return {"job_id": cloud.approve_and_enqueue(rid)}


@app.post("/api/projects/{pid}/steps/{sid}/cloud/{rid}/apply")
def apply_cloud_code(pid: str, sid: str, rid: str):
    assert_idle(pid)
    from .prepared import stage

    return stage(pid, sid, rid)


@app.post("/api/cloud/{rid}/send-budgeted")
def cloud_send_budgeted(rid: str):
    request = db.one("SELECT project_id FROM cloud_requests WHERE id=?", (rid,))
    assert_idle(request["project_id"])
    return {"job_id":cloud.approve_and_enqueue(rid, standing=True)}


@app.get("/api/projects/{pid}/cloud/results")
def cloud_code_results(pid: str):
    from .prepared import parse_response

    db.project(pid)
    results = []
    for row in db.query(
        "SELECT id,model,purpose,response,config FROM cloud_requests WHERE project_id=? AND state='done' ORDER BY created DESC",
        (pid,),
    ):
        try:
            parse_response(row["response"])
            jobs = db.query(
                "SELECT result FROM jobs WHERE kind='cloud' AND status='done' AND payload=?",
                (json.dumps({"id": row["id"]}, ensure_ascii=False),),
            )
            if any(json.loads(job["result"]).get("complete") for job in jobs):
                results.append(
                    {
                        "id": row["id"],
                        "model": row["model"],
                        "purpose": row["purpose"],
                        "provider": json.loads(row["config"]).get(
                            "cloud_provider", "gemini"
                        ),
                    }
                )
        except (ValueError, TypeError):
            continue
    return results


frontend = config.ROOT / "frontend/dist"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
