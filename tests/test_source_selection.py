import pytest
from study import config, db, engine, documents, search


def material(name, role, subject='Приклад'):
    path=config.DATA/name
    path.write_text('Умова лабораторної роботи: середнє арифметичне', 'utf-8')
    return documents.import_file(path,role,subject)


def test_selected_task_scope_is_frozen_at_approval(client,project,plan):
    pid=project['id']
    first=material('lab1.txt','task')
    second=material('lab2.txt','task')
    theory=material('lecture.txt','theory')
    r=client.put(f'/api/projects/{pid}/sources',json={'document_ids':[first]})
    assert r.status_code==200
    hits=search.search(pid,'середнє',semantic=False)
    assert {h['document_id'] for h in hits} == {first,theory}
    engine.save_plan(pid,plan)
    engine.approve(pid)
    pins={d['document_id'] for d in db.query('SELECT document_id FROM source_pins WHERE project_id=?',(pid,))}
    assert pins=={first,theory} and second not in pins
    client.put(f'/api/projects/{pid}/sources',json={'document_ids':[second]})
    assert db.project(pid)['approved_hash'] is None
    assert {h['document_id'] for h in search.search(pid,'середнє',semantic=False)}=={second,theory}


def test_source_selection_rejects_other_subject_and_unselected_citation(client,project,plan):
    pid=project['id']
    first=material('lab1.txt','task')
    second=material('lab2.txt','task')
    foreign=material('foreign.txt','task','Інший')
    assert client.put(f'/api/projects/{pid}/sources',json={'document_ids':[foreign]}).status_code==400
    client.put(f'/api/projects/{pid}/sources',json={'document_ids':[first]})
    chunk=db.one('SELECT locator,content FROM chunks WHERE document_id=?',(second,))
    plan['sources']=[{'document_id':second,'locator':chunk['locator'],'excerpt':chunk['content']}]
    with pytest.raises(ValueError,match='вибраних'):
        engine.save_plan(pid,plan)


def test_empty_selection_keeps_theory_and_reset_restores_tasks(client,project):
    pid=project['id']
    first=material('lab1.txt','task')
    theory=material('lecture.txt','theory')
    client.put(f'/api/projects/{pid}/sources',json={'document_ids':[]})
    assert {h['document_id'] for h in search.search(pid,'середнє',semantic=False)}=={theory}
    client.put(f'/api/projects/{pid}/sources',json={'document_ids':None})
    assert {h['document_id'] for h in search.search(pid,'середнє',semantic=False)}=={first,theory}


def test_radicals_and_overbars_have_required_word_math_nodes():
    from docx import Document
    from study.reports import add_report_paragraph
    doc=Document(); warnings=[]
    add_report_paragraph(doc,r'$\sqrt{326.5}$ та $\bar{x}$',warnings)
    assert not warnings
    assert doc._element.xpath('.//m:rad/m:deg')
    assert doc._element.xpath('.//m:rad/m:radPr/m:degHide')
    assert doc._element.xpath('.//m:acc/m:e')
    assert not doc._element.xpath('.//m:groupChr')
