import json
import pytest
from pydantic import ValidationError
from study import config, db, cloud
from study.assistance import BudgetedModel
from study.structured_review import report_review
from study.providers import ModelOutputTruncated


class Local:
    def generate(self,*a,**k): raise AssertionError('No hidden local fallback')


def setup(monkeypatch, parent='{"broken":', child=None, complete=True):
    config.save_settings({'cloud_consent_mode':'budgeted','cloud_provider':'deepseek','cloud_model':'deepseek-flash','cloud_input_price':.30,'cloud_output_price':1.20,'cloud_prices_date':db.now()[:10]})
    calls=[]
    def send(rid):
        calls.append(rid)
        text=parent if len(calls)==1 else child
        db.execute("UPDATE cloud_requests SET state='done',response=?,actual=.001 WHERE id=?",(text,rid))
        return {'text':text,'complete':complete,'cost_usd':.001}
    monkeypatch.setattr(cloud,'send',send)
    return calls


def test_one_distinct_durable_child_retains_negative_verdict_and_parent(project,monkeypatch):
    calls=setup(monkeypatch,child='{"blocking_issues":["wrong measured value"],"notes":[],"explanation":"reject"}')
    for _ in range(2):
        result=report_review(BudgetedModel(project['id'],Local(),'report'),'facts and draft')
        assert not result.satisfied
    assert len(calls)==2
    rows=db.query('select state,response,actual,config from cloud_requests order by created')
    assert rows[0]['response']=='{"broken":' and all(r['state']=='done' for r in rows)
    assert all(json.loads(r['config'])['cloud_request_limit_usd']==.03 for r in rows)


def test_bad_child_never_creates_grandchild_on_restart(project,monkeypatch):
    calls=setup(monkeypatch,child='{"also broken":')
    for _ in range(3):
        with pytest.raises(ValidationError):report_review(BudgetedModel(project['id'],Local(),'report'),'same')
    assert len(calls)==2


def test_incomplete_parent_never_opens_json_recovery(project,monkeypatch):
    calls=setup(monkeypatch,complete=False)
    with pytest.raises(ModelOutputTruncated):report_review(BudgetedModel(project['id'],Local(),'report'),'same')
    assert len(calls)==1


def test_semantic_schema_error_is_not_json_recovery(project,monkeypatch):
    calls=setup(monkeypatch,parent='{"blocking_issues":[],"explanation":null}')
    with pytest.raises(ValidationError):report_review(BudgetedModel(project['id'],Local(),'report'),'same')
    assert len(calls)==1


def test_unknown_parent_no_duplicate_and_no_recovery(project,monkeypatch):
    calls=setup(monkeypatch)
    def send(rid):
        calls.append(rid);db.execute("UPDATE cloud_requests SET state='uncertain' WHERE id=?",(rid,));raise RuntimeError('uncertain')
    monkeypatch.setattr(cloud,'send',send)
    for _ in range(2):
        with pytest.raises(RuntimeError):report_review(BudgetedModel(project['id'],Local(),'report'),'same')
    assert len(calls)==1


def test_legacy_accepted_text_is_revalidated_without_regeneration(project,plan,monkeypatch):
    from study import reports,assistance,engine
    from study.schemas import ContentReview
    engine.save_plan(project['id'],plan);engine.approve(project['id'])
    db.execute("UPDATE steps SET status='done' WHERE project_id=?",(project['id'],))
    monkeypatch.setattr('study.search.context',lambda *a,**k:'')
    phase=[False];calls=[]
    monkeypatch.setattr(reports,'report_evidence',lambda p,legacy=False:'old sampled facts' if legacy or not phase[0] else 'complete facts')
    def generate(self,prompt,schema=None):
        calls.append('review' if schema else 'write')
        if schema:return ContentReview(blocking_issues=[],notes=[],explanation='valid')
        assert not phase[0], 'Accepted text must be reused, not regenerated'
        return 'Original accepted text'
    monkeypatch.setattr(assistance.BudgetedModel,'generate',generate)
    def render(self,pid,report):
        assert all(s.text=='Original accepted text' for s in report.sections)
        out=config.project_dir(pid)/'reports/test';out.mkdir(parents=True,exist_ok=True)
        return {'files':[str(out/'report.docx')]}
    monkeypatch.setattr(reports.DocxRenderer,'render',render)
    reports.build_report(project['id']);calls.clear();phase[0]=True
    reports.build_report(project['id'])
    assert calls==['review']*len(plan['report_sections'])
    calls.clear();reports.build_report(project['id']);assert calls==[]


def test_interrupted_intent_does_not_dispatch_another_child(project,monkeypatch):
    calls=setup(monkeypatch)
    m=BudgetedModel(project['id'],Local(),'report')
    with pytest.raises(ValidationError):m.remote('same',__import__('study.schemas',fromlist=['ContentReview']).ContentReview)
    db.event(project['id'],'report_review_json_recovery',{'parent_request_id':m.last_request_id,'limit':1})
    with pytest.raises(RuntimeError,match='невідомий'):report_review(m,'same')
    assert len(calls)==1


def test_completed_negative_parent_never_opens_recovery(project,monkeypatch):
    calls=setup(monkeypatch,parent='{"blocking_issues":["invalid metric"],"notes":[],"explanation":"reject"}')
    assert not report_review(BudgetedModel(project['id'],Local(),'report'),'same').satisfied
    assert len(calls)==1


def test_cancel_before_child_does_not_quote_or_send(project,monkeypatch):
    calls=setup(monkeypatch)
    m=BudgetedModel(project['id'],Local(),'report',cancelled=lambda:len(calls)>0)
    with pytest.raises(RuntimeError):report_review(m,'same')
    assert len(calls)==1
