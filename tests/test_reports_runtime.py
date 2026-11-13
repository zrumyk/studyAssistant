import json
import pytest
from pathlib import Path
from study import db, config, engine
from study.schemas import Report, Action
from study.reports import DocxRenderer
from study.documents import UniversalParser


def test_title_page_sections_are_left_to_the_renderer():
    from study.reports import report_headings
    planned = ["Титульна сторінка", "Мета роботи", "Title page", "Висновки", "Титульний аркуш"]
    assert report_headings(planned) == ["Мета роботи", "Висновки"]


def test_renderer_title_carries_the_current_year(project, plan, monkeypatch):
    from datetime import date
    from docx import Document
    from study import reports
    monkeypatch.setattr(reports, "binary", lambda name: None)
    pid = project["id"]
    engine.save_plan(pid, plan)
    out = DocxRenderer().render(pid, Report(title="Звіт", sections=[]))
    text = [p.text for p in Document(out["files"][0]).paragraphs]
    assert str(date.today().year) in text


def test_docx_renderer_contains_actual_evidence(client, project, plan, monkeypatch):
    from docx import Document
    from study import reports

    monkeypatch.setattr(reports, "binary", lambda name: None)
    pid = project["id"]
    engine.save_plan(pid, plan)
    engine.approve(pid)
    root = config.project_dir(pid) / "workspace"
    root.mkdir()
    (root / "result.csv").write_text("metric,value\nmean,4\n", "utf-8")
    engine.artifact(pid, None, "result.csv", "executed")
    out = DocxRenderer().render(
        pid,
        Report(
            title="Перевірка розрахунку",
            sections=[
                {
                    "heading": "Результати",
                    "text": "Середнє значення дорівнює 4 за результатами виконання.\n\n$\\bar{x}=\\frac{2+4+6}{3}=4$",
                }
            ],
        ),
    )
    doc = Document(out["files"][0])
    assert doc.paragraphs[0].text == "Перевірка розрахунку"
    assert any("Середнє значення" in p.text for p in doc.paragraphs)
    assert doc.tables[0].cell(1, 1).text == "4"
    assert "<m:f>" in doc.element.xml and "<m:den>" in doc.element.xml
    assert "LibreOffice" in out["warning"]
    pages, warnings, profile = UniversalParser().parse(Path(out["files"][0]))
    assert "Результати" in profile["headings"]
    assert any("mean" in t for _, t in pages)
    assert any("\\frac{2+4+6}{3}" in t for _, t in pages)


def test_artifact_snapshot_immutable(client, project):
    pid = project["id"]
    root = config.project_dir(pid) / "workspace"
    root.mkdir()
    (root / "result.txt").write_text("old", "utf-8")
    engine.artifact(pid, None, "result.txt", "executed")
    old = db.project(pid)["artifacts"][0]
    (root / "result.txt").write_text("new", "utf-8")
    engine.artifact(pid, None, "result.txt", "executed")
    assert (config.project_dir(pid) / old["path"]).read_text("utf-8") == "old"
    assert (
        db.one("SELECT state FROM artifacts WHERE id=?", (old["id"],))["state"]
        == "stale"
    )


def test_manual_screenshot_is_in_report_and_fits_page(
    isolated, project, plan, monkeypatch
):
    from PIL import Image
    from docx import Document
    from docx.shared import Cm
    from study import reports, documents

    monkeypatch.setattr(reports, "binary", lambda name: None)
    monkeypatch.setattr(
        documents.UniversalParser,
        "parse",
        lambda *args: ([("Image", "Screenshot")], [], {}),
    )
    pid = project["id"]
    engine.save_plan(pid, plan)
    engine.approve(pid)
    path = isolated / "screen.png"
    Image.new("RGB", (100, 500), "white").save(path)
    documents.import_file(path, "result", "Приклад", pid)
    sid = db.project(pid)["steps"][0]["id"]
    db.execute("UPDATE steps SET status='waiting_user' WHERE id=?", (sid,))
    engine.manual_result(pid, sid, "screen.png додано", confirmed=True)
    result = DocxRenderer().render(pid, Report(title="Звіт", sections=[]))
    document = Document(result["files"][0])
    assert len(document.inline_shapes) == 1
    assert document.inline_shapes[0].height <= Cm(20)
    assert document.inline_shapes[0].width <= Cm(15)
    assert any("Надано користувачем: screen.png" in p.text for p in document.paragraphs)


def test_expected_output_must_be_from_current_run(client, project, plan, monkeypatch):
    pid = project["id"]
    engine.save_plan(pid, plan)
    engine.approve(pid)
    root = config.project_dir(pid) / "workspace"
    root.mkdir()
    (root / "results.csv").write_text("old value", "utf-8")
    monkeypatch.setattr(
        engine,
        "prepare_action",
        lambda *args: Action(
            files=[{"path": "main.py", "content": "print(4)"}],
            entrypoint="main.py",
            expected_files=["results.csv"],
            explanation="",
        ),
    )
    monkeypatch.setattr(engine.OllamaProvider, "unload", lambda self: None)
    monkeypatch.setattr(
        engine.DockerRuntime,
        "run",
        lambda *a, **k: {
            "exit_code": 0,
            "reason": "",
            "oom": False,
            "produced_files": [],
            "files": [],
            "log": "",
            "run_id": "test",
        },
    )
    engine.execute_project(pid)
    assert db.project(pid)["status"] == "needs_user"
    assert "Цей запуск не створив" in db.project(pid)["steps"][0]["error"]


def test_visuals_escape_untrusted_labels(client, project, plan):
    from study.visuals import workflow

    pid = project["id"]
    plan["steps"][0]["title"] = "<script>alert(1)</script>"
    engine.save_plan(pid, plan)
    workflow(pid, db.project(pid)["steps"][0]["id"])
    text = (config.project_dir(pid) / "workspace/workflow.svg").read_text("utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_all_runtime_languages_have_fixed_entrypoints():
    from study.runtime import RUNTIMES

    assert set(RUNTIMES) == {
        "python",
        "javascript",
        "typescript",
        "c",
        "cpp",
        "java",
        "csharp",
        "r",
        "sql",
        "octave",
    }
    assert all("/" not in r[1] and "\\" not in r[1] for r in RUNTIMES.values())


def test_report_markdown_becomes_word_blocks():
    from docx import Document
    from study.reports import add_report_section

    document = Document()
    warnings = []
    add_report_section(
        document,
        "Результати",
        "### Результати\n\n**Середнє**: $4$\n\n- Перевірено\n\n"
        "```csv\nmean\n4.0\n```\n\n| Показник | Значення |\n|---|---|\n| mean | 4 |",
        warnings,
    )
    text = "\n".join(p.text for p in document.paragraphs)
    assert "###" not in text and "**" not in text and "```" not in text
    assert "Результати" not in text
    assert any(
        run.bold and run.text == "Середнє"
        for p in document.paragraphs
        for run in p.runs
    )
    assert document.tables[0].cell(1, 1).text == "4"
    assert document._element.xpath(".//m:oMath")
    assert not warnings


def test_report_table_equations_render_natively_and_header_stays_with_data():
    from docx import Document
    from study.reports import add_report_section
    from docx.oxml.ns import qn
    d=Document();warnings=[]
    add_report_section(d,'Metrics','| Window | Time |\n|---|---|\n| 3 | $1.4 \\cdot 10^{-4}$ |',warnings)
    table=d.tables[0]
    assert table.cell(1,1)._tc.xpath('.//m:oMath')
    assert '$' not in table.cell(1,1).text and not warnings
    assert table.rows[0]._tr.get_or_add_trPr().find(qn('w:tblHeader')) is not None
    assert table.rows[0].cells[0].paragraphs[0].paragraph_format.keep_with_next


def test_report_keeps_metrics_from_each_code_step(project, plan):
    from study.reports import report_evidence

    pid = project["id"]
    plan["steps"].append(dict(plan["steps"][0], title="Другий розрахунок"))
    engine.save_plan(pid, plan)
    workspace = config.project_dir(pid) / "workspace"
    workspace.mkdir()
    for index, step in enumerate(db.project(pid)["steps"]):
        name = f"metrics_{index}.json"
        (workspace / name).write_text(
            json.dumps({"accuracy": 0.71 + index * 0.1}), "utf-8"
        )
        engine.artifact(pid, step["id"], name, "executed")
        db.execute(
            "UPDATE steps SET status='done',result=? WHERE id=?",
            (json.dumps({"run_id": str(index), "log": "x" * 10000}), step["id"]),
        )
    evidence = report_evidence(db.project(pid))
    assert "metrics_0.json" in evidence and "metrics_1.json" in evidence
    assert '"accuracy": 0.71' in evidence
    assert len(evidence) < 4000


def test_report_review_uses_actual_run_sources_not_later_workspace(project, plan):
    from study.reports import report_evidence
    pid = project['id']
    engine.save_plan(pid, plan)
    sid = db.project(pid)['steps'][0]['id']
    root = config.project_dir(pid)
    run = root / 'runs' / 'proof'
    run.mkdir(parents=True)
    (run / 'main.py').write_text('ACTUAL_RUN_INPUTS = [8,16,21,34,51]', 'utf-8')
    (run / 'check_csv.py').write_text('assert ACTUAL_CHECKER', 'utf-8')
    workspace = root / 'workspace'; workspace.mkdir()
    (workspace / 'main.py').write_text('LATER_UNEXECUTED_CHANGE', 'utf-8')
    db.execute("UPDATE steps SET status='done',result=? WHERE id=?", (json.dumps({
        'run_id':'proof', 'exit_code':0, 'checks':['check_csv.py'], 'oom':False,'reason':''}), sid))
    evidence = report_evidence(db.project(pid))
    assert 'ФАКТ RUNTIME' in evidence
    assert 'ACTUAL_RUN_INPUTS' in evidence and 'ACTUAL_CHECKER' in evidence
    assert 'LATER_UNEXECUTED_CHANGE' not in evidence


@pytest.mark.parametrize("name", ["CON", "NUL.txt", "main.py:stream", "folder/AUX.txt"])
def test_windows_special_file_names(isolated, name):
    with pytest.raises(ValueError):
        config.safe_path(isolated, name)


def test_report_repairs_rejected_section_before_rendering(project, plan, monkeypatch):
    from study import assistance, reports
    from study.schemas import Assessment

    pid = project["id"]
    engine.save_plan(pid, plan)
    engine.approve(pid)
    db.execute("UPDATE steps SET status='done' WHERE project_id=?", (pid,))
    config.save_settings({"cloud_consent_mode": "budgeted"})
    monkeypatch.setattr(reports, "report_evidence", lambda p,**kwargs: "Actual mean=4")
    monkeypatch.setattr("study.search.context", lambda *a, **k: "")

    def local(self, prompt, schema=None):
        return (
            Assessment(satisfied=False, explanation="Mean must be 4")
            if schema
            else "Mean=99"
        )

    monkeypatch.setattr(reports.OllamaProvider, "generate", local)
    calls = []

    def remote(self, prompt, schema=None):
        calls.append(prompt)
        if schema:
            return Assessment(
                satisfied="Виправлений текст" in prompt, explanation="Review"
            )
        return "Mean=4"

    monkeypatch.setattr(assistance.BudgetedModel, "remote", remote)

    def render(self, pid, report):
        assert all(s.text == "Mean=4" for s in report.sections)
        out = config.project_dir(pid) / "reports" / "test"
        out.mkdir(parents=True)
        return {"files": [str(out / "report.docx")]}

    monkeypatch.setattr(reports.DocxRenderer, "render", render)
    result = reports.build_report(pid)
    assert (Path(result["files"][0]).parent / "automatic-review.json").exists()
    assert len(calls) == 6  # Two sections, each review -> rewrite -> re-review.


def test_report_rejects_another_approved_section_even_if_model_accepts():
    from study.reports import review_section_structure
    from study.schemas import ContentReview

    review = ContentReview(blocking_issues=[], explanation="Model accepted")
    result = review_section_structure(
        review, "Результати", "Дані.\n\n## Висновки\n\nТекст.",
        ["Результати", "Висновки"],
    )
    assert not result.satisfied
    assert "висновки" in result.blocking_issues[0]


def test_report_allows_own_heading_and_unrelated_subheading():
    from study.reports import review_section_structure
    from study.schemas import ContentReview

    review = ContentReview(blocking_issues=[], explanation="Accepted")
    result = review_section_structure(
        review, "Результати", "## Результати\n\n### Таблиця\n\nДані.",
        ["Результати", "Висновки"],
    )
    assert result is review


def test_remote_report_keeps_measured_values_despite_long_checker_inventory(project,plan):
    from study.reports import report_evidence
    pid=project['id']
    plan['steps']=[dict(plan['steps'][0],title=f'Experiment {i}') for i in range(6)]
    engine.save_plan(pid,plan)
    config.save_settings({'cloud_consent_mode':'budgeted'})
    root=config.project_dir(pid)/'workspace';root.mkdir()
    for i,step in enumerate(db.project(pid)['steps']):
        name=f'metrics_{i}.csv'
        (root/name).write_text('window,mse,psnr,edge_score\n'+''.join(f'{k},{123.456+i},{24.1234+i},{0.8123+i}\n' for k in range(3,14,2)),'utf-8')
        engine.artifact(pid,step['id'],name,'executed')
        result={'run_id':str(i),'exit_code':0,'checks':[f'very_long_checker_path_{j}.py' for j in range(100)]}
        db.execute("UPDATE steps SET status='done',result=? WHERE id=?",(json.dumps(result),step['id']))
    evidence=report_evidence(db.project(pid))
    for i in range(6):
        assert f'metrics_{i}.csv' in evidence and str(123.456+i) in evidence
    assert len(evidence)<24000


def test_report_resumes_accepted_sections_and_invalidates_changed_evidence(project,plan,monkeypatch):
    from study import reports,assistance
    from study.schemas import ContentReview
    pid=project['id'];engine.save_plan(pid,plan);engine.approve(pid)
    db.execute("UPDATE steps SET status='done' WHERE project_id=?",(pid,))
    config.save_settings({'cloud_consent_mode':'budgeted'})
    evidence=['mean=4'];calls=[];fail=[True]
    monkeypatch.setattr(reports,'report_evidence',lambda p,**kwargs:evidence[0])
    monkeypatch.setattr('study.search.context',lambda *a,**kw:'')
    def generate(self,prompt,schema=None):
        calls.append(('review' if schema else 'write',prompt))
        if schema:return ContentReview(blocking_issues=[],explanation='Consistent')
        if '«Результати»' in prompt and fail[0]:
            raise RuntimeError('Transport unavailable')
        return 'Значення '+evidence[0]
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    monkeypatch.setattr(assistance.BudgetedModel,'remote',generate)
    def render(self,pid,report):
        out=config.project_dir(pid)/'reports'/'test';out.mkdir(parents=True,exist_ok=True)
        assert all(evidence[0] in s.text for s in report.sections)
        return {'files':[str(out/'report.docx')]}
    monkeypatch.setattr(reports.DocxRenderer,'render',render)
    with pytest.raises(RuntimeError,match='Transport'):reports.build_report(pid)
    fail[0]=False;calls.clear()
    reports.build_report(pid)
    assert len(calls)==2 # Only the unfinished second section is written/reviewed.
    assert db.query("SELECT id FROM events WHERE kind='report_section_reused'")
    evidence[0]='mean=5';calls.clear()
    reports.build_report(pid)
    assert len(calls)==4 # Old accepted text must not survive changed measurements.


def test_pause_during_report_preserves_accepted_section_and_stops_next_request(project,plan,monkeypatch):
    from study import reports,assistance
    from study.schemas import ContentReview
    pid=project['id'];engine.save_plan(pid,plan);engine.approve(pid)
    db.execute("UPDATE steps SET status='done' WHERE project_id=?",(pid,))
    config.save_settings({'cloud_consent_mode':'budgeted'})
    monkeypatch.setattr(reports,'report_evidence',lambda p,**kwargs:'mean=4')
    monkeypatch.setattr('study.search.context',lambda *a,**kw:'')
    calls=[]
    def generate(self,prompt,schema=None):
        calls.append(prompt)
        if schema:
            db.event(pid,'paused',{})
            return ContentReview(blocking_issues=[],explanation='Consistent')
        return 'mean=4'
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    monkeypatch.setattr(assistance.BudgetedModel,'remote',generate)
    monkeypatch.setattr(reports.DocxRenderer,'render',lambda *a:pytest.fail('Paused report must not render'))
    with pytest.raises(RuntimeError,match='призупинено'):reports.build_report(pid)
    assert len(calls)==2
    assert len(list((config.project_dir(pid)/'report-sections').glob('*.json')))==1


def test_internal_checker_runner_is_not_included_as_student_code(project,monkeypatch):
    from study import reports
    from docx import Document
    pid=project['id'];workspace=config.project_dir(pid)/'workspace';workspace.mkdir()
    (workspace/'.study_checker_runner.py').write_text('INTERNAL_RUNNER_SHOULD_NOT_APPEAR','utf-8')
    engine.artifact(pid,None,'.study_checker_runner.py','executed')
    monkeypatch.setattr(reports,'binary',lambda *a:None)
    result=DocxRenderer().render(pid,Report(title='Test',sections=[]))
    text='\n'.join(p.text for p in Document(result['files'][0]).paragraphs)
    assert 'INTERNAL_RUNNER_SHOULD_NOT_APPEAR' not in text


def test_global_profile_is_shared_by_report_writer_reviewer_and_cover(project,plan,monkeypatch):
    from study import reports,assistance
    from study.schemas import ContentReview
    from docx import Document
    pid=project['id'];engine.save_plan(pid,plan);engine.approve(pid)
    db.execute("UPDATE steps SET status='done' WHERE project_id=?",(pid,))
    config.save_settings({'author':'Global Author','group':'Global Group','institution':'Global University'})
    monkeypatch.setattr(reports,'report_evidence',lambda p,**kwargs:'mean=4')
    monkeypatch.setattr('study.search.context',lambda *a,**kw:'')
    monkeypatch.setattr(reports,'binary',lambda *a:None)
    calls=[]
    def generate(self,prompt,schema=None):
        calls.append(prompt)
        assert config.settings()['author'] in prompt and 'Global Group' in prompt
        assert 'мають пріоритет' in prompt
        return ContentReview(blocking_issues=[],explanation='Consistent') if schema else 'Результат mean=4.'
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    result=reports.build_report(pid)
    text='\n'.join(p.text for p in Document(result['files'][0]).paragraphs)
    assert 'Global Author' in text and 'Global Group' in text and 'Global University' in text
    assert len(calls)==4
    config.save_settings({'author':'Updated Global Author'});calls.clear()
    reports.build_report(pid)
    assert len(calls)==4 # Changed profile invalidates dependent section checkpoints.
