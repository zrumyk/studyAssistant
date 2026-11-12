import copy,json
from study import config,db,engine
from study.codegen import save_candidate
from study.schemas import Action
from study.prior_sources import previous_sources


def test_completed_source_snapshot_is_available_without_executing_it(project,plan):
    candidate=copy.deepcopy(plan)
    candidate['steps'].append(copy.deepcopy(candidate['steps'][0]))
    engine.save_plan(project['id'],candidate)
    p=db.project(project['id']);earlier,current=p['steps']
    runid='a'*32
    run=config.project_dir(p['id'])/'runs'/runid;run.mkdir(parents=True)
    content='raise RuntimeError("MUST_NOT_EXECUTE_OLD_TRAINING")\n\ndef compute_metrics(x):\n    return x*0.5\n'
    (run/'main.py').write_text(content,'utf-8')
    (run/'result.json').write_text(json.dumps({'exit_code':0,'oom':False,'reason':''}),'utf-8')
    save_candidate(p['id'],earlier['id'],p['revision'],'cloud',Action(files=[{'path':'main.py','content':'other buffer'}],explanation='source'))
    db.execute("UPDATE steps SET status='done',result=? WHERE id=?",(json.dumps({'run_id':runid}),earlier['id']))
    preview=previous_sources(p['id'],current['id'],materialize=False)
    assert 'def compute_metrics' in preview
    assert not (config.project_dir(p['id'])/'workspace').exists()
    text=previous_sources(p['id'],current['id'])
    assert 'def compute_metrics(x):' in text and 'return x*0.5' in text
    snapshot=config.project_dir(p['id'])/'workspace'/'inputs'/'_study_sources'/earlier['id']/'main.py.txt'
    assert snapshot.read_bytes()==(run/'main.py').read_bytes()
    assert 'MUST_NOT_EXECUTE_OLD_TRAINING' not in text # module-level code isn't executed or previewed
    assert runid in text and 'sha256' in text
    db.execute("UPDATE steps SET status='failed' WHERE id=?",(earlier['id'],))
    assert previous_sources(p['id'],current['id'])==''


def test_unverified_or_foreign_run_paths_are_not_exposed(project,plan):
    candidate=copy.deepcopy(plan);candidate['steps'].append(copy.deepcopy(candidate['steps'][0]))
    engine.save_plan(project['id'],candidate)
    p=db.project(project['id']);earlier,current=p['steps']
    db.execute("UPDATE steps SET status='done',result=? WHERE id=?",(json.dumps({'run_id':'../../another-project'}),earlier['id']))
    assert previous_sources(p['id'],current['id'])==''

def test_noise_algorithm_and_literal_parameters_are_visible_without_execution(project,plan):
    candidate=copy.deepcopy(plan);candidate['steps'].append(copy.deepcopy(candidate['steps'][0]))
    engine.save_plan(project['id'],candidate);p=db.project(project['id']);earlier,current=p['steps'];runid='c'*32
    run=config.project_dir(p['id'])/'runs'/runid;run.mkdir(parents=True)
    source='SEED=20250101\nFRACTION=0.05\nWINDOWS=[3,5,7,9]\nDANGER=__import__("no_such_package").run()\n\ndef add_noise(image,seed):\n    rng=generator(seed)\n    return choose_without_replacement(image,rng)\n\ndef compute_metric(x):\n    return x*2\n'
    (run/'main.py').write_text(source,'utf-8');(run/'result.json').write_text(json.dumps({'exit_code':0}),'utf-8')
    save_candidate(p['id'],earlier['id'],p['revision'],'cloud',Action(files=[{'path':'main.py','content':source}],explanation='source'))
    db.execute("UPDATE steps SET status='done',result=? WHERE id=?",(json.dumps({'run_id':runid}),earlier['id']))
    text=previous_sources(p['id'],current['id'],budget=24000)
    assert 'SEED=20250101' in text and 'FRACTION=0.05' in text and 'WINDOWS=[3,5,7,9]' in text
    assert 'def add_noise' in text and 'choose_without_replacement' in text
    assert 'DANGER=' not in text and 'no_such_package' not in text
