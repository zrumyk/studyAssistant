from study import config,db,visual_context


class Page:
    def extract_words(self):
        return [
            {'text':'Варіант','x0':10,'x1':30,'bottom':20},
            {'text':'№001','x0':35,'x1':55,'bottom':20},
            {'text':'Варіант','x0':130,'x1':150,'bottom':20},
            {'text':'№002','x0':155,'x1':175,'bottom':20},
        ]
    images=[
        {'x0':0,'x1':100,'top':50,'bottom':150},
        {'x0':120,'x1':220,'top':50,'bottom':150},
        {'x0':0,'x1':100,'top':200,'bottom':300},
    ]


def test_variant_label_selects_figure_below_matching_label():
    assert visual_context.figure_bbox(Page(),'1')==(0,50,100,150)
    assert visual_context.figure_bbox(Page(),'2')==(120,50,220,150)
    assert visual_context.figure_bbox(Page(),'3') is None


def test_select_only_requested_lab_first_variant_page(project):
    pid=project['id']
    db.execute('UPDATE projects SET variant=?,task=? WHERE id=?',('1','Виконай лабораторну роботу №1',pid))
    db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
        'figure',None,project['subject'],'figures.pdf',str(config.DATA/'figures.pdf'),'original','hash','task','ready','[]','{}',db.now()))
    for page,lab in [(2,1),(9,2)]:
        db.execute('INSERT INTO chunks(document_id,locator,content) VALUES(?,?,?)',(
            'figure',f'Сторінка {page}',f'Лабораторна робота № {lab}\nВаріант № 001'))
    found=visual_context.selected_figures(pid)
    assert len(found)==1 and found[0]['page']==2
    db.execute('UPDATE projects SET task=? WHERE id=?',('Виконай лабораторну роботу №2',pid))
    found=visual_context.selected_figures(pid)
    assert len(found)==1 and found[0]['page']==9


def test_only_appendices_linked_to_task_passages_are_inspected(project,monkeypatch):
    from study import requirements_context
    pid=project['id']
    db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
        'appendix',None,project['subject'],'task.pdf',str(config.DATA/'task.pdf'),'original','hash','task','ready','[]','{}',db.now()))
    for number in (32,33,80):
        db.execute('INSERT INTO chunks(document_id,locator,content) VALUES(?,?,?)',(
            'appendix',f'Сторінка {number}',f'{number}\nДодаток А\nФорма креслення'))
    monkeypatch.setattr(requirements_context,'task_passages',lambda *a,**k:('',[{'document_id':'appendix','locator':'Сторінка 32'}]))
    found=visual_context.selected_figures(pid)
    assert [row['page'] for row in found]==[32,33]
    assert all(row['kind']=='appendix' for row in found)
