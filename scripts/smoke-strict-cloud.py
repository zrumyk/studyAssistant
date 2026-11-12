"""Paid adapter probe in the existing authorized budget; never a lab result.

Prepare while API is running, then run after stopping the worker to avoid races.
"""
import argparse
import json
from study import config,db

parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--inspect',action='store_true')
args=parser.parse_args()
target=config.DATA/'verification'/'strict-cloud-smoke.json'
if args.prepare:
    import httpx
    with httpx.Client(base_url='http://127.0.0.1:18573',trust_env=False,timeout=30) as client:
        client.headers['x-study-token']=client.get('/api/session').json()['token']
        result=client.post('/api/projects',json={'title':'Технічна проба строгих відповідей API','subject':'Перевірки програми','variant':'','task':'Технічна перевірка формату API; не навчальна робота.'})
        result.raise_for_status()
    target.write_text(json.dumps({'project_id':result.json()['id'],'kind':'Infrastructure probe only','accepted_lab':False},ensure_ascii=False,indent=2),'utf-8')
    print('Adapter probe project prepared')
else:
    from study.assistance import BudgetedModel
    from study.providers import OllamaProvider
    from study.codegen import ModuleSource,PythonBundle
    state=json.loads(target.read_text('utf-8'));pid=state['project_id']
    if db.query('SELECT id FROM cloud_requests WHERE project_id=?',(pid,)) and not args.inspect:
        raise RuntimeError('Probe already attempted; inspect its receipt before another paid request')
    model=BudgetedModel(pid,OllamaProvider(),'Strict adapter smoke',prefer_cloud=True)
    responses=db.query('SELECT response FROM cloud_requests WHERE project_id=? ORDER BY created',(pid,)) if args.inspect else []
    sample=ModuleSource.model_validate_json(responses[0]['response']) if args.inspect else model.generate('Return content exactly: print("strict_probe") followed by one newline. Do not execute it.',ModuleSource)
    assert sample.content.strip()=='print("strict_probe")'
    raw=json.loads(responses[1]['response']) if args.inspect else model.generate('Return a tiny hypothetical PythonBundle: one implementation main.py, one checker check_probe.py, expected output probe.txt; short Ukrainian contracts. No actual source code, no execution.',PythonBundle).model_dump()
    state['manifest_contract_passed']=([f['path'] for f in raw['files']]==['main.py'] and [f['path'] for f in raw['checks']]==['check_probe.py'] and raw['expected_files']==['probe.txt'])
    state['module_source_format_passed']=True
    state['manifest_json_format_passed']=True
    receipts=db.query('SELECT id,state,actual,reserved,config FROM cloud_requests WHERE project_id=?',(pid,))
    state['receipts']=[{'id':r['id'],'state':r['state'],'usd':r['actual'],'maximum_usd':r['reserved'],'strict':bool(json.loads(r['config']).get('response_schema'))} for r in receipts]
    state['status']='passed' if state['manifest_contract_passed'] else 'format_passed_manifest_contract_failed'
    target.write_text(json.dumps(state,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(state,ensure_ascii=False,indent=2))
