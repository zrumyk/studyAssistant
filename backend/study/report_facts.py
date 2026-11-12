"""Bounded file observations, with explicit coverage; never infer unseen success."""
import csv
import hashlib
import io
import json
from collections import Counter


def _outcome(value):
    value = str(value).strip().lower()
    if value in ('true', 'pass', 'passed', 'ok', 'success'): return 'passed'
    if value in ('false', 'fail', 'failed', 'error'): return 'failed'
    return 'unknown'


def file_facts(path, max_bytes=2000000):
    with path.open('rb') as stream: raw = stream.read(max_bytes+1)
    complete = len(raw) <= max_bytes
    data = raw[:max_bytes]
    result = {'bytes_total': path.stat().st_size, 'bytes_read': len(data),
              'complete': complete, 'sha256': hashlib.sha256(data).hexdigest() if complete else None}
    text = data.decode('utf-8-sig', errors='strict')
    if path.suffix.lower() == '.csv':
        reader = csv.DictReader(io.StringIO(text), strict=True)
        rows = list(reader)
        result.update(header=reader.fieldnames, row_count=len(rows), first_rows=rows[:5])
        fields = [k for k in (reader.fieldnames or []) if k.lower() in ('passed', 'ok', 'success', 'status')]
        result['outcome_counts'] = {k: dict(Counter(_outcome(row.get(k)) for row in rows)) for k in fields}
        result['all_passed'] = bool(complete and rows and fields and all(
            _outcome(row.get(k)) == 'passed' for row in rows for k in fields))
        result['failed_or_unknown_rows'] = [dict(row_index=i+1, values=row) for i,row in enumerate(rows)
            if any(_outcome(row.get(k)) != 'passed' for k in fields)][:8]
        label = next((k for k in reader.fieldnames or [] if k.lower() in ('check','name','id','test')), None)
        if label:
            result['named_outcomes'] = [{label: str(row.get(label,''))[:160], **{k:row.get(k) for k in fields}} for row in rows[:60]]
            result['named_outcomes_complete'] = len(rows) <= 60 and complete
    elif path.suffix.lower() == '.jsonl':
        lines = text.splitlines()
        if not complete: lines = lines[:-1]
        rows = [json.loads(line) for line in lines if line.strip()]
        records = [row for row in rows if isinstance(row,dict)]
        result.update(record_count=len(rows), object_record_count=len(records),
            exit_code_counts=dict(Counter(str(row.get('exit_code','missing')) for row in records)),
            timed_out_count=sum(row.get('timed_out') is True for row in records),
            timed_out_missing=sum('timed_out' not in row for row in records),
            cases=[{k: row[k] for k in ('argv','exit_code','timed_out','db_changed') if k in row} for row in records[:24]],
            cases_complete=complete and len(records)<=24)
    else:
        if not complete:
            result['warning'] = 'File exceeds bounded read; contents not parsed or generalized'
        else:
            value = json.loads(text)
            from .input_inspection import _failures, _shape
            result.update(content=value if len(text)<3500 else _shape(value), failed_records=_failures(value))
    return result


def bounded_facts(path, room):
    try:
        facts = file_facts(path)
    except (ValueError, UnicodeError, csv.Error) as error:
        facts = {'complete': False, 'warning': 'Unparsed file: '+str(error)[:160]}
    for key in ('first_rows', 'content', 'cases', 'named_outcomes', 'failed_or_unknown_rows'):
        encoded = json.dumps(facts, ensure_ascii=False)
        if len(encoded) <= room: return encoded
        if key in facts:
            facts.pop(key)
            facts['omitted_details'] = True
    encoded = json.dumps(facts, ensure_ascii=False)
    return encoded if len(encoded)<=room else json.dumps({'complete':False,'warning':'No room for file facts; do not infer its outcomes'})
