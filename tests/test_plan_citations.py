import pytest
from study import config,documents,engine
from study.schemas import Plan


def cited(project,plan,text,quote):
    path=config.DATA/'source.txt';path.write_text(text,'utf-8')
    did=documents.import_file(path,'task',project['subject'])
    plan['sources']=[{'document_id':did,'locator':'Текст, короткий опис','excerpt':quote}]
    from study import db
    row=db.one('SELECT locator FROM chunks WHERE document_id=?',(did,))
    plan['sources'][0]['locator']=row['locator']+', короткий опис'
    return Plan.model_validate(plan)


def test_citation_whitespace_and_locator_annotation_are_canonicalized(project,plan):
    text='Завдання: виміряти\nвідстань 20 см.'
    p=cited(project,plan,text,'Завдання: виміряти відстань 20 см.')
    engine.validate_plan_sources(project['id'],p)
    from study import db
    actual=db.one('SELECT content FROM chunks WHERE document_id=?',(p.sources[0].document_id,))['content']
    assert p.sources[0].excerpt==actual
    assert p.sources[0].locator=='Текст'


@pytest.mark.parametrize('quote',['Завдання: виміряти відстань 25 см.','Завдання: порахувати відстань 20 см.',''])
def test_citation_does_not_repair_invented_numbers_or_words(project,plan,quote):
    p=cited(project,plan,'Завдання: виміряти\nвідстань 20 см.',quote)
    with pytest.raises(ValueError,match='непідтверджене'):
        engine.validate_plan_sources(project['id'],p)


def test_long_review_explanation_preserves_blocking_decision():
    from study.schemas import ContentReview
    review=ContentReview(blocking_issues=['Incorrect formula'],notes=['Note'],explanation='a'*3000)
    assert len(review.explanation)==2000
    assert not review.satisfied and review.blocking_issues==['Incorrect formula']
    assert review.notes==['Note']


def test_generated_plan_uses_retrieved_source_metadata_not_model_guesses(project,plan,monkeypatch):
    from study import db
    path=config.DATA/'task.txt'
    path.write_text('Завдання до лабораторної роботи\nОбчислити середнє чисел 2, 4, 6.','utf-8')
    did=documents.import_file(path,'task',project['subject'])
    plan['steps'].append(dict(plan['steps'][0],title='Звіт',kind='report',expected='DOCX PDF'))
    plan['sources']=[{'document_id':'invented','locator':'invented','excerpt':'Invented quote'}]
    monkeypatch.setattr(engine.OllamaProvider,'generate',lambda self,prompt,schema=None:Plan.model_validate(plan | {"artifact_contract_version":1}))
    engine.analyse(project['id'])
    saved=db.project(project['id'])
    assert saved['plan']['sources']
    assert all(s['document_id']==did for s in saved['plan']['sources'])
    assert all('Invented' not in s['excerpt'] for s in saved['plan']['sources'])
