"""Oracle for s1-grades: recompute group statistics from the input CSV."""
import csv
import statistics
from pathlib import Path

HERE = Path(__file__).parent


def expected():
    groups = {}
    with open(HERE / 'files' / 'grades.csv', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            groups.setdefault(row['group'], []).append(float(row['score']))
    out = {}
    for g in sorted(groups):
        xs = groups[g]
        out[g] = {'n': len(xs), 'mean': round(statistics.mean(xs), 2), 'median': round(statistics.median(xs), 2),
                  'std': round(statistics.stdev(xs), 2), 'below_60': sum(x < 60 for x in xs)}
    return out


def evaluate(files):
    path = files.get('group_stats.csv')
    if not path:
        return False, ['group_stats.csv не знайдено'], []
    exp = expected()
    problems = []
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    header = list(rows[0].keys()) if rows else []
    if [h.strip() for h in header] != ['group', 'n', 'mean', 'median', 'std', 'below_60']:
        problems.append(f'колонки {header}')
    got = {r['group'].strip(): r for r in rows}
    if sorted(got) != sorted(exp):
        problems.append(f'групи {sorted(got)} замість {sorted(exp)}')
    if [r['group'].strip() for r in rows] != sorted(got):
        problems.append('рядки не впорядковано за групою')
    for g, e in exp.items():
        r = got.get(g)
        if not r:
            continue
        for k, v in e.items():
            try:
                actual = float(r[k])
            except (KeyError, ValueError):
                problems.append(f'{g}.{k} не число'); continue
            if abs(actual - v) > 0.011:
                problems.append(f'{g}.{k}={actual}, очікується {v}')
    best = max(exp, key=lambda g: exp[g]['mean'])
    keys = [f"{exp[best]['mean']:.2f}", str(exp[best]['n'])]
    return not problems, problems, keys


def mutate(files, tmp):
    src = files['group_stats.csv']
    lines = src.read_text('utf-8-sig').splitlines()
    parts = lines[1].split(',')
    parts[2] = str(round(float(parts[2]) + 1.0, 2))
    lines[1] = ','.join(parts)
    dst = tmp / 'group_stats.csv'
    dst.write_text('\n'.join(lines) + '\n', 'utf-8')
    return {'group_stats.csv': dst}


def reference(tmp):
    exp = expected()
    dst = tmp / 'group_stats.csv'
    with open(dst, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['group', 'n', 'mean', 'median', 'std', 'below_60'])
        for g, e in exp.items():
            w.writerow([g, e['n'], f"{e['mean']:.2f}", f"{e['median']:.2f}", f"{e['std']:.2f}", e['below_60']])
    return {'group_stats.csv': dst}
