import json
from pathlib import Path
import subprocess

import pytest
from pypdf import PdfWriter

from study import config, db, engine, report_pdf, reports, worker
from study.schemas import Report


@pytest.mark.parametrize('kind', ['report', 'defense'])
def test_pdf_retry_uses_saved_docx_without_render_or_model(client, project, plan, monkeypatch, kind):
    pid = project['id']
    engine.save_plan(pid, plan)
    engine.approve(pid)
    calls = []

    def convert(args, **kwargs):
        calls.append(args)
        if len(calls) > 1:
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.write(Path(args[-2]) / Path(args[-1]).with_suffix('.pdf').name)
        return subprocess.CompletedProcess(args, 0, b'', b'')

    monkeypatch.setattr(report_pdf.subprocess, 'run', convert)
    monkeypatch.setattr(reports, 'binary', lambda name: 'controlled-soffice')
    monkeypatch.setattr(report_pdf, 'binary', lambda name: 'controlled-soffice')
    initial = reports.DocxRenderer().render(pid, Report(title='SA134 control', sections=[]), basename=kind)
    assert initial['pdf_status'] == 'missing_output'
    docx, = map(Path, initial['files'])
    original = docx.read_bytes()
    artifact = db.one('SELECT id FROM artifacts WHERE project_id=? AND name=?', (pid, docx.name))
    url = f"/api/projects/{pid}/artifacts/{artifact['id']}/pdf"

    def unexpected(*args, **kwargs):
        pytest.fail('Retry used document rendering, generation or computation')

    monkeypatch.setattr(reports.DocxRenderer, 'render', unexpected)
    monkeypatch.setattr('study.assistance.BudgetedModel.generate', unexpected)
    monkeypatch.setattr(engine, 'execute_project', unexpected)

    def dispatch():
        response = client.post(url)
        assert response.status_code == 200
        job = worker.claim()
        assert job['id'] == response.json()['job_id'] and job['kind'] == 'retry_pdf'
        outcome = worker.dispatch(job)
        db.execute("UPDATE jobs SET status='done',result=? WHERE id=?", (json.dumps(outcome), job['id']))
        return outcome

    outcome = dispatch()
    assert outcome['pdf_status'] == 'ready'
    assert outcome['files'] == [str(docx), str(docx.with_suffix('.pdf'))]
    assert docx.read_bytes() == original and report_pdf.readable_pdf(docx.with_suffix('.pdf'))
    assert dispatch() == outcome and len(calls) == 2
    assert len(db.query('SELECT id FROM artifacts WHERE project_id=? AND name=?', (pid, kind + '.pdf'))) == 1
    assert all(Path(args[-2]) != docx.parent for args in calls)
    docx.write_bytes(original + b'changed')
    assert client.post(url).status_code == 400


@pytest.mark.parametrize('mode,status', [('bad_pdf', 'invalid_pdf'), ('timeout', 'timeout')])
def test_failed_export_leaves_saved_docx_intact(tmp_path, monkeypatch, mode, status):
    docx = tmp_path / 'report.docx'
    docx.write_bytes(b'input')

    def convert(args, **kwargs):
        if mode == 'timeout':
            raise subprocess.TimeoutExpired(args, 120)
        (Path(args[-2]) / 'report.pdf').write_bytes(b'not a PDF')
        return subprocess.CompletedProcess(args, 0, b'', b'')

    monkeypatch.setattr(report_pdf.subprocess, 'run', convert)
    assert report_pdf.export_pdf(docx, 'controlled-soffice') == (None, status)
    assert docx.read_bytes() == b'input' and not docx.with_suffix('.pdf').exists()
