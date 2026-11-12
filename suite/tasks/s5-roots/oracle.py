"""Oracle for s5-roots, variant 3: f(x) = x^3 - 2x - 5 on [2; 3], eps = 1e-6."""
import json
import math

A, B, EPS = 2.0, 3.0, 1e-6


def f(x):
    return x ** 3 - 2 * x - 5


def true_root():
    a, b = A, B
    for _ in range(200):
        c = (a + b) / 2
        if f(a) * f(c) <= 0:
            b = c
        else:
            a = c
    return (a + b) / 2


def evaluate(files):
    path = files.get('roots.json')
    if not path:
        return False, ['roots.json не знайдено'], []
    try:
        data = json.loads(path.read_text('utf-8-sig'))
    except ValueError:
        return False, ['roots.json не є JSON'], []
    if isinstance(data, dict):
        data = data.get('results') or data.get('roots') or list(data.values())
    problems = []
    by = {}
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get('method') in ('bisection', 'newton'):
            by[item['method']] = item
    if set(by) != {'bisection', 'newton'}:
        return False, [f'методи {sorted(by)} замість bisection і newton'], []
    r = true_root()
    bound = math.ceil(math.log2((B - A) / EPS))
    for method, item in by.items():
        try:
            root, it, fr = float(item['root']), int(item['iterations']), float(item['f_root'])
        except (KeyError, TypeError, ValueError):
            problems.append(f'{method}: поля root, iterations, f_root'); continue
        if abs(root - r) > 2 * EPS:
            problems.append(f'{method}: корінь {root}, істинний {r:.9f}')
        if abs(fr - f(root)) > 1e-6:
            problems.append(f'{method}: f_root {fr} не дорівнює f(root) {f(root)}')
        if method == 'bisection' and not bound - 2 <= it <= bound + 2:
            problems.append(f'bisection: {it} ітерацій, очікується близько {bound}')
        if method == 'newton' and not 1 <= it <= 15:
            problems.append(f'newton: {it} ітерацій')
    return not problems, problems, [f'{r:.4f}']


def mutate(files, tmp):
    data = json.loads(files['roots.json'].read_text('utf-8-sig'))
    items = data if isinstance(data, list) else data.get('results') or data.get('roots')
    for item in items:
        if item.get('method') == 'newton':
            item['root'] = float(item['root']) + 0.01
    dst = tmp / 'roots.json'
    dst.write_text(json.dumps(data), 'utf-8')
    return {'roots.json': dst}


def reference(tmp):
    r = true_root()
    dst = tmp / 'roots.json'
    dst.write_text(json.dumps([
        {'method': 'bisection', 'root': r, 'iterations': math.ceil(math.log2((B - A) / EPS)), 'f_root': f(r)},
        {'method': 'newton', 'root': r, 'iterations': 4, 'f_root': f(r)},
    ]), 'utf-8')
    return {'roots.json': dst}
