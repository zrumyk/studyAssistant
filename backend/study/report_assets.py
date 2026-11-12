"""Render bounded local SVGs and select sources proven by successful snapshots."""
import hashlib
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from . import config
from .documents import binary

SOURCE_SUFFIXES = {'.py', '.js', '.ts', '.cpp', '.c', '.java', '.cs', '.r', '.m', '.sql'}


def svg_png(path):
    raw=path.read_bytes()
    if len(raw)>1000000 or re.search(br'<!DOCTYPE|<!ENTITY',raw,re.I):
        raise ValueError('SVG XML занадто великий або містить DTD/entity')
    root=ET.fromstring(raw)
    allowed={'svg','g','defs','rect','line','path','text','tspan','circle','ellipse','polyline','polygon','title','desc','marker','clippath','lineargradient','radialgradient','stop','pattern','mask','use'}
    if root.tag.rsplit('}',1)[-1].lower()!='svg':raise ValueError('Потрібний SVG root')
    for node in root.iter():
        if node.tag.rsplit('}',1)[-1].lower() not in allowed:raise ValueError('Непідтримуваний активний SVG element')
        for key,value in node.attrib.items():
            name=key.rsplit('}',1)[-1].lower()
            if name=='base' or name.startswith('on') or (name=='href' and not value.startswith('#')) or '@' in value:
                raise ValueError('SVG external/active resource заборонено')
            if name=='style' and ('\\' in value or (node is root and re.search(r'(?:^|;)\s*(?:min-|max-)?(?:width|height)\s*:',value,re.I))):
                raise ValueError('SVG CSS не може обходити bounded viewport')
            for target in re.findall(r'url\(\s*([^)]*)\)',value,re.I):
                if not target.strip(' \"\'').startswith('#'):raise ValueError('SVG external URL заборонено')
    box=root.get('viewBox','').replace(',',' ').split()
    if len(box)!=4:raise ValueError('SVG потребує finite viewBox')
    values=[float(v) for v in box]
    if any(not math.isfinite(v) or abs(v)>1000000 for v in values) or min(values[2:])<=0:raise ValueError('Некоректний SVG viewBox')
    scale=min(1800/values[2],1800/values[3]);root.set('width',str(round(values[2]*scale)));root.set('height',str(round(values[3]*scale)))
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    key=hashlib.sha256(b'safe-svg-draw-v3'+raw).hexdigest();folder=config.DATA/'render-cache'/key;folder.mkdir(parents=True,exist_ok=True)
    source=folder/'figure.svg';png=folder/'figure.png'
    if not png.exists():
        exe=binary('soffice')
        if not exe:raise ValueError('SVG render потребує LibreOffice')
        source.write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))
        process=subprocess.run([exe,'-env:UserInstallation='+folder.as_uri()+'/lo-profile','--headless','--infilter=svg_Import','--convert-to','png:draw_png_Export','--outdir',str(folder),str(source)],capture_output=True,timeout=60)
        if process.returncode or not png.exists():raise ValueError('SVG render не створив PNG')
    from PIL import Image
    with Image.open(png) as im:
        if max(im.size)>4000 or min(im.size)<1:raise ValueError('Некоректний SVG raster size')
        im.verify()
    return png


def _written_sources(project, step, base):
    """Find source snapshots that match the completed write receipt."""
    from .artifact_contracts import artifact_path, covers
    from .execution_protocol import stream_identity

    receipt = step['result'].get('artifact_contract', {})
    if receipt.get('version') != 1:
        return []
    outputs = step['spec'].get('artifact_outputs', [])
    expected = {}
    for relative, identity in receipt.get('outputs', {}).items():
        if PurePosixPath(relative).suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(covers(output, relative) for output in outputs):
            expected[artifact_path(relative)] = identity
    found = {}
    for artifact in project['artifacts']:
        if (artifact['step_id'] != step['id'] or artifact['state'] == 'stale'
                or artifact['revision'] != project['revision']):
            continue
        try:
            relative = json.loads(artifact['provenance']).get('workspace_path')
            if relative not in expected or relative in found:
                continue
            path = config.safe_path(base, artifact['path'])
            if path.is_relative_to(base.resolve() / 'artifacts') and stream_identity(path) == expected[relative]:
                found[relative] = (artifact, relative, True)
        except (ValueError, OSError):
            continue
    missing = expected.keys() - found.keys()
    if missing:
        raise ValueError('Підтверджені source artifacts відсутні у write snapshot: ' + str(sorted(missing)))
    return [found[relative] for relative in expected]


def report_sources(project):
    from .schemas import ActionCheckpoint
    from .artifact_contracts import covers
    base=config.project_dir(project['id']);candidates=[];expected=set();found=set()
    written = []
    for step in project['steps']:
        if (step['status'] == 'done' and step['spec']['kind'] == 'write'
                and project['plan'].get('artifact_contract_version') == 1):
            written.extend(_written_sources(project, step, base))
            continue
        if step['status']!='done' or step['spec']['kind']!='code':continue
        run=step['result'].get('run_id','')
        if not re.fullmatch('[a-f0-9]{32}',run) or step['result'].get('exit_code')!=0:continue
        try:
            saved=ActionCheckpoint.model_validate_json((base/'codegen'/step['id']/'last-action.json').read_text('utf-8'))
            if saved.revision!=project['revision'] or not saved.written_to_workspace:continue
            own={f.path:f.content for f in saved.action.files}
            for relative in own:
                if any(covers(out,relative) for out in step['spec'].get('artifact_outputs',[])) and config.safe_path(base,relative).suffix.lower() in SOURCE_SUFFIXES:
                    expected.add((step['id'],relative))
            for artifact in project['artifacts']:
                if artifact['step_id']!=step['id'] or artifact['state']=='stale' or artifact['revision']!=project['revision']:continue
                proof=json.loads(artifact['provenance']);relative=proof.get('workspace_path','')
                if relative not in own:continue
                actual=config.safe_path(base,artifact['path']);snapshot=config.safe_path(base,'runs/'+run+'/'+relative)
                actual_bytes, run_bytes = actual.read_bytes(), snapshot.read_bytes()
                source_bytes = own[relative].encode('utf-8')
                if actual_bytes != run_bytes:
                    receipt = step['result'].get('artifact_contract', {})
                    identity = {'bytes': len(run_bytes), 'sha256': hashlib.sha256(run_bytes).hexdigest()}
                    if (receipt.get('version') != 1 or receipt.get('outputs', {}).get(relative) != identity
                            or actual_bytes != source_bytes
                            or actual_bytes.replace(b'\r\n', b'\n') != run_bytes.replace(b'\r\n', b'\n')):
                        continue
                if run_bytes.replace(b'\r\n', b'\n') != source_bytes.replace(b'\r\n', b'\n'):
                    continue
                if actual.suffix.lower() not in SOURCE_SUFFIXES:continue
                candidates.append((artifact,relative,any(covers(out,relative) for out in step['spec'].get('artifact_outputs',[]))))
                found.add((step['id'],relative))
        except (ValueError,OSError):continue
    if expected-found:
        raise ValueError('Підтверджені source artifacts відсутні; потрібне звіряння з run receipts: '+str(sorted(expected-found)))
    declared=any(item[2] for item in candidates);seen=set();result=[]
    for artifact,relative,is_declared in candidates + written:
        if declared and not is_declared:continue
        identity=(relative,hashlib.sha256(config.safe_path(base,artifact['path']).read_bytes()).hexdigest())
        if identity in seen:continue
        seen.add(identity);result.append((artifact,relative))
    return result


def table_text(value):
    return value if len(value)<=400 else value[:220]+' ... [скорочено; повне значення у вихідному CSV] ... '+value[-100:]
