import json

import pytest

from study import config, db, documents, engine
from study.planning_sources import planning_sources, save_receipt, extend_coverage
from study.requirements_context import clarification_passages


def source(project, name, role, chunks):
    path = config.DATA / name
    path.write_text('original source', encoding='utf-8')
    did = documents.import_file(path, role, project['subject'], project['id'])
    db.execute('DELETE FROM chunks WHERE document_id=?', (did,))
    for locator, content in chunks:
        db.execute('INSERT INTO chunks(document_id,locator,content) VALUES(?,?,?)', (did, locator, content))
    return did


@pytest.mark.parametrize('heading', ['Homework requirements', 'Завдання до лабораторної роботи', 'Aufgaben'])
def test_all_short_task_text_reaches_last_page_without_language_ranking(project, heading):
    did = source(project, 'task.txt', 'task', [('Page 1', heading), ('Page 5', 'Final mandatory deliverable Ω')])
    text, refs, receipt = planning_sources(project['id'], 'Unrelated query')
    assert heading in text and 'Final mandatory deliverable Ω' in text
    assert not receipt['blockers']
    record = receipt['documents'][0]
    assert record['coverage'] == 'complete indexed text'
    assert record['omitted_chunk_count'] == 0
    assert {ref['document_id'] for ref in refs} == {did}


def test_library_pdf_is_not_claimed_as_runtime_file_and_ocr_warnings_remain(project):
    did = source(project, 'task.txt', 'task', [('Page 1', 'OCR uncertain formula')])
    db.execute('UPDATE documents SET warnings=? WHERE id=?', (json.dumps(['OCR/formula needs review']), did))
    text, _, receipt = planning_sources(project['id'], '')
    record = receipt['documents'][0]
    assert record['workspace_path'] is None
    assert record['warnings'] == ['OCR/formula needs review']
    assert 'not a runtime input file' in text
    assert 'not image/formula/OCR completeness' in text


def test_opaque_data_file_is_available_but_not_read_as_text(project):
    did = source(project, 'input.txt', 'data', [])
    text, _, receipt = planning_sources(project['id'], '')
    record = receipt['documents'][0]
    assert record['workspace_path'] == f'inputs/{did}/input.txt'
    assert record['indexed_chunks'] == 0
    assert record['coverage'] == 'not included / no indexed text'
    assert not receipt['blockers']


def test_oversized_primary_stops_before_any_model_call_and_preserves_receipt(project, monkeypatch):
    source(project, 'large.txt', 'task', [('Page 1', 'x' * 45000)])
    monkeypatch.setattr(engine.OllamaProvider, 'generate', lambda *a, **k: pytest.fail('No model call'))
    with pytest.raises(ValueError, match='budget'):
        engine.analyse(project['id'])
    receipts = list((config.project_dir(project['id']) / 'planning-coverage').glob('*.json'))
    assert len(receipts) == 1
    record = json.loads(receipts[0].read_text(encoding='utf-8'))
    assert record['blockers'] and record['documents'][0]['omitted_chunk_count'] == 1
    assert not db.query('SELECT id FROM cloud_requests')


def test_missing_index_stops_instead_of_claiming_missing_upload(project):
    source(project, 'unread.txt', 'task', [])
    _, _, receipt = planning_sources(project['id'], '')
    assert 'Немає індексованого тексту' in receipt['blockers'][0]
    assert 'не повторне завантаження' in receipt['blockers'][0]


def test_theory_budget_is_explicit_and_requested_pages_are_prioritized(project):
    source(project, 'task.txt', 'task', [('Page 1', 'Read pages 14 and 34 in theory')])
    source(project, 'theory.txt', 'theory', [(f'Page {i}', f'Lecture topic {i} ' + 'x' * 300) for i in range(1, 50)])
    text, _, receipt = planning_sources(project['id'], 'Read pages 14 and 34', theory_budget=1600)
    record = next(d for d in receipt['documents'] if d['role'] == 'theory')
    assert 'Lecture topic 14 ' in text and 'Lecture topic 34 ' in text
    assert record['coverage'] == 'partial indexed text'
    assert record['omitted_chunk_count'] > 0
    assert len(text) <= receipt['budget_characters']


def test_multiple_short_tasks_and_theory_notes_are_fully_included(project):
    for name, role in [('first.txt', 'task'), ('second.txt', 'task'), ('notes.txt', 'theory')]:
        source(project, name, role, [('Page 1', 'Mandatory text ' + name)])
    _, _, receipt = planning_sources(project['id'], '')
    assert not receipt['blockers']
    assert all(d['coverage'] == 'complete indexed text' for d in receipt['documents'])


def test_scope_excludes_other_project_and_superseded_sources(project, client):
    included = source(project, 'allowed.txt', 'task', [('Page 1', 'allowed evidence')])
    old = source(project, 'old.txt', 'task', [('Page 1', 'obsolete evidence')])
    db.execute('UPDATE documents SET status=? WHERE id=?', ('superseded', old))
    other = client.post('/api/projects', json={'title': 'Other', 'subject': 'Other', 'task': 'Other', 'variant': ''}).json()
    source(other, 'other.txt', 'task', [('Page 1', 'private other-project evidence')])
    text, refs, receipt = planning_sources(project['id'], '')
    assert {r['document_id'] for r in refs} == {included}
    assert len(receipt['documents']) == 1 and 'obsolete evidence' not in text
    assert 'private other-project evidence' not in text


def test_same_page_other_chunk_can_be_read_without_repeating_known_chunk(project):
    source(project, 'theory.txt', 'theory', [('Page 14', 'Known rules first'), ('Page 14', 'Additional rules second')])
    _, refs, receipt = planning_sources(project['id'], '', theory_budget=120)
    # Supply an exact known excerpt, rather than considering its whole page read.
    did = receipt['documents'][0]['document_id']
    known = [{'document_id': did, 'locator': 'Page 14', 'excerpt': 'Known rules first'}]
    text, additional = clarification_passages(project['id'], ['Read page 14'], known)
    assert 'Additional rules second' in text and 'Known rules first' not in text
    extend_coverage(receipt, known + additional)
    assert receipt['documents'][0]['coverage'] == 'complete indexed text'
    assert clarification_passages(project['id'], ['Read page 14'], known + additional) == ('', [])


def test_coverage_receipt_is_content_addressed_and_deterministic(project):
    source(project, 'task.txt', 'task', [('Page 1', 'Required text')])
    one = planning_sources(project['id'], '')
    two = planning_sources(project['id'], '')
    assert one == two
    first = save_receipt(project['id'], one[2])
    assert save_receipt(project['id'], two[2]) == first
    assert len(list(first.parent.glob('*.json'))) == 1


@pytest.mark.parametrize('query', ['сторінках14 і34', 'pages 14 and 34', 'сторінки 14–16'])
def test_page_requests_work_with_compact_ukrainian_and_english(query):
    from study.planning_sources import _pages
    assert _pages(query) == ({14, 15, 16} if '–' in query else {14, 34})


def test_analysis_reads_omitted_theory_and_records_the_new_attempt(project, plan, monkeypatch,patch_reply):
    import copy
    from study.schemas import PlanDraft

    source(project, 'lecture.txt', 'theory', [(f'Page {i}', f'Lecture {i} ' + 'x' * 500) for i in range(1, 40)]
           + [('Page 99', 'EXTRA_REQUIRED_THEORY ' + 'y' * 5800)])
    candidate = copy.deepcopy(plan)
    candidate.pop('sources')
    candidate['steps'].append({'title': 'Звіт', 'instruction': 'Зібрати', 'kind': 'report', 'expected': 'DOCX/PDF'})
    calls = []
    def generate(self, prompt, schema):
        calls.append(prompt)
        result = copy.deepcopy(candidate)
        if len(calls) == 1:
            assert 'EXTRA_REQUIRED_THEORY' not in prompt
            result['missing'] = ['Read page 99 of lecture.txt']
        else:
            assert 'EXTRA_REQUIRED_THEORY' in prompt
            assert 'Повторно склади повний план' not in prompt
            return patch_reply(prompt, path='/missing', value=[])
        return PlanDraft.model_validate(result)
    monkeypatch.setattr(engine.OllamaProvider, 'generate', generate)
    engine.analyse(project['id'])
    assert len(calls) == 2
    assert db.project(project['id'])['plan']['missing'] == []
    records = [json.loads(path.read_text(encoding='utf-8')) for path in
               (config.project_dir(project['id']) / 'planning-coverage').glob('*.json')]
    assert {r.get('attempt') for r in records} == {None, 1, 2}
    assert next(r for r in records if r.get('attempt') == 2)['prompt_sha256']


def test_author_and_reviewer_receive_identical_source_bundle(project, plan, monkeypatch):
    import copy
    from study import assistance
    from study.schemas import Assessment, PlanDraft

    source(project, 'task.txt', 'task', [('Page 1', 'Mandatory source text')])
    bundle = planning_sources(project['id'], project['task'])[0]
    candidate = copy.deepcopy(plan)
    candidate.pop('sources')
    candidate['steps'].append({'title': 'Звіт', 'instruction': 'Зібрати', 'kind': 'report', 'expected': 'DOCX/PDF'})
    prompts = []
    def generate(self, prompt, schema, **kwargs):
        prompts.append(prompt)
        assert bundle in prompt
        return PlanDraft.model_validate(candidate) if schema is PlanDraft else Assessment(satisfied=True, explanation='Source facts verified')
    monkeypatch.setattr(assistance.BudgetedModel, 'available', lambda self: True)
    monkeypatch.setattr(assistance.BudgetedModel, 'generate', generate)
    monkeypatch.setattr(assistance.BudgetedModel, 'remote_with_fallback', generate)
    engine.analyse(project['id'])
    assert len(prompts) == 2
    assert db.project(project['id'])['approved_hash'] is None


def test_identical_excerpt_prefixes_on_one_page_do_not_fake_full_coverage(project):
    did = source(project, 'theory.txt', 'theory', [('Page 14', 'x' * 300 + ' FIRST'), ('Page 14', 'x' * 300 + ' SECOND')])
    rows = db.query('SELECT id FROM chunks WHERE document_id=? ORDER BY id', (did,))
    _, _, receipt = planning_sources(project['id'], '', theory_budget=0)
    known = [{'document_id': did, 'locator': 'Page 14', 'excerpt': 'x' * 300, 'chunk_id': rows[0]['id']}]
    extend_coverage(receipt, known)
    assert receipt['documents'][0]['omitted_chunk_count'] == 1
    text, refs = clarification_passages(project['id'], ['Read page 14'], known)
    assert 'SECOND' in text and 'FIRST' not in text
    assert refs[0]['chunk_id'] == rows[1]['id']


def test_approved_source_pins_are_respected(project):
    allowed = source(project, 'allowed.txt', 'task', [('Page 1', 'pinned evidence')])
    source(project, 'unapproved.txt', 'task', [('Page 1', 'unapproved evidence')])
    db.execute('UPDATE projects SET approved_hash=? WHERE id=?', ('approved', project['id']))
    db.execute('INSERT INTO source_pins(project_id,document_id) VALUES(?,?)', (project['id'], allowed))
    text, refs, receipt = planning_sources(project['id'], '')
    assert 'pinned evidence' in text and 'unapproved evidence' not in text
    assert len(receipt['documents']) == 1


def test_planning_facts_explain_registry_and_immutable_file_contracts():
    from study.planning_facts import planning_facts
    facts = json.loads(planning_facts())
    assert '@scope/name' in facts['dependency_specs']['javascript']
    assert 'typescript@5.7.3' in facts['dependency_specs']['typescript']
    paths = facts['execution_scope']['artifact_contract']['paths']
    assert 'FILE paths only' in paths and 'immutable' in paths and 'NEW output path' in paths
