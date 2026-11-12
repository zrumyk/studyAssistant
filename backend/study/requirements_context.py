"""Expose original requirement passages, beyond a small semantic top-k."""
import re
from . import db
from .search import scope

HEADER = re.compile(r'(?im)^\s*(?:\d[\d.]*\s*)?(?:завдання\s*(?::|до|для)|індивідуальн[еі]\s+завдання|зміст\s+звіту|вимоги\s+до\s+(?:звіту|оформлення)|порядок\s+виконання|лабораторна\s+робота\s*№?\s*1\b)')


def task_passages(pid, budget=8000, with_sources=False):
    condition,args=scope(pid)
    rows=db.query('SELECT c.id,c.document_id,c.locator,c.content,d.name FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.role=\'task\' AND '+condition+' ORDER BY d.id,c.id',args)
    variant=db.one('SELECT variant FROM projects WHERE id=?',(pid,))['variant'].strip()
    variant_row=re.compile(r'(?m)^\s*0*'+re.escape(variant)+r'\s+(?!\d\s*$)') if variant.isdigit() else None
    candidates=[]
    for index,row in enumerate(rows):
        score=4*len(HEADER.findall(row['content']))
        if re.search(r'(?i)batch\s*size|оптимізатор|додаткове\s+завдання|варіант\s*1',row['content']):
            score+=2
        nearby='\n'.join(n['content'] for n in rows[max(0,index-1):index+2] if n['document_id']==row['document_id'])
        tabular=re.search(r'(?i)№\s+Завдання|Метод\s+Тип\s+даних|Додаткове\s+завдання|Batch\s+size|Варіант\s*№',nearby)
        if tabular and variant_row and variant_row.search(row['content']):
            score+=8
        if score:
            for neighbor in rows[max(0,index-1):index+2]:
                if neighbor['document_id']==row['document_id']:
                    candidates.append((score,neighbor))
    # Highest ranked complete passages first, with deterministic tie-breaking.
    candidates.sort(key=lambda pair:(-pair[0],pair[1]['document_id'],pair[1]['id']))
    blocks=[]; seen=set(); used=0; omitted=False
    document_count=len({row['document_id'] for _,row in candidates}) or 1
    per_document=max(2400,budget//document_count)
    document_used={}
    references=[]
    for _,row in candidates:
        if row['id'] in seen:
            continue
        seen.add(row['id'])
        block=f"[УМОВА {row['document_id']} | {row['name']} | {row['locator']}]\n{row['content']}"
        spent=document_used.get(row['document_id'],0)
        if used+len(block)>budget or spent+len(block)>per_document:
            omitted=True
            continue
        blocks.append(block); used+=len(block)+2
        document_used[row['document_id']]=spent+len(block)+2
        references.append({'document_id':row['document_id'],'locator':row['locator'],'excerpt':row['content'][:300]})
    if omitted:
        blocks.append('[Не всі уривки вмістилися. Якщо потрібний параметр не знайдено, явно познач його відсутнім; не вигадуй.]')
    text='\n\n'.join(blocks)
    return (text,references) if with_sources else text


def clarification_passages(pid, questions, known_sources=(), budget=10000):
    """Read missing-context candidates before asking the user to supply them.

    Only scoped task/theory documents are eligible. This supplies evidence, never
    answers a question or silently clears a genuinely missing input.
    """
    condition,args=scope(pid)
    rows=db.query("SELECT c.id,c.document_id,c.locator,c.content,d.name FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.role IN ('task','theory') AND "+condition+' ORDER BY d.id,c.id',args)
    known_ids={r['chunk_id'] for r in known_sources if 'chunk_id' in r}
    legacy_known={(r['document_id'],r['locator'],r.get('excerpt','')) for r in known_sources if 'chunk_id' not in r}
    query='\n'.join(questions).casefold()
    from .planning_sources import _pages
    pages=_pages(query)
    ignored={'потріб','джере','вказу','конкр','значе','метод','підтв','повно','текст','надан','уривк','сторі','вибра','корис','можна','немає','мають'}
    terms={word[:5] for word in re.findall(r'[^\W\d_]{4,}',query) if word[:5] not in ignored}
    ranked=[]
    for row in rows:
        if row['id'] in known_ids or any(doc==row['document_id'] and loc==row['locator'] and (not excerpt or row['content'].startswith(excerpt))
                                        for doc,loc,excerpt in legacy_known):
            continue
        content=row['content'].casefold()
        # Tables of contents repeat headings without supplying their substance.
        if len(re.findall(r'\.{5,}',content))>=3:
            continue
        locator_page=re.search(r'(?:сторінка|page)\s+(\d+)\b',row['locator'],re.I)
        explicit=bool(locator_page and int(locator_page.group(1)) in pages)
        score=sum(term in content for term in terms)
        if explicit or score>=3:
            ranked.append((100+score if explicit else score,row))
    ranked.sort(key=lambda pair:(-pair[0],pair[1]['document_id'],pair[1]['id']))
    blocks=[];references=[];used=0
    for _,row in ranked:
        block=f"[ДОДАТКОВЕ ДЖЕРЕЛО {row['document_id']} | {row['name']} | {row['locator']}]\n{row['content']}"
        if used+len(block)+2>budget:
            continue
        blocks.append(block);used+=len(block)+2
        references.append({'document_id':row['document_id'],'locator':row['locator'],'excerpt':row['content'][:300],'chunk_id':row['id']})
        if len(blocks)>=6:
            break
    return '\n\n'.join(blocks),references
