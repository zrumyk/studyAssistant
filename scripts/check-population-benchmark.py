"""Offline development oracle. Never import/run a submitted program or trust log claims."""
import argparse
import json
import random
import re


def years(start, end):
    if start < 9 or end < start:
        raise ValueError('Invalid oracle domain')
    population = start
    count = 0
    while population < end:
        births, deaths = population // 3, population // 4
        population += births - deaths
        count += 1
    return count


def cases():
    values = [(100, 200), (1200, 1300), (9, 18), (20, 100), (100, 1000000),
              (9, 9), (10, 11), (11, 12)]
    rng = random.Random(1022026)
    values += [(start := rng.randint(9, 10000), start + rng.randint(0, 100000)) for _ in range(32)]
    result = []
    for index, (start, end) in enumerate(values):
        prefix = '-5\n3\n' if index % 2 else ''
        invalid_end = f'{start - 1}\n' if index % 3 == 0 else ''
        result.append(dict(id=f'P{index:02}', stdin=f'{prefix}{start}\n{invalid_end}{end}\n',
                           start=start, end=end, expected=years(start, end)))
    return result


def check(transcripts):
    expected = {row['id']: row for row in cases()}
    errors = []
    seen = set()
    if not isinstance(transcripts, list):
        return ['transcripts must be a list']
    for row in transcripts:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            errors.append('invalid transcript record')
            continue
        ident = row['id']
        if ident not in expected or ident in seen:
            errors.append(f'{ident}: unknown or duplicate id')
            continue
        seen.add(ident)
        stdout = row.get('stdout')
        matches = re.findall(r'\bYears:\s*([0-9]+)(?=\s|$)', stdout) if isinstance(stdout, str) else []
        if (row.get('stdin') != expected[ident]['stdin'] or type(row.get('exit_code')) is not int
                or row['exit_code'] != 0 or row.get('timed_out') is not False
                or len(matches) != 1 or (matches[0].lstrip('0') or '0') != str(expected[ident]['expected'])):
            errors.append(f'{ident}: invalid execution or result')
    errors += [f'{ident}: missing transcript' for ident in expected.keys() - seen]
    return sorted(errors)


def self_test():
    for start, end, expected in [(100, 200, 9), (1200, 1300, 1), (9, 18, 8),
                                  (20, 100, 20), (100, 1000000, 115), (9, 9, 0)]:
        assert years(start, end) == expected
    valid = [dict(id=row['id'], stdin=row['stdin'], stdout=f"Years: {row['expected']}\n",
                  exit_code=0, timed_out=False) for row in cases()]
    assert not check(valid)
    for mutation in ('off_by_one', 'duplicate_output', 'wrong_input', 'timeout', 'missing'):
        records = [dict(row) for row in valid]
        if mutation == 'off_by_one':
            records[0]['stdout'] = f"Years: {years(100, 200) + 1}\n"
        elif mutation == 'duplicate_output':
            records[0]['stdout'] *= 2
        elif mutation == 'wrong_input':
            records[0]['stdin'] = '100\n100\n'
        elif mutation == 'timeout':
            records[0]['timed_out'] = True
        else:
            records.pop()
        assert check(records), mutation
    assert check([])
    for mutation in ('rounded_births', 'sequential_deaths'):
        records = []
        for case in cases():
            population, count = case['start'], 0
            while population < case['end']:
                births = (population + 2) // 3 if mutation == 'rounded_births' else population // 3
                deaths = (population + births) // 4 if mutation == 'sequential_deaths' else population // 4
                population += births - deaths
                count += 1
                if count > 10000:
                    break
            records.append(dict(id=case['id'], stdin=case['stdin'], stdout=f'Years: {count}\n',
                                exit_code=0, timed_out=False))
        assert check(records), mutation
    return dict(scope='Oracle self-test only; no C execution', cases=len(valid), rejected_mutations=7)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--self-test', action='store_true')
    group.add_argument('--transcripts', type=argparse.FileType('r', encoding='utf-8'))
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return 0
    errors = check(json.load(args.transcripts))
    print(json.dumps(dict(errors=errors, scope='Transcript consistency only; authenticity needs audit')))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
