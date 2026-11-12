"""Oracle for s3-shop-sql: recompute query answers in plain Python, without SQL."""
import csv
from pathlib import Path

HERE = Path(__file__).parent


def load():
    with open(HERE / 'files' / 'customers.csv', encoding='utf-8') as f:
        customers = list(csv.DictReader(f))
    with open(HERE / 'files' / 'orders.csv', encoding='utf-8') as f:
        orders = list(csv.DictReader(f))
    return customers, orders


def expected():
    customers, orders = load()
    by_id = {c['customer_id']: c for c in customers}
    city, total, month = {}, {}, {}
    for o in orders:
        a = float(o['amount'])
        c = by_id[o['customer_id']]
        city[c['city']] = city.get(c['city'], 0) + a
        total[o['customer_id']] = total.get(o['customer_id'], 0) + a
        month[o['order_date'][:7]] = month.get(o['order_date'][:7], 0) + 1
    q1 = [(k, round(v, 2)) for k, v in sorted(city.items(), key=lambda kv: -kv[1])]
    top = sorted(total.items(), key=lambda kv: -kv[1])[:3]
    q2 = [(cid, by_id[cid]['name'], round(v, 2)) for cid, v in top]
    q3 = sorted(month.items())
    q4 = sorted(((c['customer_id'], c['name']) for c in customers if c['customer_id'] not in total), key=lambda x: int(x[0]))
    return q1, q2, q3, q4


def read(path):
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    return [h.strip() for h in rows[0]] if rows else [], rows[1:]


def evaluate(files):
    q1, q2, q3, q4 = expected()
    specs = [
        ('q1_city_revenue.csv', ['city', 'revenue'], [(a, b) for a, b in q1], (str, float)),
        ('q2_top_customers.csv', ['customer_id', 'name', 'total'], q2, (int, str, float)),
        ('q3_orders_by_month.csv', ['month', 'orders'], q3, (str, int)),
        ('q4_no_orders.csv', ['customer_id', 'name'], q4, (int, str)),
    ]
    problems = []
    for name, header, exp, types in specs:
        path = files.get(name)
        if not path:
            problems.append(f'{name} не знайдено'); continue
        got_header, rows = read(path)
        if got_header != header:
            problems.append(f'{name}: колонки {got_header}')
        try:
            got = [tuple(t(v.strip()) for t, v in zip(types, r)) for r in rows if r]
        except ValueError as e:
            problems.append(f'{name}: {e}'); continue
        want = [tuple(t(v) for t, v in zip(types, e)) for e in exp]
        if len(got) != len(want):
            problems.append(f'{name}: {len(got)} рядків замість {len(want)}'); continue
        for g, w in zip(got, want):
            ok = all(abs(a - b) <= 0.011 if isinstance(b, float) else a == b for a, b in zip(g, w))
            if not ok:
                problems.append(f'{name}: {g} замість {w}')
                break
    return not problems, problems, [f'{q1[0][1]:.2f}', q2[0][1]]


def mutate(files, tmp):
    out = dict(files)
    lines = files['q3_orders_by_month.csv'].read_text('utf-8-sig').splitlines()
    parts = lines[1].split(',')
    parts[1] = str(int(parts[1]) + 1)
    lines[1] = ','.join(parts)
    dst = tmp / 'q3_orders_by_month.csv'
    dst.write_text('\n'.join(lines) + '\n', 'utf-8')
    out['q3_orders_by_month.csv'] = dst
    return out


def reference(tmp):
    q1, q2, q3, q4 = expected()
    data = {
        'q1_city_revenue.csv': (['city', 'revenue'], [(a, f'{b:.2f}') for a, b in q1]),
        'q2_top_customers.csv': (['customer_id', 'name', 'total'], [(a, b, f'{c:.2f}') for a, b, c in q2]),
        'q3_orders_by_month.csv': (['month', 'orders'], q3),
        'q4_no_orders.csv': (['customer_id', 'name'], q4),
    }
    out = {}
    for name, (header, rows) in data.items():
        dst = tmp / name
        with open(dst, 'w', encoding='utf-8', newline='') as f:
            w = csv.writer(f); w.writerow(header); w.writerows(rows)
        out[name] = dst
    return out
