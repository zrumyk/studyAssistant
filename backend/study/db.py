import sqlite3
import json
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from . import config


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


@contextmanager
def connect():
    c = sqlite3.connect(config.DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects(
          id TEXT PRIMARY KEY, title TEXT, subject TEXT, task TEXT, variant TEXT,
          status TEXT, plan TEXT DEFAULT '{}', approved_hash TEXT, revision INTEGER DEFAULT 0,
          created TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS documents(
          id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), subject TEXT,
          name TEXT, path TEXT, origin TEXT, hash TEXT, role TEXT, status TEXT,
          warnings TEXT, profile TEXT DEFAULT '{}', created TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS doc_version ON documents(origin,hash,role,coalesce(project_id,''));
        CREATE TABLE IF NOT EXISTS chunks(
          id INTEGER PRIMARY KEY, document_id TEXT REFERENCES documents(id),
          locator TEXT, content TEXT, embedding BLOB);
        CREATE TABLE IF NOT EXISTS source_pins(project_id TEXT,document_id TEXT,PRIMARY KEY(project_id,document_id));
        CREATE TABLE IF NOT EXISTS source_selections(project_id TEXT PRIMARY KEY,document_ids TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS library_folders(path TEXT,role TEXT,subject TEXT,project_id TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS folder_identity ON library_folders(path,role,subject,coalesce(project_id,''));
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, tokenize='unicode61');
        CREATE TABLE IF NOT EXISTS messages(
          id INTEGER PRIMARY KEY, project_id TEXT REFERENCES projects(id), role TEXT, content TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, status TEXT, payload TEXT,
          result TEXT, error TEXT, created TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS steps(
          id TEXT PRIMARY KEY, project_id TEXT, position INTEGER, spec TEXT, status TEXT,
          attempts INTEGER DEFAULT 0, result TEXT DEFAULT '{}', error TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS artifacts(
          id TEXT PRIMARY KEY, project_id TEXT, step_id TEXT, name TEXT, path TEXT,
          state TEXT, provenance TEXT, revision INTEGER, created TEXT);
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY, project_id TEXT, kind TEXT, data TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS cloud_requests(
          id TEXT PRIMARY KEY, project_id TEXT, prompt TEXT, model TEXT, purpose TEXT,
          month TEXT, reserved REAL, actual REAL, state TEXT, response TEXT,
          config TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS cloud_cost_adjustments(
          request_id TEXT PRIMARY KEY REFERENCES cloud_requests(id),
          original_estimate REAL NOT NULL, corrected_estimate REAL NOT NULL,
          evidence TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS planning_recoveries(
          id TEXT PRIMARY KEY, project_id TEXT NOT NULL, revision INTEGER NOT NULL,
          source_job_id TEXT NOT NULL, checkpoint_sha256 TEXT NOT NULL,
          context_sha256 TEXT NOT NULL, job_id TEXT NOT NULL, created TEXT NOT NULL,
          UNIQUE(project_id,source_job_id,checkpoint_sha256));
        """)


def query(sql, args=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = query(sql, args)
    if not rows:
        raise ValueError("Запис не знайдено")
    return rows[0]


def execute(sql, args=()):
    with connect() as c:
        c.execute(sql, args)


def event(pid, kind, data):
    execute(
        "INSERT INTO events(project_id,kind,data,created) VALUES(?,?,?,?)",
        (pid, kind, json.dumps(data, ensure_ascii=False), now()),
    )


def message(pid, role, content):
    execute(
        "INSERT INTO messages(project_id,role,content,created) VALUES(?,?,?,?)",
        (pid, role, content, now()),
    )


def project(pid):
    p = one("SELECT * FROM projects WHERE id=?", (pid,))
    p["plan"] = json.loads(p["plan"])
    p["steps"] = [
        dict(s, spec=json.loads(s["spec"]), result=json.loads(s["result"]))
        for s in query(
            "SELECT * FROM steps WHERE project_id=? ORDER BY position", (pid,)
        )
    ]
    p["messages"] = query(
        "SELECT * FROM messages WHERE project_id=? ORDER BY id", (pid,)
    )
    p["artifacts"] = query(
        "SELECT * FROM artifacts WHERE project_id=? ORDER BY created DESC", (pid,)
    )
    p["documents"] = query(
        "SELECT id,name,role,status,warnings FROM documents WHERE project_id=?", (pid,)
    )
    p["jobs"] = query(
        "SELECT id,kind,status,error,created FROM jobs WHERE project_id=? ORDER BY created DESC LIMIT 10",
        (pid,),
    )
    return p


def enqueue(pid, kind, payload=None):
    jid = uid()
    execute(
        "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
        (
            jid,
            pid,
            kind,
            "queued",
            json.dumps(payload or {}, ensure_ascii=False),
            None,
            None,
            now(),
            now(),
        ),
    )
    return jid
