import re
import numpy as np
from . import db, config
from .providers import OllamaProvider


def scope(pid, include_subject_theory=False):
    p = db.one("SELECT subject,approved_hash FROM projects WHERE id=?", (pid,))
    if p["approved_hash"]:
        if include_subject_theory:
            return (
                "(d.id IN (SELECT document_id FROM source_pins WHERE project_id=?) OR (d.project_id=? AND d.role='result') OR (d.project_id IS NULL AND d.subject=? AND d.role='theory' AND d.status!='superseded'))",
                (pid, pid, p['subject']),
            )
        return (
            "(d.id IN (SELECT document_id FROM source_pins WHERE project_id=?) OR (d.project_id=? AND d.role='result'))",
            (pid, pid),
        )
    selected = db.query('SELECT document_ids FROM source_selections WHERE project_id=?', (pid,))
    if selected:
        import json
        ids = json.loads(selected[0]['document_ids'])
        placeholders = ','.join('?' for _ in ids) or 'NULL'
        return (
            "d.status!='superseded' AND (d.project_id=? OR (d.project_id IS NULL AND d.subject=? AND (d.role='theory' OR d.id IN (" + placeholders + "))))",
            (pid, p['subject'], *ids),
        )
    return (
        "d.status!='superseded' AND (d.project_id=? OR (d.project_id IS NULL AND d.subject=?))",
        (
            pid,
            p["subject"],
        ),
    )


def index_embeddings(pid=None):
    condition, args = scope(pid) if pid else ("1=1", ())
    rows = db.query(
        "SELECT c.id,c.content FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.embedding IS NULL AND "
        + condition,
        args,
    )
    model = OllamaProvider()
    model.unload()
    for i in range(0, len(rows), 8):
        group = rows[i : i + 8]
        vectors = model.embed(
            [r["content"] for r in group],
            keep_alive=0 if i + 8 >= len(rows) else "5m",
        )
        with db.connect() as c:
            for r, v in zip(group, vectors, strict=True):
                a = np.array(v, dtype=np.float32)
                a /= max(np.linalg.norm(a), 1e-12)
                c.execute(
                    "UPDATE chunks SET embedding=? WHERE id=?", (a.tobytes(), r["id"])
                )
        db.event(
            pid, "index_progress", {"done": min(i + 8, len(rows)), "total": len(rows)}
        )
    return len(rows)


def search(pid, text, limit=6, semantic=True, include_subject_theory=False):
    condition, args = scope(pid, include_subject_theory)
    base = (
        "SELECT c.id,c.document_id,c.locator,c.content,d.name,c.embedding,d.role,d.warnings FROM chunks c JOIN documents d ON d.id=c.document_id WHERE "
        + condition
    )
    tokens = re.findall(r"[^\W_]+", text, re.UNICODE)[:24]
    scores = {}
    rows = {}
    if tokens:
        match = " OR ".join('"' + t + '"' for t in tokens)
        hits = db.query(
            base
            + " AND c.id IN (SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT 40)",
            (*args, match),
        )
        for i, r in enumerate(hits):
            scores[r["id"]] = 1 / (60 + i)
            rows[r["id"]] = r
    if semantic:
        candidates = db.query(base + " AND c.embedding IS NOT NULL", args)
        if candidates:
            try:
                q = np.array(OllamaProvider().embed([text])[0], dtype=np.float32)
                q /= max(np.linalg.norm(q), 1e-12)
                ranked = sorted(
                    [
                        (
                            float(
                                np.dot(
                                    np.frombuffer(r["embedding"], dtype=np.float32), q
                                )
                            ),
                            r,
                        )
                        for r in candidates
                        if len(r["embedding"]) == q.nbytes
                    ],
                    key=lambda x: x[0],
                    reverse=True,
                )[:40]
                for i, (_, r) in enumerate(ranked):
                    scores[r["id"]] = scores.get(r["id"], 0) + 1 / (60 + i)
                    rows[r["id"]] = r
            except Exception:
                pass  # Lexical retrieval remains available; UI exposes indexing status.
    if not rows:
        hits = db.query(
            base
            + " ORDER BY CASE d.role WHEN 'task' THEN 0 WHEN 'reference' THEN 1 ELSE 2 END,c.id LIMIT ?",
            (*args, limit),
        )
    else:
        hits = [rows[k] for k in sorted(scores, key=scores.get, reverse=True)[:limit]]
    return [{k: v for k, v in r.items() if k != "embedding"} for r in hits]


def context(pid, text, limit=6, exclude_reference=False, include_subject_theory=False, with_sources=False):
    hits = search(pid, text, limit * 3 if exclude_reference else limit,
                  include_subject_theory=include_subject_theory)
    if exclude_reference:
        hits = [r for r in hits if r["role"] != "reference"][:limit]
    # Keep complete retrieval chunks (including their table headers). The model
    # must retain room for the task, system rules, schema and generated answer.
    budget = config.settings()["context"]
    parts = []
    references=[]
    used = 0
    omitted = False
    for r in hits:
        part = f"[ДЖЕРЕЛО {r['document_id']} | {r['name']} | {r['locator']} | роль={r['role']}]\n{r['content']}"
        import json

        notices = json.loads(r.get("warnings", "[]"))
        if notices:
            part += "\n[Якість читання джерела: " + "; ".join(notices[:2])[:350] + "]"
        if used + len(part) > budget:
            omitted = True
            continue
        parts.append(part)
        references.append({'document_id':r['document_id'],'locator':r['locator'],'excerpt':r['content'][:300]})
        used += len(part) + 2
    if omitted:
        parts.append(
            "[Частина знайдених фрагментів не ввійшла в контекст. Це не повний текст матеріалів; не роби висновку про відсутність вимог у всій бібліотеці.]"
        )
    text="\n\n".join(parts)
    return (text,references) if with_sources else text
