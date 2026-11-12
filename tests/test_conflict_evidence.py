import copy
import pytest
from study import config,documents,engine
from study.schemas import Plan,PlanDraft,ConflictPair


def candidate(plan):
    result=copy.deepcopy(plan)
    result['steps'].append({'title':'Звіт','instruction':'Зібрати','kind':'report','expected':'DOCX/PDF'})
    return result


def test_unsupported_conflict_is_not_offered_as_a_generated_approval_block(plan):
    data=candidate(plan)
    data['conflicts']=['No report sample supplied']
    with pytest.raises(ValueError,match='conflict_evidence'):
        engine.validate_generated_plan(Plan.model_validate(data))
    data['conflicts']=[];data['warnings']=['OCR needs verification']
    engine.validate_generated_plan(Plan.model_validate(data))


def test_conflict_quotes_are_checked_against_real_selected_sources(project,plan):
    data=candidate(plan)
    refs=[]
    for number,text in enumerate(('Оформити звіт шрифтом Arial.','Оформити звіт шрифтом Times New Roman.')):
        path=config.DATA/f'source{number}.txt';path.write_text(text,'utf-8')
        did=documents.import_file(path,'task',project['subject'])
        refs.append({'document_id':did,'locator':'Текст','excerpt':text})
    data['conflicts']=['Two mandatory fonts disagree']
    data['conflict_evidence']=[{'conflict_index':0,'source_a':refs[0],'source_b':refs[1]}]
    parsed=Plan.model_validate(data)
    engine.validate_generated_plan(parsed)
    engine.validate_plan_sources(project['id'],parsed)
    parsed.conflict_evidence[0].source_b.excerpt='Оформити звіт шрифтом Comic Sans.'
    with pytest.raises(ValueError,match='непідтверджене'):
        engine.validate_plan_sources(project['id'],parsed)


def test_same_excerpt_twice_does_not_prove_conflict(plan):
    data=candidate(plan)
    source={'document_id':'same','locator':'Текст','excerpt':'One instruction'}
    data['conflicts']=['Claimed disagreement']
    data['conflict_evidence']=[{'conflict_index':0,'source_a':source,'source_b':source}]
    with pytest.raises(ValueError,match='однакові'):
        engine.validate_generated_plan(Plan.model_validate(data))


def test_analysis_keeps_real_conflict_and_its_separately_verified_quotes(project,plan,monkeypatch):
    from study import db
    data=candidate(plan);data.pop('sources');data['conflicts']=['Two mandatory fonts disagree']
    monkeypatch.setattr(engine,'context',lambda *a,**k:('',[]))
    refs=[]
    for number,text in enumerate(('Arial required.','Times New Roman required.')):
        path=config.DATA/f'font{number}.txt';path.write_text(text,'utf-8')
        did=documents.import_file(path,'task',project['subject'])
        refs.append({'document_id':did,'locator':'Текст','excerpt':text})
    def generate(self,prompt,schema):
        if schema is PlanDraft:return PlanDraft.model_validate(data)
        assert schema is ConflictPair
        return ConflictPair(source_a=refs[0],source_b=refs[1])
    monkeypatch.setattr(engine.OllamaProvider,'generate',generate)
    engine.analyse(project['id'])
    saved=db.project(project['id'])
    assert saved['plan']['conflicts']==data['conflicts']
    assert saved['plan']['conflict_evidence'][0]['source_b']==refs[1]
    assert saved['approved_hash'] is None
    with pytest.raises(ValueError,match='суперечності'):
        engine.approve(project['id'])


def test_false_conflict_is_replanned_as_warning_without_operator_edits(project,plan,monkeypatch,patch_reply):
    from study import db
    from study.plan_patches import PlanPatch, PlanEdit
    data=candidate(plan);data.pop('sources');calls=[]
    monkeypatch.setattr(engine,'context',lambda *a,**k:('',[]))
    def generate(self,prompt,schema):
        if schema is PlanPatch:
            calls.append(prompt)
            change = patch_reply(prompt, path='/conflicts', value=[])
            change.edits.append(PlanEdit(op='replace', path='/warnings', before_json='[]', after_json='["Style chosen by assistant"]'))
            return change
        if schema is PlanDraft:
            calls.append(prompt)
            return PlanDraft.model_validate(data|{'conflicts':['No style sample'] if len(calls)==1 else [],'warnings':[] if len(calls)==1 else ['Style chosen by assistant']})
        return ConflictPair(source_a={'document_id':'','locator':'','excerpt':''},source_b={'document_id':'','locator':'','excerpt':''})
    monkeypatch.setattr(engine.OllamaProvider,'generate',generate)
    engine.analyse(project['id'])
    saved=db.project(project['id'])
    assert len(calls)==2 and 'двох явних вимог' in calls[1]
    assert saved['plan']['conflicts']==[]
    assert saved['plan']['warnings']==['Style chosen by assistant']
    assert saved['approved_hash'] is None
