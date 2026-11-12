"""One budgeted protocol probe; never execute generated source on the host."""
import ast
import json
from study import db,config
from study.assistance import BudgetedModel
from study.codegen import ModuleSource
from study.providers import OllamaProvider

pid='6495a07c1c2d473b8627d21b74db3bcb'
db.project(pid)
model=BudgetedModel(pid,OllamaProvider(),'Infrastructure reasoning protocol probe',prefer_cloud=True,thinking_override=True)
response=model.remote('Return a tiny pure Python function ordered_words(words) that returns a new list of strings in ascending order, leaving the input unchanged. No imports, no files, no execution. Return only the requested ModuleSource data.',ModuleSource)
tree=ast.parse(response.content)
assert any(isinstance(node,ast.FunctionDef) and node.name=='ordered_words' for node in tree.body)
receipt=db.query("SELECT id,config,actual,state FROM cloud_requests WHERE project_id=? AND purpose LIKE '%Infrastructure reasoning protocol probe' ORDER BY created DESC LIMIT 1",(pid,))[0]
snapshot=json.loads(receipt['config'])
assert snapshot['cloud_thinking'] is True and snapshot['response_schema']
assert receipt['state']=='done'
proof={'kind':'Infrastructure protocol only; no lab acceptance','request_id':receipt['id'],'strict_thinking_response_received':True,'source_parses':True,'generated_source_executed':False,'actual_usd':receipt['actual'],'configured_request_cap_usd':snapshot['cloud_request_limit_usd'],'global_thinking_setting':config.settings()['cloud_thinking']}
path=config.DATA/'verification'/'thinking-protocol-smoke.json'
path.write_text(json.dumps(proof,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(proof))
