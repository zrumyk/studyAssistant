"""Oracle for s2-primes: trial division, independent of a sieve."""
import csv

NS = [10, 100, 1000, 10000, 100000]


def is_prime(n):
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True


def expected():
    out, count, largest, k = {}, 0, None, 1
    for n in NS:
        while k < n:
            k += 1
            if is_prime(k):
                count += 1
                largest = k
        out[n] = (count, largest)
    return out


def evaluate(files):
    path = files.get('primes.csv')
    if not path:
        return False, ['primes.csv не знайдено'], []
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    problems = []
    header = [h.strip() for h in (rows[0].keys() if rows else [])]
    if header != ['N', 'count', 'largest']:
        problems.append(f'колонки {header}')
    got = {}
    for r in rows:
        try:
            got[int(r['N'])] = (int(r['count']), int(r['largest']))
        except (KeyError, ValueError, TypeError):
            problems.append(f'рядок {r}')
    exp = expected()
    for n, v in exp.items():
        if got.get(n) != v:
            problems.append(f'N={n}: {got.get(n)} замість {v}')
    if set(got) - set(exp):
        problems.append(f'зайві N {sorted(set(got) - set(exp))}')
    return not problems, problems, [str(exp[100000][0]), str(exp[100000][1])]


def mutate(files, tmp):
    lines = files['primes.csv'].read_text('utf-8-sig').splitlines()
    parts = lines[-1].split(',')
    parts[1] = str(int(parts[1]) + 1)
    lines[-1] = ','.join(parts)
    dst = tmp / 'primes.csv'
    dst.write_text('\n'.join(lines) + '\n', 'utf-8')
    return {'primes.csv': dst}


def reference(tmp):
    dst = tmp / 'primes.csv'
    rows = ['N,count,largest'] + [f'{n},{c},{l}' for n, (c, l) in expected().items()]
    dst.write_text('\n'.join(rows) + '\n', 'utf-8')
    return {'primes.csv': dst}
