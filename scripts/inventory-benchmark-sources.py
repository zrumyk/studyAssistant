"""Hash material files; catalogue labels are not a content/primary-source audit."""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    base = ROOT / 'materials/semester'
    catalog = json.loads((base / '_catalog.json').read_text(encoding='utf-8'))
    files = []
    groups = defaultdict(list)
    for path in sorted(base.rglob('*')):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(base.resolve()) or path.is_symlink():
            raise ValueError('Material tree contains a link; inspect before inventory')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                digest.update(block)
        name = path.relative_to(ROOT).as_posix()
        files.append(dict(path=name, bytes=path.stat().st_size, sha256=digest.hexdigest(),
                          catalog=catalog.get(path.relative_to(base).as_posix())))
        groups[digest.hexdigest()].append(name)
    actual = {path.relative_to(base).as_posix() for path in base.rglob('*') if path.is_file()}
    by_name = defaultdict(list)
    for name in sorted(actual):
        by_name[Path(name).name].append(name)
    missing = sorted(set(catalog) - actual)
    report = dict(scope='File metadata and hashes only; catalogue roles do not prove primary content',
                  files=files, missing_catalog_paths=sorted(set(catalog) - actual),
                  uncatalogued_paths=sorted(actual - set(catalog)),
                  relocation_candidates=[dict(old_path=name, candidates=by_name[Path(name).name],
                                              basis='Exact filename only; not content/provenance proof')
                                         for name in missing],
                  duplicate_groups=[paths for paths in groups.values() if len(paths) > 1])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(files=len(files), bytes=sum(row['bytes'] for row in files),
                          missing_catalog_paths=len(report['missing_catalog_paths']),
                          uncatalogued_paths=len(report['uncatalogued_paths']),
                          duplicate_groups=len(report['duplicate_groups']))))


if __name__ == '__main__':
    main()
