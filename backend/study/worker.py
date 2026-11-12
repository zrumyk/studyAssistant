"""Single durable worker; interrupted jobs require explicit resume."""

import json
import socket
import time
from . import db


def recover():
    import subprocess
    from .runtime import owner_label

    try:
        r = subprocess.run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=study-assistant.owner=" + owner_label(),
            ],
            capture_output=True,
            timeout=5,
            encoding="utf-8",
        )
        for cid in r.stdout.split():
            if len(cid) >= 12 and all(ch in "0123456789abcdef" for ch in cid):
                subprocess.run(
                    ["docker", "rm", "-f", cid], capture_output=True, timeout=10
                )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    with db.connect() as c:
        c.execute(
            "UPDATE jobs SET status='interrupted',error='Виконання перервано. Відновіть роботу вручну.' WHERE status='running'"
        )
        # An interrupted attempt has no verified outcome; it must not consume
        # the final repair slot when the user resumes after a process crash.
        c.execute(
            "UPDATE steps SET status='pending',attempts=MAX(0,attempts-1) WHERE status='running'"
        )
        c.execute(
            "UPDATE projects SET status='paused' WHERE status IN ('running','analysis_running') AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.project_id=projects.id AND jobs.status='queued' AND jobs.kind IN ('analyse','execute','reassess','recover_assessment','recover_plan'))"
        )


def claim():
    with db.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY CASE WHEN kind='defense' THEN 1 ELSE 0 END,created LIMIT 1"
        ).fetchone()
        if not row:
            return None
        c.execute(
            "UPDATE jobs SET status='running',updated=? WHERE id=?",
            (db.now(), row["id"]),
        )
        return dict(row)


def dispatch(job):
    from . import engine, documents, search, reports, cloud

    p = json.loads(job["payload"])
    pid = job["project_id"]
    kind = job["kind"]
    if kind == "analyse":
        return engine.analyse(pid)
    if kind == 'recover_plan':
        from .planning_recovery import execute
        return execute(pid,p['recovery_id'])
    if kind == "execute":
        return engine.execute_project(pid)
    if kind == 'prepare':
        from .preparation import prepare
        return prepare(pid,p['step_id'],p['revision'],p['approved_hash'],p.get('failure'))
    if kind == 'reassess':
        return engine.reassess_result(pid,p['step_id'])
    if kind == 'recover_assessment':
        from .assessment_recovery import execute
        return execute(pid, p['step_id'], p['recovery_id'])
    if kind == "chat":
        return engine.chat(pid, p["text"])
    if kind == "manual":
        return engine.manual_result(
            pid, p["step_id"], p["text"], p.get("confirmed", False)
        )
    if kind == "import":
        ids = documents.import_folder(
            p["path"], p.get("role", "theory"), p.get("subject", ""), pid
        )
        db.event(pid, "import", {"count": len(ids)})
        return ids
    if kind == "index":
        return search.index_embeddings(pid)
    if kind == "refresh_library":
        return documents.refresh_library()
    if kind == "reparse":
        return documents.reparse(p["id"])
    if kind == "report":
        return reports.build_report(pid)
    if kind == "retry_pdf":
        from .report_pdf import retry_pdf
        return retry_pdf(pid, p['artifact_id'])
    if kind == "defense":
        from .defense import build
        return build(pid)
    if kind == "semester_import":
        from .semester import import_semester
        return import_semester()
    if kind == "cloud":
        return cloud.send(p["id"])
    if kind == "pull":
        import httpx
        from .config import settings

        s = settings()
        if p["model"] not in (s["model"], s["embedding_model"], s.get("coding_model")):
            raise ValueError("Невідома модель")
        with httpx.Client(timeout=None, trust_env=False) as c:
            with c.stream(
                "POST",
                s["ollama_url"] + "/api/pull",
                json={"name": p["model"], "stream": True},
            ) as r:
                r.raise_for_status()
                last = 0
                completed = False
                for line in r.iter_lines():
                    if not line:
                        continue
                    progress = json.loads(line)
                    if progress.get("error"):
                        raise RuntimeError("Ollama: " + progress["error"])
                    if progress.get("status") == "success":
                        completed = True
                    if time.monotonic() - last > 2 or completed:
                        db.event(None, "pull", progress)
                        last = time.monotonic()
                if not completed:
                    raise RuntimeError(
                        "Завантаження моделі перервано до підтвердження завершення"
                    )
        return "Модель завантажено"
    raise ValueError("Невідомий тип завдання")


def main():
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 18574))
    except OSError:
        return
    db.init()
    recover()
    while True:
        job = claim()
        if not job:
            time.sleep(0.5)
            continue
        from .providers import model_project

        model_token = model_project.set(job["project_id"])
        try:
            result = dispatch(job)
            db.execute(
                "UPDATE jobs SET status='done',result=?,updated=? WHERE id=?",
                (
                    json.dumps(result, ensure_ascii=False, default=str),
                    db.now(),
                    job["id"],
                ),
            )
        except Exception as e:
            error = str(e)[:3000]
            db.execute(
                "UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",
                (error, db.now(), job["id"]),
            )
            if job["project_id"]:
                if job["kind"] in ("analyse", "execute"):
                    db.execute(
                        "UPDATE projects SET status='error' WHERE id=?",
                        (job["project_id"],),
                    )
                if job['kind'] == 'recover_plan':
                    db.execute("UPDATE projects SET status='error' WHERE id=? AND status='analysis_running'",(job['project_id'],))
                db.message(
                    job["project_id"], "assistant", "Не вдалося завершити дію: " + error
                )
            db.event(job["project_id"], "error", {"message": error, "job": job["id"]})
        finally:
            model_project.reset(model_token)


if __name__ == "__main__":
    main()
