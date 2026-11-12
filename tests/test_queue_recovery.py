from types import SimpleNamespace
from study import db,worker


def test_queued_analysis_survives_restart_without_becoming_paused(project,monkeypatch):
    pid=project['id']
    db.execute("UPDATE projects SET status='analysis_running' WHERE id=?",(pid,))
    jid=db.enqueue(pid,'analyse')
    monkeypatch.setattr('subprocess.run',lambda *a,**k:SimpleNamespace(stdout='',returncode=0))
    worker.recover()
    assert db.project(pid)['status']=='analysis_running'
    assert db.one('SELECT status FROM jobs WHERE id=?',(jid,))['status']=='queued'


def test_actually_interrupted_analysis_requires_resume(project,monkeypatch):
    pid=project['id']
    db.execute("UPDATE projects SET status='analysis_running' WHERE id=?",(pid,))
    jid=db.enqueue(pid,'analyse')
    db.execute("UPDATE jobs SET status='running' WHERE id=?",(jid,))
    monkeypatch.setattr('subprocess.run',lambda *a,**k:SimpleNamespace(stdout='',returncode=0))
    worker.recover()
    assert db.project(pid)['status']=='paused'
    assert db.one('SELECT status FROM jobs WHERE id=?',(jid,))['status']=='interrupted'
