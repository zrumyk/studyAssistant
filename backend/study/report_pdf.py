"""Export a saved report or defense without rebuilding its DOCX."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from . import config, db
from .documents import binary
from .execution_protocol import stream_identity


def readable_pdf(path):
    try:
        with path.open('rb') as stream:
            return len(PdfReader(stream).pages) > 0
    except (OSError, PyPdfError, ValueError):
        return False


def export_pdf(docx, executable):
    """Return the published PDF and an outcome, including silent missing output."""
    if not executable:
        return None, 'unavailable'
    identity = stream_identity(docx)
    if identity is None:
        return None, 'missing_docx'
    try:
        with tempfile.TemporaryDirectory(prefix='sa-lo-', ignore_cleanup_errors=True) as directory:
            scratch = Path(directory)
            result = subprocess.run(
                [executable, '-env:UserInstallation=' + (scratch / 'profile').as_uri(),
                 '--headless', '--convert-to', 'pdf', '--outdir', str(scratch), str(docx)],
                capture_output=True, timeout=120,
            )
            output = scratch / docx.with_suffix('.pdf').name
            if result.returncode:
                return None, 'conversion_failed'
            if not output.is_file():
                return None, 'missing_output'
            if not readable_pdf(output):
                return None, 'invalid_pdf'
            if stream_identity(docx) != identity:
                return None, 'changed_docx'
            # The converter scratch folder may be on another volume.
            fd, temporary = tempfile.mkstemp(prefix='.pdf-', dir=docx.parent)
            os.close(fd)
            staged = Path(temporary)
            try:
                shutil.copyfile(output, staged)
                pdf = docx.with_suffix('.pdf')
                staged.replace(pdf)
            finally:
                staged.unlink(missing_ok=True)
            return pdf, 'ready'
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    except OSError:
        return None, 'conversion_failed'


def saved_docx(pid, aid):
    row = db.one(
        "SELECT a.*,p.revision AS current_revision,p.approved_hash FROM artifacts a "
        "JOIN projects p ON p.id=a.project_id WHERE a.id=? AND p.id=?", (aid, pid),
    )
    if (row['state'] == 'stale' or row['revision'] != row['current_revision']
            or not row['approved_hash']):
        raise ValueError('Потрібен DOCX поточної погодженої роботи')
    proof = json.loads(row['provenance'])
    kind = proof.get('type')
    relative = Path(row['path'])
    if (kind not in ('report', 'defense') or len(relative.parts) != 3
            or relative.parts[0] != 'reports' or relative.name != kind + '.docx'):
        raise ValueError('Оберіть збережений DOCX звіту або захисту')
    root = config.project_dir(pid).resolve()
    path = config.safe_path(root, relative)
    if path != root / relative:
        raise ValueError('DOCX має бути звичайним файлом у каталозі звітів')
    identity = stream_identity(path)
    if not identity or ('docx_identity' in proof and proof['docx_identity'] != identity):
        raise ValueError('Збережений DOCX відсутній або змінився')
    return row, path, kind, identity


def retry_pdf(pid, aid):
    row, docx, kind, identity = saved_docx(pid, aid)
    pdf = docx.with_suffix('.pdf')
    relative = str(pdf.relative_to(config.project_dir(pid)))
    registered = db.query(
        "SELECT id FROM artifacts WHERE project_id=? AND path=? AND revision=? AND state!='stale'",
        (pid, relative, row['revision']),
    )
    if registered and not pdf.is_symlink() and readable_pdf(pdf):
        status = 'ready'
    else:
        pdf, status = export_pdf(docx, binary('soffice'))
    result = {'files': [str(docx)], 'pdf_status': status}
    if pdf is None:
        return result
    current, _, _, current_identity = saved_docx(pid, aid)
    if current['approved_hash'] != row['approved_hash'] or current_identity != identity:
        raise ValueError('Робота змінилася під час PDF-експорту')
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        project = connection.execute("SELECT revision,approved_hash FROM projects WHERE id=?", (pid,)).fetchone()
        if tuple(project) != (row['revision'], row['approved_hash']):
            raise ValueError('Робота змінилася під час PDF-експорту')
        if not connection.execute(
            "SELECT id FROM artifacts WHERE project_id=? AND path=? AND revision=? AND state!='stale'",
            (pid, relative, row['revision']),
        ).fetchone():
            connection.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)", (
                db.uid(), pid, None, pdf.name, relative, 'needs_review',
                json.dumps({'type': kind, 'revision': row['revision'], 'source_docx': aid}),
                row['revision'], db.now(),
            ))
    result['files'].append(str(pdf))
    return result
