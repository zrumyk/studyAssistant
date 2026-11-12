"""Read bounded declarative project JSON from ZIP; never extract or run scripts."""
import hashlib
import json
from pathlib import PurePosixPath
import stat
import zipfile
from collections import Counter


def project_configs(archive, members):
    names = {'package.json', 'package-lock.json', 'tsconfig.json'}
    candidates = [m for m in members[:10000] if PurePosixPath(m.filename).name in names]
    candidates.sort(key=lambda m: (len(PurePosixPath(m.filename).parts), m.filename))
    counts = Counter(m.filename for m in candidates)
    result = {'files': [], 'scan_limited': len(members) > 10000,
              'candidate_count_in_scan': len(candidates), 'omitted_candidates': max(0, len(candidates) - 6),
              'limitation': 'Untrusted declarations only; scripts not executed, dependencies not installed or certified.'}
    used = 0
    for member in candidates[:6]:
        path = PurePosixPath(member.filename)
        record = {'member': member.filename, 'bytes': member.file_size}
        result['files'].append(record)
        if counts[member.filename] > 1:
            record['status'] = 'duplicate member path; ambiguous, not read'
            continue
        if (member.orig_filename != member.filename or path.is_absolute() or '\\' in member.filename or ':' in member.filename
                or '..' in path.parts or stat.S_ISLNK(member.external_attr >> 16)
                or member.flag_bits & 1):
            record['status'] = 'unsafe path, symlink or encrypted member; not read'
            continue
        if (member.file_size > 256 * 1024 or used + member.file_size > 512 * 1024
                or member.file_size > max(1, member.compress_size) * 200):
            record['status'] = 'size/ratio/total budget exceeded; not read'
            continue
        try:
            with archive.open(member) as stream:
                raw = stream.read(256 * 1024 + 1)
            used += len(raw)
            if len(raw) > 256 * 1024:
                raise ValueError('config exceeds read budget')
            record['sha256'] = hashlib.sha256(raw).hexdigest()
            value = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(value, dict):
                raise ValueError('project config must be a JSON object')
            if path.name == 'package.json':
                fields = ('name', 'version', 'scripts', 'dependencies', 'devDependencies', 'engines')
                selected = {key: value[key] for key in fields if key in value}
            elif path.name == 'package-lock.json':
                packages = value.get('packages', {})
                root = packages.get('', {}) if isinstance(packages, dict) else {}
                selected = {'lockfileVersion': value.get('lockfileVersion'), 'root_package': root}
            else:
                selected = {key: value[key] for key in ('compilerOptions', 'include', 'exclude', 'extends', 'references') if key in value}
            if len(json.dumps(selected, ensure_ascii=False)) > 5000:
                record['status'] = 'selected fields exceed prompt budget; content not included'
                continue
            record.update(status='read', declarations=selected)
        except (OSError, ValueError, RuntimeError, RecursionError, zipfile.BadZipFile, EOFError) as error:
            record['status'] = 'unread: ' + str(error)[:180]
    result['bytes_read'] = used
    return result
