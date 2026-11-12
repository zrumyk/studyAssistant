import json
import pytest
from PIL import Image
from study import config, db, engine, reports
from study.report_assets import svg_png, report_sources, table_text


@pytest.mark.parametrize('fragment', [
    '<script>alert(1)</script>', '<image href="file:///secret"/>',
    '<use href="https://example.test/x.svg"/>', '<rect style="fill:url(http://example.test/x)"/>',
    '<text onclick="fetch(1)">x</text>', '<foreignObject/>',
    '<use xml:base="https://example.test/" href="#external"/>',
])
def test_active_or_external_svg_is_rejected_before_converter(tmp_path,monkeypatch,fragment):
    from study import report_assets
    monkeypatch.setattr(report_assets.subprocess,'run',lambda *a,**k:pytest.fail('Must reject before external process'))
    p=tmp_path/'x.svg';p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'+fragment+'</svg>')
    with pytest.raises(ValueError):svg_png(p)


def test_dtd_and_infinite_geometry_rejected(tmp_path):
    p=tmp_path/'x.svg';p.write_text('<!DOCTYPE svg [<!ENTITY x "secret">]><svg/>')
    with pytest.raises(ValueError):svg_png(p)
    p.write_text('<svg viewBox="0 0 inf 1"/>')
    with pytest.raises(ValueError):svg_png(p)
    p.write_text('<svg viewBox="0 0 100 100" style="width:999999999px"/>')
    with pytest.raises(ValueError):svg_png(p)


def test_svg_conversion_is_bounded_cached_and_preserves_original(tmp_path,monkeypatch):
    from study import report_assets
    p=tmp_path/'x.svg';raw='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><rect width="100" height="50"/></svg>';p.write_text(raw)
    calls=[];monkeypatch.setattr(report_assets,'binary',lambda *a:'converter')
    def run(args,**kwargs):
        assert '--infilter=svg_Import' in args and 'png:draw_png_Export' in args
        calls.append(args);folder=__import__('pathlib').Path(args[args.index('--outdir')+1]);Image.new('RGB',(1800,900),'white').save(folder/'figure.png')
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr(report_assets.subprocess,'run',run)
    assert svg_png(p)==svg_png(p) and len(calls)==1 and p.read_text()==raw


@pytest.fixture
def source_project(project,plan):
    from study.codegen import save_candidate
    from study.schemas import Action,ActionCheckpoint
    pid=project['id'];plan['artifact_contract_version']=1;plan['steps'][0].update(artifact_inputs=[],artifact_outputs=['app/codec.py','result.csv'])
    engine.save_plan(pid,plan);p=db.project(pid);sid=p['steps'][0]['id']
    a=Action(files=[{'path':'main.py','content':'print(1)'},{'path':'app/codec.py','content':'def transform(value):\n    return value+1\n'}],entrypoint='main.py',checks=[{'path':'check_current_step.py','content':'assert 1==1'}],expected_files=['result.csv'],explanation='fixture')
    save_candidate(pid,sid,p['revision'],'fixture',a);workspace=engine.write_action(pid,sid,a)
    (workspace/'seed').mkdir();(workspace/'seed/codec.py').write_text('ORIGINAL_STARTER_STUB')
    engine.artifact(pid,sid,'seed/codec.py','executed')
    runid='b'*32;run=config.project_dir(pid)/'runs'/runid;run.mkdir(parents=True)
    for f in a.files+a.checks:
        path=run/f.path;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((workspace/f.path).read_bytes())
    db.execute("UPDATE steps SET status='done',result=? WHERE id=?",(json.dumps({'run_id':runid,'exit_code':0}),sid))
    return pid,sid


def test_appendix_selects_owned_declared_source_with_full_path_not_starter_or_driver(source_project,monkeypatch):
    from docx import Document
    from study.schemas import Report
    pid,sid=source_project;p=db.project(pid);items=report_sources(p)
    assert [relative for _,relative in items]==['app/codec.py']
    monkeypatch.setattr(reports,'binary',lambda *a:None)
    out=reports.DocxRenderer().render(pid,Report(title='Test',sections=[]));d=Document(out['files'][0]);text='\n'.join(p.text for p in d.paragraphs)
    assert 'app/codec.py' in text and 'return value+1' in text
    assert 'ORIGINAL_STARTER_STUB' not in text and 'print(1)' not in text


def test_missing_declared_source_does_not_silently_fall_back_to_driver(source_project):
    pid,sid=source_project
    db.execute("UPDATE artifacts SET state='stale' WHERE project_id=? AND json_extract(provenance,'$.workspace_path')='app/codec.py'",(pid,))
    with pytest.raises(ValueError,match='source artifacts'):report_sources(db.project(pid))


def test_appendix_accepts_only_receipt_bound_crlf_conversion(source_project):
    from study.execution_protocol import stream_identity

    pid, sid = source_project
    step = db.project(pid)['steps'][0]
    result = step['result']
    name = 'app/codec.py'
    snapshot = config.project_dir(pid) / 'runs' / result['run_id'] / name
    snapshot.write_bytes(snapshot.read_bytes().replace(b'\n', b'\r\n'))
    result['artifact_contract'] = {'version': 1, 'outputs': {name: stream_identity(snapshot)}}
    db.execute('UPDATE steps SET result=? WHERE id=?', (json.dumps(result), sid))
    assert [relative for _, relative in report_sources(db.project(pid))] == [name]
    snapshot.write_bytes(snapshot.read_bytes().replace(b'value+1', b'value+2'))
    with pytest.raises(ValueError, match='source artifacts'):
        report_sources(db.project(pid))


def test_long_cell_has_explicit_abbreviation_and_short_values_unchanged():
    assert table_text('3.14159')=='3.14159'
    original='start'+('x'*1000)+'end';result=table_text(original)
    assert result.startswith('start') and result.endswith('end') and 'скорочено' in result and len(result)<500


@pytest.fixture
def written_source(project, plan):
    from study.artifact_contracts import capture_outputs
    from study.schemas import Action

    pid = project['id']
    name = 'work/schema.sql'
    writer = plan['steps'][0] | {'kind': 'write', 'artifact_outputs': [name, 'work/seed.txt']}
    report = plan['steps'][0] | {'kind': 'report', 'artifact_inputs': [name]}
    plan.update(artifact_contract_version=1, steps=[writer, report])
    engine.save_plan(pid, plan)
    engine.approve(pid)
    step = db.project(pid)['steps'][0]
    action = Action(files=[
        {'path': name, 'content': 'CREATE TABLE appendix_events(id INTEGER);\n'},
        {'path': 'work/seed.txt', 'content': 'Fixture data, not source.'},
        {'path': 'private_note.py', 'content': 'UNDECLARED_NOTE = True'},
    ], explanation='Controlled developer write fixture')
    workspace = engine.write_action(pid, step['id'], action)
    result = {'artifact_contract': capture_outputs(workspace, step['spec'], [f.path for f in action.files])}
    db.execute("UPDATE steps SET status='done',result=? WHERE id=?", (json.dumps(result), step['id']))
    return pid, name


def test_appendix_contains_declared_write_source_from_receipt(written_source, monkeypatch):
    from docx import Document
    from study.schemas import Report

    pid, name = written_source
    assert [relative for _, relative in report_sources(db.project(pid))] == [name]
    monkeypatch.setattr(reports, 'binary', lambda *args: None)
    rendered = reports.DocxRenderer().render(pid, Report(title='Write fixture', sections=[]))
    text = '\n'.join(p.text for p in Document(rendered['files'][0]).paragraphs)
    assert name in text and 'CREATE TABLE appendix_events' in text
    assert 'Fixture data, not source.' not in text and 'UNDECLARED_NOTE' not in text


@pytest.mark.parametrize('failure', ['tampered_bytes', 'old_revision'])
def test_appendix_rejects_unverified_write_snapshot(written_source, failure):
    pid, name = written_source
    project = db.project(pid)
    artifact = next(a for a in project['artifacts'] if json.loads(a['provenance'])['workspace_path'] == name)
    if failure == 'tampered_bytes':
        config.safe_path(config.project_dir(pid), artifact['path']).write_bytes(b'CREATE TABLE tampered(id INTEGER);')
    else:
        db.execute('UPDATE artifacts SET revision=revision-1 WHERE id=?', (artifact['id'],))
    with pytest.raises(ValueError, match='source artifacts'):
        report_sources(db.project(pid))
