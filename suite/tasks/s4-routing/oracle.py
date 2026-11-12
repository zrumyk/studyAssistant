"""Oracle for s4-routing: routes follow directly from the thresholds in the task text."""
import csv
import json

EVENTS = [('a', 'auth', 3), ('b', 'auth', 4), ('c', 'dns', 2), ('d', 'dns', 1), ('e', 'other', 5), ('f', 'other', 0)]
THRESHOLDS = {'auth': 4, 'dns': 2}


def expected():
    return [(e, 'review' if c not in THRESHOLDS else ('alert' if s >= THRESHOLDS[c] else 'archive')) for e, c, s in EVENTS]


def evaluate(files):
    problems = []
    path = files.get('decisions.csv')
    if not path:
        return False, ['decisions.csv не знайдено'], []
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    if not rows or [h.strip() for h in rows[0]] != ['event_id', 'route']:
        problems.append(f'колонки {rows[0] if rows else None}')
    got = [(r[0].strip(), r[1].strip()) for r in rows[1:] if r]
    if got != expected():
        problems.append(f'маршрути {got} замість {expected()}')
    checks = files.get('checks.json')
    if not checks:
        problems.append('checks.json не знайдено')
    else:
        try:
            json.loads(checks.read_text('utf-8-sig'))
        except ValueError:
            problems.append('checks.json не є JSON')
    policy = files.get('routing-policy.json')
    if not policy:
        problems.append('routing-policy.json не знайдено')
    else:
        text = policy.read_text('utf-8-sig')
        if '4' not in text or '2' not in text or 'review' not in text:
            problems.append('політика не містить порогів 4 і 2 та маршруту review')
    return not problems, problems, ['alert', 'review']


def mutate(files, tmp):
    out = dict(files)
    dst = tmp / 'decisions.csv'
    rows = ['event_id,route'] + [f'{e},{r}' for e, r in expected()]
    rows[2] = 'b,archive'
    dst.write_text('\n'.join(rows) + '\n', 'utf-8')
    out['decisions.csv'] = dst
    return out


def reference(tmp):
    d = tmp / 'decisions.csv'
    d.write_text('\n'.join(['event_id,route'] + [f'{e},{r}' for e, r in expected()]) + '\n', 'utf-8')
    c = tmp / 'checks.json'
    c.write_text('{"passed": true}', 'utf-8')
    p = tmp / 'routing-policy.json'
    p.write_text(json.dumps({'thresholds': THRESHOLDS, 'unknown': 'review'}), 'utf-8')
    return {'decisions.csv': d, 'checks.json': c, 'routing-policy.json': p}
