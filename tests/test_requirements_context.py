from study import config,db,documents
from study.requirements_context import task_passages
from study.requirements_context import clarification_passages


def test_task_passages_include_requirements_beyond_top_k(project):
    path=config.DATA/'lab.txt'
    path.write_text(('Загальна теорія. '*150)+'\nЗавдання до лабораторної роботи\nЗберегти п’ять кадрів.\nЗміст звіту\nПояснити функції.','utf-8')
    documents.import_file(path,'task',project['subject'])
    text=task_passages(project['id'])
    assert 'п’ять кадрів' in text and 'Пояснити функції' in text
    assert 'lab.txt' in text


def test_task_passages_obey_explicit_selection(client,project):
    for name in ('chosen','excluded'):
        path=config.DATA/(name+'.txt')
        path.write_text('Завдання до лабораторної роботи\n'+name,'utf-8')
        did=documents.import_file(path,'task',project['subject'])
        if name=='chosen':
            selected=did
    client.put('/api/projects/'+project['id']+'/sources',json={'document_ids':[selected]})
    text=task_passages(project['id'])
    assert 'chosen' in text and 'excluded' not in text


def test_task_passages_share_budget_between_parts_and_include_variant_row(project):
    db.execute('UPDATE projects SET variant=? WHERE id=?',('1',project['id']))
    first=config.DATA/'part1.txt'
    first.write_text(('Завдання до лабораторної роботи\n'+('Текст першої частини. '*120)+'\n')*20,'utf-8')
    second=config.DATA/'part2.txt'
    second.write_text('№ Завдання Метод Тип даних\n1\nClassificationTarget\nRequiredArchitecture\n','utf-8')
    documents.import_file(first,'task',project['subject'])
    documents.import_file(second,'task',project['subject'])
    text=task_passages(project['id'])
    assert 'part1.txt' in text and 'part2.txt' in text
    assert 'ClassificationTarget' in text and 'RequiredArchitecture' in text


def test_missing_question_reads_requested_pages_without_table_of_contents(project):
    did=db.uid()
    path=config.DATA/'manual.txt'
    path.write_text('source','utf-8')
    did=documents.import_file(path,'task',project['subject'])
    for locator,content in [('Сторінка 3','Зміст\n2.3 Шари ........13\n2.4 Колір ........14\n2.5 Лінії ........15'),('Сторінка 13','Імена шарів\nДозволено обирати зрозумілі імена.'),('Сторінка 14','Призначення кольорів\nКожному шару призначається колір.')]:
        db.execute('INSERT INTO chunks(document_id,locator,content) VALUES(?,?,?)',(did,locator,content))
    text,refs=clarification_passages(project['id'],['Чи є перелік шарів на сторінках 13–14?'])
    assert 'Дозволено обирати' in text and 'Кожному шару' in text
    assert 'Зміст' not in text
    assert {r['locator'] for r in refs}=={'Сторінка 13','Сторінка 14'}
    assert clarification_passages(project['id'],['сторінки 13–14'],refs)==('',[])


def test_missing_question_cannot_read_excluded_tasks(client,project):
    selected=None
    for name in ('allowed','excluded'):
        path=config.DATA/(name+'.txt')
        path.write_text('Імена шарів креслення, типи ліній '+name,'utf-8')
        did=documents.import_file(path,'task',project['subject'])
        if name=='allowed':selected=did
    client.put('/api/projects/'+project['id']+'/sources',json={'document_ids':[selected]})
    text,refs=clarification_passages(project['id'],['Імена шарів креслення типи ліній?'])
    assert 'allowed' in text and 'excluded' not in text
    assert refs and all(r['document_id']==selected for r in refs)
    assert clarification_passages(project['id'],['Імена шарів креслення типи ліній?'],budget=10)==('',[])
