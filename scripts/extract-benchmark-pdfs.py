"""Read primary lab PDFs without OCR or LLM; extracted text is not a semantic audit."""
import argparse
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    base = ROOT / 'materials/semester'
    for path in sorted(base.rglob('*.pdf')):
        if '02_Лабораторні' not in path.parts:
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()):
            raise ValueError('Inspect linked source before extraction')
        record = dict(path=path.relative_to(ROOT).as_posix())
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            reader = PdfReader(path)
            pages = [page.extract_text() or '' for page in reader.pages]
            target = args.output / (digest + '.txt')
            target.write_text('\n'.join(f'\n--- Physical page {i+1} ---\n{text}'
                                        for i, text in enumerate(pages)), encoding='utf-8')
            record.update(sha256=digest, pages=len(pages), characters=sum(map(len, pages)),
                          extraction=target.as_posix(),
                          low_text_pages=[i+1 for i, text in enumerate(pages) if len(text.strip()) < 80])
        except Exception as error:
            record['error'] = type(error).__name__
        records.append(record)
        print(json.dumps(dict(path=record['path'], pages=record.get('pages'),
                              error=record.get('error')), ensure_ascii=False), flush=True)
    (args.output / 'index.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    return 1 if any('error' in row for row in records) else 0


if __name__ == '__main__':
    raise SystemExit(main())
