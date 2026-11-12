import json
from pathlib import Path
import pytest
from study import config, db, engine, defense, semester
from study.schemas import ContentReview


def finish(project, plan):
    pid = project['id']
    engine.save_plan(pid, plan)
    engine.approve(pid)
    db.execute("UPDATE steps SET status='done' WHERE project_id=?", (pid,))
    db.execute("UPDATE projects SET status='completed' WHERE id=?", (pid,))
    return pid


def test_defense_requires_completed_approved_work(client, project):
    response = client.post('/api/projects/' + project['id'] + '/defense')
    assert response.status_code == 400
    assert not db.query("SELECT id FROM jobs WHERE kind='defense'")


def test_defense_queue_deduplicates(client, project, plan):
    pid = finish(project, plan)
    assert defense.enqueue(pid) == defense.enqueue(pid)
    assert len(db.query("SELECT id FROM jobs WHERE kind='defense'")) == 1


def test_defense_generated_reviewed_cached_and_downloadable(client, project, plan, monkeypatch):
    pid = finish(project, plan)
    calls = []
    monkeypatch.setattr(defense, 'context', lambda *a, **k: '[lecture page 2]')
    monkeypatch.setattr(defense, 'report_evidence', lambda p: 'Actual mean=4')
    def generate(self, prompt, schema=None, **kwargs):
        calls.append(prompt)
        return ContentReview(blocking_issues=[], explanation='Checked') if schema else 'Пояснення фактичного середнього 4.'
    monkeypatch.setattr(defense.BudgetedModel, 'generate', generate)
    def render(self, pid, report, basename='report'):
        assert basename == 'defense'
        out = config.project_dir(pid) / 'reports' / 'guide'
        out.mkdir(parents=True)
        for suffix in ('docx', 'pdf'):
            (out / ('defense.' + suffix)).write_bytes(b'rendered')
        return {'files':[str(out / 'defense.docx'), str(out / 'defense.pdf')], 'warning':''}
    monkeypatch.setattr(defense.DocxRenderer, 'render', render)
    result = defense.build(pid)
    assert result['sections'] == len(defense.TOPICS) + 1
    assert Path(result['files'][-1]).name == 'defense.md'
    first_call_count = len(calls)
    assert defense.build(pid) == result
    assert len(calls) == first_call_count
    artifact = db.one("SELECT id FROM artifacts WHERE name='defense.md'")
    response = client.get(f'/api/projects/{pid}/artifacts/{artifact["id"]}')
    assert response.status_code == 200 and 'Пояснення' in response.text


def test_defense_blocks_incorrect_explanation_before_render(project, plan, monkeypatch):
    pid = finish(project, plan)
    monkeypatch.setattr(defense, 'context', lambda *a, **k: '')
    monkeypatch.setattr(defense, 'report_evidence', lambda p: 'mean=4')
    def generate(self, prompt, schema=None, **kwargs):
        return ContentReview(blocking_issues=['Wrong mean'], explanation='Wrong') if schema else 'mean=99'
    monkeypatch.setattr(defense.BudgetedModel, 'generate', generate)
    monkeypatch.setattr(defense.DocxRenderer, 'render', lambda *a, **k: pytest.fail('Rejected text rendered'))
    with pytest.raises(RuntimeError, match='Wrong mean'):
        defense.build(pid)
    assert not db.query("SELECT id FROM artifacts WHERE name LIKE 'defense.%'")


def test_defense_snapshots_executed_code_and_invalidates_on_new_theory(project, plan):
    pid = finish(project, plan)
    sid = db.project(pid)['steps'][0]['id']
    run = config.project_dir(pid) / 'runs' / 'executed'
    run.mkdir(parents=True)
    (run / 'main.py').write_text('print(4)', 'utf-8')
    db.execute('UPDATE steps SET result=? WHERE id=?', (json.dumps({'run_id':'executed'}), sid))
    before, sources = defense.snapshot(db.project(pid))
    assert sources[0]['text'] == 'print(4)'
    from study.documents import import_file
    lecture = config.DATA / 'new_theory.txt'
    lecture.write_text('Новий матеріал про середнє арифметичне', 'utf-8')
    import_file(lecture, 'theory', project['subject'])
    after, _ = defense.snapshot(db.project(pid))
    assert before != after


def test_defense_scope_adds_only_same_subject_theory(project, plan):
    pid = finish(project, plan)
    from study import documents, search
    for subject, role, name in [('Приклад','theory','right'), ('Інший','theory','wrong'), ('Приклад','reference','reference')]:
        path = config.DATA / (name + '.txt')
        path.write_text('арифметичне середнє', 'utf-8')
        documents.import_file(path, role, subject)
    assert not search.search(pid, 'середнє', semantic=False)
    hits = search.search(pid, 'середнє', semantic=False, include_subject_theory=True)
    assert {h['name'] for h in hits} == {'right.txt'}


def test_semester_preview_and_import_preserve_scope_and_originals(client, monkeypatch):
    folder = config.DATA / 'semester'
    config.save_settings({'semester_root': str(folder)})
    lecture = folder / '01_AI' / '01_Лекції' / 'lecture.txt'
    task = folder / '05_IoT' / '02_Лабораторні' / 'lab.txt'
    for path in (lecture, task):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Навчальний матеріал для перевірки', 'utf-8')
    preview = client.get('/api/semester').json()
    assert len(preview['files']) == 2
    assert {f['role'] for f in preview['files']} == {'theory','task'}
    result = semester.import_semester()
    assert len(result['imported']) == 2 and not result['errors']
    assert lecture.exists() and task.exists()
    docs = db.query('SELECT subject,role FROM documents')
    assert {'subject':'Штучний інтелект','role':'theory'} in docs
    assert {'subject':'Інтернет речей','role':'task'} in docs
    semester.import_semester()
    assert len(db.query('SELECT id FROM documents')) == 2


def test_semester_refuses_unassigned_files(client):
    folder = config.DATA / 'semester'
    config.save_settings({'semester_root':str(folder)})
    folder.mkdir()
    (folder / 'lecture.txt').write_text('Теорія', 'utf-8')
    response = client.post('/api/semester/import')
    assert response.status_code == 400
    assert not db.query("SELECT id FROM jobs WHERE kind='semester_import'")


def test_semester_catalog_classifies_unsorted_and_deduplicates_bytes(client):
    folder = config.DATA / 'semester'
    config.save_settings({'semester_root':str(folder)})
    inbox = folder / '00_UNSORTED'
    inbox.mkdir(parents=True)
    catalog = {}
    for name in ('one.txt','copy.txt'):
        (inbox/name).write_text('Однаковий навчальний матеріал', 'utf-8')
        catalog['00_UNSORTED/'+name] = {'subject':'Приклад','role':'theory'}
    (folder/'_catalog.json').write_text(json.dumps(catalog), 'utf-8')
    assert not semester.preview()['unassigned']
    result = semester.import_semester()
    assert len(result['imported']) == 1 and len(result['duplicates']) == 1
    assert len(db.query('SELECT id FROM documents')) == 1


def test_defense_ignores_old_unexecuted_checker(project, plan):
    pid = finish(project, plan)
    sid = db.project(pid)['steps'][0]['id']
    run = config.project_dir(pid)/'runs'/'executed'
    run.mkdir(parents=True)
    (run/'main.py').write_text('import calculation\nprint(calculation.value)', 'utf-8')
    (run/'calculation.py').write_text('value=4', 'utf-8')
    (run/'check_actual.py').write_text('assert True', 'utf-8')
    (run/'check_old.py').write_text('assert False', 'utf-8')
    db.execute('UPDATE steps SET result=? WHERE id=?', (json.dumps({'run_id':'executed','checks':['check_actual.py']}), sid))
    _, sources = defense.snapshot(db.project(pid))
    assert {s['path'] for s in sources} == {'main.py','calculation.py','check_actual.py'}


def test_defense_docx_renderer_keeps_lab_report_separate(project, plan, monkeypatch):
    from study import reports
    from study.schemas import Report, ReportSection
    from docx import Document
    pid = finish(project, plan)
    monkeypatch.setattr(reports, 'binary', lambda name: None)
    result = reports.DocxRenderer().render(pid, Report(
        title='Підготовка до захисту',
        sections=[ReportSection(heading='Тема',text='Пояснення з фактичними даними.')],
    ), basename='defense')
    path = Path(result['files'][0])
    assert path.name == 'defense.docx'
    assert not (path.parent/'report.docx').exists()
    assert 'Пояснення' in '\n'.join(p.text for p in Document(path).paragraphs)
    artifact = db.one("SELECT provenance FROM artifacts WHERE name='defense.docx'")
    assert json.loads(artifact['provenance'])['type'] == 'defense'


def test_imported_dataset_metadata_reads_headers_and_member_names(project):
    import zipfile
    import numpy as np
    from study.documents import import_file
    from study.input_metadata import imported_data_metadata
    path=config.DATA/'arrays.npz'
    np.savez(path,x_train=np.zeros((3,4,4),dtype=np.uint8))
    import_file(path,'data',project['subject'],project['id'])
    archive=config.DATA/'images.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('Training/class_a/image.jpg',b'not decoded here')
        z.writestr('Testing/class_b/image.jpg',b'not decoded here')
    import_file(archive,'data',project['subject'],project['id'])
    metadata=json.loads(imported_data_metadata(project['id']))
    arrays=next(d for d in metadata if d['name']=='arrays.npz')
    assert arrays['array_headers'][0]['arrays']['x_train']['shape']==[3,4,4]
    members=next(d for d in metadata if d['name']=='images.zip')
    assert members['member_count']==2
    assert 'Training/class_a' in members['second_directories']
