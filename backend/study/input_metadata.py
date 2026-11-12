"""Read array schemas without loading object payloads or executing user code."""

import hashlib
import json
import zipfile
import re
from pathlib import Path
from . import config
from .schemas import ActionCheckpoint


def imported_data_metadata(pid):
    """Inspect headers/ZIP declarative configs without extraction or execution."""
    from . import db
    from .search import scope
    condition,args=scope(pid)
    records=[]
    for doc in db.query("SELECT d.name,d.path FROM documents d WHERE d.role='data' AND "+condition,args):
        path=Path(doc['path'])
        record={'name':doc['name'],'bytes':path.stat().st_size}
        try:
            if path.suffix.lower() in ('.npz','.npy'):
                record['array_headers']=json.loads(array_metadata(path.parent))
            elif path.suffix.lower()=='.zip':
                with zipfile.ZipFile(path) as archive:
                    members=archive.infolist()
                    record['member_count']=len(members)
                    record['uncompressed_bytes']=sum(item.file_size for item in members)
                    names=[item.filename for item in members if not item.is_dir()]
                    record['examples']=names[:15]
                    record['top_directories']=sorted({name.split('/')[0] for name in names})[:30]
                    record['second_directories']=sorted({'/'.join(name.split('/')[:2]) for name in names if '/' in name})[:30]
                    from .archive_metadata import project_configs
                    record['project_configs'] = project_configs(archive, members)
        except (OSError,ValueError,zipfile.BadZipFile) as error:
            record['warning']=str(error)[:300]
        records.append(record)
    # Keep valid structured metadata and explicit coverage instead of slicing JSON.
    budget = 9000
    kept = []
    for record in records:
        if len(json.dumps(kept + [record], ensure_ascii=False)) > budget - 400:
            kept.append({'name': record['name'][:120], 'metadata_omitted': 'combined metadata exceeds9000-character budget'})
            break
        kept.append(record)
    if len(kept) < len(records):
        kept.append({'remaining_records_omitted': len(records) - len(kept)})
    return json.dumps(kept,ensure_ascii=False)


def repair_sources(pid, sid, revision, model, workspace, entry, previous):
    if not previous:
        return ""
    log = previous
    try:
        log = json.loads(previous).get("log", previous)
    except (ValueError, AttributeError):
        pass
    mentioned = {
        Path(path.replace("\\", "/")).name
        for path in re.findall(r'File "([^"\n]+)"', log)
    }
    if not mentioned:
        mentioned = {Path(path).name for path in re.findall(r"[\w./-]+\.py", previous)}
    sources = {}
    current_checkpoint=False
    checkpoint = config.project_dir(pid) / "codegen" / sid / "last-action.json"
    if checkpoint.exists():
        try:
            saved = ActionCheckpoint.model_validate_json(checkpoint.read_text("utf-8"))
            if saved.revision == revision:
                current_checkpoint=True
                for file in saved.action.files+saved.action.checks:
                    key=Path(file.path.replace('\\','/')).as_posix()
                    if not saved.written_to_workspace:
                        sources[key]=file.content
                    else:
                        # Handled exceptions often have no traceback filename.
                        # Include this step's actual helper modules, not just main.
                        actual=config.safe_path(workspace,file.path)
                        if actual.is_file() and actual.stat().st_size<=100000:
                            sources[key]=actual.read_text('utf-8',errors='replace')
        except (ValueError, OSError):
            pass
    if current_checkpoint and workspace.exists():
        for path in workspace.rglob("*"):
            if path.is_file() and (path.name == entry or path.name in mentioned):
                safe = config.safe_path(workspace, path.relative_to(workspace))
                relative = path.relative_to(workspace).as_posix()
                if safe.stat().st_size <= 100000 and relative not in sources:
                    sources[relative] = safe.read_text("utf-8", errors="replace")
    settings=config.settings()
    # A cloud repair must not inherit the laptop's small local context budget.
    # Missing the middle of the failed function made repeated repairs ineffective.
    remaining = 24000 if settings['cloud_consent_mode']=='budgeted' else (8000 if settings['context']==8192 else 3000)
    parts = []
    for path in sorted(
        sources,
        key=lambda name: (Path(name).name not in mentioned, Path(name).name != entry),
    )[:8]:
        if remaining < 300:
            break
        content = sources[path]
        if len(content) > remaining:
            content = (
                content[: remaining // 2]
                + "\n[середину великого файлу пропущено]\n"
                + content[-remaining // 2 :]
            )
        parts.append(path + ":\n" + content)
        remaining -= len(content)
    return "\n".join(parts)


def array_metadata(workspace):
    import numpy.lib.format as fmt

    def header(stream):
        version = fmt.read_magic(stream)
        if version == (1, 0):
            shape, order, dtype = fmt.read_array_header_1_0(stream)
        elif version == (2, 0):
            shape, order, dtype = fmt.read_array_header_2_0(stream)
        else:
            return {"warning": "Непідтримувана версія заголовка NPY"}
        return {
            "shape": list(shape),
            "dtype": str(dtype),
            "object_payload": bool(dtype.hasobject),
        }

    result = []
    if not workspace.exists():
        return "[]"
    for path in workspace.rglob("*"):
        if path.suffix.lower() not in (".npy", ".npz") or not path.is_file():
            continue
        path = config.safe_path(workspace, path.relative_to(workspace))
        entry = {"path": str(path.relative_to(workspace)).replace("\\", "/")}
        try:
            if path.suffix.lower() == ".npy":
                with path.open("rb") as stream:
                    entry["array"] = header(stream)
            else:
                arrays = {}
                with zipfile.ZipFile(path) as archive:
                    for name in archive.namelist()[:24]:
                        if name.endswith(".npy"):
                            with archive.open(name) as stream:
                                arrays[name[:-4]] = header(stream)
                entry["arrays"] = arrays
            if path.stat().st_size <= 100_000_000:
                with path.open("rb") as stream:
                    entry["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        except (ValueError, OSError, zipfile.BadZipFile) as error:
            entry["warning"] = str(error)[:300]
        result.append(entry)
        if len(result) >= 4:
            break
    return json.dumps(result, ensure_ascii=False)[:8000]
