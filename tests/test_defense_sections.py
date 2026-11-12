import copy
import pytest
from study import config, db
from study.defense_sections import reviewed_section
from study.providers import ModelOutputTruncated
from study.schemas import ContentReview


class Model:
    last_source = 'cloud/test'
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.last_request_id = None
    def generate(self, prompt, schema=None):
        self.calls.append(prompt)
        self.last_request_id = f'receipt-{id(self)}-{len(self.calls)}'
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def accepted():
    return ContentReview(blocking_issues=[], explanation='Checked against actual evidence')


def test_verbose_review_keeps_all_notes_and_blocking_decisions():
    notes=[f'Note {i}' for i in range(12)]
    review=ContentReview(blocking_issues=[],notes=notes,explanation='No blocking issue')
    assert review.satisfied and len(review.notes)==8
    assert all(note in '\n'.join(review.notes) for note in notes)
    blocked=ContentReview(blocking_issues=notes,explanation='Actual defects')
    assert not blocked.satisfied and all(note in '\n'.join(blocked.blocking_issues) for note in notes)


def run(project, state, model, reviewer, save=lambda:None, prompt='Actual data: mean=4', cancelled=lambda:False):
    return reviewed_section(project['id'], 'Results', prompt, ['Results'], state, save, model, reviewer, cancelled)


def test_truncation_splits_only_current_section_and_records_reviewed_receipts(project):
    state={'sections':{'Earlier':{'text':'Preserved'}}}
    model=Model([ModelOutputTruncated('output cap'), 'First half.', 'Second half.'])
    reviewer=Model([accepted(), accepted(), accepted()])
    saves=[]
    assert run(project,state,model,reviewer,lambda:saves.append(copy.deepcopy(state)))=='First half.\n\nSecond half.'
    assert len(model.calls)==3 and len(reviewer.calls)==3
    assert state['sections']['Earlier']['text']=='Preserved'
    progress=state['section_progress']['Results']
    assert len(progress['receipts'])==6
    assert all(progress['parts'][part]['review_request_id'] for part in ('1','2'))
    assert all(progress['parts'][part]['context_sha256'] for part in ('1','2'))
    assert 'Second half' not in state['sections']['Results']['review']['explanation']
    assert run(project,state,Model([]),Model([]))=='First half.\n\nSecond half.'


def test_restart_reuses_first_half_and_does_not_rebill_completed_generation(project):
    state={'sections':{}}
    model=Model([ModelOutputTruncated('cap'), 'Accepted first.', RuntimeError('network uncertain')])
    reviewer=Model([accepted()])
    with pytest.raises(RuntimeError,match='uncertain'):run(project,state,model,reviewer)
    restored=copy.deepcopy(state)
    next_model=Model(['Completed second.']);next_reviewer=Model([accepted(),accepted()])
    assert run(project,restored,next_model,next_reviewer)=='Accepted first.\n\nCompleted second.'
    assert len(next_model.calls)==1 and 'другу' in next_model.calls[0]


def test_second_truncation_is_terminal_across_restarts(project):
    state={'sections':{}}
    with pytest.raises(RuntimeError,match='Повторне обрізання'):
        run(project,state,Model([ModelOutputTruncated('whole cap'),ModelOutputTruncated('part cap')]),Model([]))
    assert 'Results' not in state['sections']
    with pytest.raises(RuntimeError,match='автоматичні повтори'):
        run(project,copy.deepcopy(state),Model([]),Model([]))


def test_uncertain_transport_error_never_triggers_split(project):
    state={'sections':{}}
    model=Model([RuntimeError('uncertain provider result')])
    with pytest.raises(RuntimeError,match='uncertain'):run(project,state,model,Model([]))
    assert not state['section_progress']['Results']['split'] and len(model.calls)==1


def test_rejected_merged_section_is_not_renderable(project):
    state={'sections':{}}
    with pytest.raises(RuntimeError,match='Об’єднаний'):
        run(project,state,Model([ModelOutputTruncated('cap'),'Half one','Contradictory half two']),
            Model([accepted(),accepted(),ContentReview(blocking_issues=['Contradiction'],explanation='Wrong')]))
    assert 'Results' not in state['sections']


def test_changed_context_or_profile_invalidates_checkpoint(project):
    state={'sections':{}}
    assert run(project,state,Model(['Old']),Model([accepted()]))=='Old'
    assert run(project,state,Model(['New']),Model([accepted()]),prompt='Actual data: mean=5')=='New'


def test_pause_after_first_half_keeps_it_and_stops_second_request(project):
    state={'sections':{}};paused=[False]
    class Reviewer(Model):
        def generate(self,*a,**kw):
            value=super().generate(*a,**kw);paused[0]=True;return value
    model=Model([ModelOutputTruncated('cap'),'First'])
    with pytest.raises(RuntimeError,match='призупинено'):
        run(project,state,model,Reviewer([accepted()]),cancelled=lambda:paused[0])
    assert len(model.calls)==2
    assert state['section_progress']['Results']['parts']['1']['text']=='First'


def test_old_accepted_section_is_rechecked_without_regeneration(project):
    state={'sections':{'Results':{'text':'Legacy accepted text','review':accepted().model_dump()}}}
    reviewer=Model([accepted()])
    assert run(project,state,Model([]),reviewer)=='Legacy accepted text'
    assert len(reviewer.calls)==1
