"""Bounded visual diagnostics; findings are evidence, not semantic acceptance."""
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import ImageFont


def svg_text_findings(path):
    raw=path.read_bytes()
    if len(raw)>1000000 or re.search(br'<!DOCTYPE|<!ENTITY',raw,re.I):
        return {'coverage':'unknown','reason':'SVG size or DTD/entity','findings':[]}
    try: root=ET.fromstring(raw)
    except ET.ParseError:return {'coverage':'unknown','reason':'Invalid XML','findings':[]}
    boxes=[];unknown=[];total=0
    fontfile=next((p for p in [Path('C:/Windows/Fonts/arial.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')] if p.is_file()),None)
    def walk(node, inherited, unsupported=False, depth=0):
        nonlocal total
        if depth>64:
            unknown.append('SVG nesting limit');return
        attrs=inherited|node.attrib
        unsupported=unsupported or any(k in node.attrib for k in ('transform','style','display','visibility','dominant-baseline','baseline-shift','rotate','textLength','letter-spacing','word-spacing')) or (node is not root and node.tag.rsplit('}',1)[-1]=='svg')
        if node.tag.rsplit('}',1)[-1]=='text':
            total+=1;label=''.join(node.itertext()).strip()
            try:
                if unsupported or len(node) or len(boxes)>=200 or len(label)>500:raise ValueError('Unsupported typography/transform or limit')
                x=float(attrs.get('x','0'));y=float(attrs.get('y','0'));size=float(attrs.get('font-size','16').removesuffix('px'))
                if not label or any(not math.isfinite(v) for v in (x,y,size)) or not 1<=size<=500:raise ValueError('Unsupported text geometry')
                font=ImageFont.truetype(str(fontfile),max(1,round(size))) if fontfile else ImageFont.load_default()
                left,top,right,bottom=font.getbbox(label,anchor='ls')
                width=right-left;anchor=attrs.get('text-anchor','start')
                if anchor not in ('start','middle','end'):raise ValueError('Unknown anchor')
                offset=width/2 if anchor=='middle' else width if anchor=='end' else 0
                boxes.append({'text':label,'bbox':[x+left-offset,y+top,x+right-offset,y+bottom]})
            except (ValueError,OSError):unknown.append(label[:100])
        inherited_font={k:v for k,v in attrs.items() if k in ('font-size','text-anchor','font-weight')}
        for child in node:walk(child,inherited_font,unsupported,depth+1)
    walk(root,{})
    findings=[];count=0
    for i,a in enumerate(boxes):
        for b in boxes[i+1:]:
            x1,y1,x2,y2=a['bbox'];u1,v1,u2,v2=b['bbox']
            intersection=max(0,min(x2,u2)-max(x1,u1))*max(0,min(y2,v2)-max(y1,v1))
            smaller=min((x2-x1)*(y2-y1),(u2-u1)*(v2-v1))
            if smaller>0 and intersection/smaller>=.3:
                count+=1
                if len(findings)<25:findings.append({'kind':'svg_text_overlap_candidate','a':a,'b':b,'overlap_ratio':round(intersection/smaller,3)})
    return {'source_sha256':hashlib.sha256(raw).hexdigest(),'coverage':'partial' if unknown or not fontfile else 'simple_axis_aligned_text',
            'total_texts':total,'checked_texts':len(boxes),'unhandled_texts':len(unknown),'unhandled_examples':unknown[:8],
            'font':fontfile.name if fontfile else 'PillowDefault','finding_count':count,'findings':findings,
            'limitation':'Approximate glyph boxes; no claim of raster/semantic acceptance. Transforms/CSS/tspan are not certified.'}


def pdf_tail_findings(path):
    from pypdf import PdfReader
    reader=PdfReader(path)
    if len(reader.pages)>200:return {'coverage':'unknown','findings':[],'reason':'Page limit'}
    texts=[p.extract_text() or '' for p in reader.pages]
    if not texts:return {'coverage':'unknown','findings':[]}
    appendix=any('Додаток Програмний код' in text for text in texts)
    lines=[line.strip() for line in texts[-1].splitlines() if line.strip()]
    if lines and lines[-1]==str(len(texts)):lines.pop()
    if lines and lines[0]==str(len(texts)):lines.pop(0)
    findings=[]
    if appendix and 0<len(lines)<3:
        findings.append({'kind':'orphan_source_tail','page':len(texts),'lines':lines})
    return {'coverage':'pdf_text_tail_only','pages':len(texts),'findings':findings,
            'limitation':'Text extraction does not certify other layout, clipping or image readability.'}


def report_visual_review(project, pdf):
    from . import config
    result={'version':1,'status':'needs_visual_review','svg':[],'pdf':None}
    for artifact in project['artifacts']:
        if artifact['state']=='stale' or Path(artifact['name']).suffix.lower()!='.svg':continue
        path=config.safe_path(config.project_dir(project['id']),artifact['path'])
        result['svg'].append({'artifact_id':artifact['id'],'name':artifact['name'],**svg_text_findings(path)})
    if pdf and pdf.is_file():result['pdf']=pdf_tail_findings(pdf)
    result['findings_count']=sum(item.get('finding_count',0) for item in result['svg'])+len((result['pdf'] or {}).get('findings',[]))
    return result
