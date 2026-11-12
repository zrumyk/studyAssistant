"""Bounded, language-neutral indexed-text coverage for planning (not PDF certification)."""
import hashlib
import json
import re

from . import config, db
from .search import scope


def _block(row):
    return f"[ДЖЕРЕЛО {row['document_id']} | {row['name']} | {row['locator']} | роль={row['role']}]\n{row['content']}"


def _pages(query):
    pages = set()
    for match in re.finditer(r'(?:сторін[^\W\d_]*|pages?|с\.)\s*(\d+(?:\s*(?:[,і&]|and|[-–])\s*\d+)*)', query, re.I):
        values = [int(n) for n in re.findall(r'\d+', match[1])]
        pages.update(values[:20])
        if len(values) == 2 and re.search('[-–]', match[1]) and 0 <= values[1] - values[0] <= 8:
            pages.update(range(values[0], values[1] + 1))
    return pages


def planning_sources(pid, query, *, budget=40000, theory_budget=9000):
    """Read all indexed task chunks or stop before a model call; bound other roles.

    Complete indexed text does not imply complete OCR, image or original-file reading.
    The returned receipt is also saved by analyse, including on a pre-model stop.
    """
    condition, args = scope(pid)
    docs = db.query('SELECT d.id,d.name,d.role,d.status,d.warnings FROM documents d WHERE '
                    + condition + ' ORDER BY d.id LIMIT 65', args)
    receipt = {'version': 1, 'budget_characters': budget, 'documents': [], 'blockers': [],
               'limitation': 'Coverage of indexed text only, not image/formula/OCR completeness or model understanding.'}
    if len(docs) > 64:
        receipt['blockers'].append('Забагато джерел для обмеженого аналізу: понад64; уточніть вибір матеріалів.')
        docs = docs[:64]
    rows_by_doc = {}
    records = {}
    for doc in docs:
        rows = db.query('SELECT id,document_id,locator,content FROM chunks WHERE document_id=? ORDER BY id LIMIT 10001', (doc['id'],))
        rows = [dict(row, name=doc['name'], role=doc['role']) for row in rows]
        rows_by_doc[doc['id']] = rows
        source = config.project_dir(pid) / 'workspace' / 'inputs' / doc['id'] / doc['name']
        base = config.project_dir(pid) / 'workspace'
        readable = (source.is_file() and not source.is_symlink()
                    and source.resolve().is_relative_to(base.resolve()))
        record = {'document_id': doc['id'], 'name': doc['name'], 'role': doc['role'],
                  'status': doc['status'], 'indexed_chunks': len(rows),
                  'indexed_characters': sum(len(row['content']) for row in rows),
                  'index_sha256': hashlib.sha256(json.dumps([(r['id'], r['locator'], r['content']) for r in rows], ensure_ascii=False).encode()).hexdigest(),
                  'workspace_path': source.relative_to(base).as_posix() if readable else None,
                  'availability': 'workspace file' if readable else 'library document; not a runtime input file',
                  'warnings': json.loads(doc['warnings'] or '[]'), 'included_chunk_ids': []}
        records[doc['id']] = record
        receipt['documents'].append(record)
        if len(rows) > 10000:
            receipt['blockers'].append('Перевищено межу10000 фрагментів джерела: ' + doc['name'])
    # Leave room for the availability/coverage receipt; never silently cut a chunk.
    content_budget = max(0, budget - 7000)
    blocks = []
    refs = []
    used = 0

    def add(row, limit):
        nonlocal used
        block = _block(row)
        cost = len(block) + 2
        if used + cost > limit:
            return False
        blocks.append(block)
        used += cost
        records[row['document_id']]['included_chunk_ids'].append(row['id'])
        refs.append({'document_id': row['document_id'], 'locator': row['locator'], 'chunk_id': row['id'],
                     'excerpt': row['content'][:300]})
        return True

    # No heading/language heuristic can discard a task's final requirements.
    for doc in docs:
        if doc['role'] != 'task':
            continue
        rows = rows_by_doc[doc['id']]
        if not rows:
            receipt['blockers'].append('Немає індексованого тексту умови: ' + doc['name'] + '; потрібне читання/OCR, не повторне завантаження наявного файла.')
        for row in rows:
            if not add(row, content_budget):
                receipt['blockers'].append('Повний індексований текст умови не вміщується в budget: ' + doc['name'] + '; потрібне поетапне читання до планування.')
                break
    terms = set(re.findall(r'[^\W\d_]{4,}', query.casefold()))
    pages = _pages(query)
    theory_end = min(content_budget, used + theory_budget)
    auxiliary = [d for d in docs if d['role'] in ('theory', 'reference')]
    auxiliary.sort(key=lambda d: (d['role'] != 'theory', records[d['id']]['indexed_characters'], d['id']))
    for doc in auxiliary:
        rows = rows_by_doc[doc['id']]
        per_doc_end = min(theory_end, used + 5500)
        def rank(row):
            page = re.search(r'(?:сторінка|page)\s*(\d+)\b', row['locator'], re.I)
            return (-(1000 if page and int(page[1]) in pages else 0)
                    - sum(term in row['content'].casefold() for term in terms), row['id'])
        # Short notes keep natural order. Long theory uses explicit pages and lexical relevance.
        if sum(len(_block(row)) + 2 for row in rows) > per_doc_end - used:
            rows = sorted(rows, key=rank)
        for row in rows:
            add(row, per_doc_end)
    for record in receipt['documents']:
        included = set(record['included_chunk_ids'])
        omitted = [r for r in rows_by_doc[record['document_id']] if r['id'] not in included]
        record['omitted_chunk_count'] = len(omitted)
        record['omitted_locators_sample'] = list(dict.fromkeys(r['locator'] for r in omitted))[:8]
        record['coverage'] = ('complete indexed text' if included and not omitted else
                              'partial indexed text' if included else 'not included / no indexed text')
    receipt['content_characters'] = used
    text = ('РЕЄСТР ДОСТУПНОСТІ ТА ПОКРИТТЯ ДЖЕРЕЛ (дані)\n'
            + json.dumps(receipt, ensure_ascii=False) + '\n\n' + '\n\n'.join(blocks))
    if len(text) > budget:
        receipt['blockers'].append('Реєстр і текст джерел перевищили сумарний budget; аналіз зупинено до запиту моделі.')
    receipt['bundle_characters'] = len(text)
    return text, refs, receipt


def save_receipt(pid, receipt):
    folder = config.project_dir(pid) / 'planning-coverage'
    folder.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
    path = folder / (hashlib.sha256(encoded.encode()).hexdigest() + '.json')
    if not path.exists():
        path.write_text(encoded, encoding='utf-8', newline='\n')
    db.event(pid, 'planning_source_coverage', {'path': str(path.relative_to(config.DATA)),
                                             'blockers': receipt['blockers']})
    return path


def extend_coverage(receipt, references):
    """Update counts for the exact additional chunks actually appended to prompts."""
    for record in receipt['documents']:
        rows = db.query('SELECT id,locator,content FROM chunks WHERE document_id=? ORDER BY id',
                        (record['document_id'],))
        included = set(record['included_chunk_ids'])
        for row in rows:
            if any(ref['document_id'] == record['document_id'] and ref['locator'] == row['locator']
                   and (ref['chunk_id'] == row['id'] if 'chunk_id' in ref else
                        ref.get('excerpt') == row['content'][:300]) for ref in references):
                included.add(row['id'])
        record['included_chunk_ids'] = sorted(included)
        omitted = [row for row in rows if row['id'] not in included]
        record['omitted_chunk_count'] = len(omitted)
        record['omitted_locators_sample'] = list(dict.fromkeys(row['locator'] for row in omitted))[:8]
        record['coverage'] = ('complete indexed text' if included and not omitted else
                              'partial indexed text' if included else 'not included / no indexed text')
