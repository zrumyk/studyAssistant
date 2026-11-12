"""G05 private oracle. Never imported/uploaded into the study project."""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys


def cases():
    result=[]
    def add(fn,args,value,kind):result.append({'id':f'{fn}-{len(result)}','fn':fn,'args':args,'expected':{'type':kind,'value':value}})
    for n,m in [(0,0),(0,9),(1,0),(8,0),(1,1),(9,3),(2**70,2**65)]:
        add('r',{'n':str(n),'m':str(m)},str(1 if n==0 else -1 if m==0 else 0),'bigint')
    for b in [True,False]:add('s',b,'0','bigint')
    for n in [0,1,7,2**70+3]:
        for b in [True,False]:add('s',[str(n),b],str(n if b else n+1),'bigint')
    for b in [True,False]:
        for n,m in [(0,0),(1,2),(-3,4),(.5,-.25),(2.5,0),(-2,-5)]:add('t',[b,{'n':n,'m':m}],n*m if b else n-2*m,'number')
    for n in [0,1,2,3,5,8,12,20,30]:add('fact',str(n),str(math.factorial(n)),'bigint')
    return result


def check(observations):
    expected=cases();errors=[]
    if len(observations)!=len(expected):return ['Missing/extra observations']
    for case,row in zip(expected,observations):
        value=row.get('value');want=case['expected']['value']
        if row.get('id')!=case['id'] or row.get('type')!=case['expected']['type'] or row.get('error'):
            errors.append(case['id']+': identity/type/error');continue
        if case['expected']['type']=='number':
            valid=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value==want
        else:valid=isinstance(value,str) and value==want
        if not valid:errors.append(case['id']+': incorrect value')
    return errors


def self_test():
    rows=[{'id':c['id'],**c['expected']} for c in cases()];assert not check(rows)
    mutants={}
    for fn in ['r','s','t','fact']:
        for kind in ['value','type','error']:
            data=copy.deepcopy(rows);index=next(i for i,c in enumerate(cases()) if c['fn']==fn)
            if kind=='value':data[index]['value']='wrong' if data[index]['type']=='bigint' else 9876543
            elif kind=='type':data[index]['type']='string'
            else:data[index]['error']='candidate exception'
            errors=check(data);assert errors;mutants[fn+'-'+kind]=errors[0]
    assert check(rows[:-1]);return {'scope':'Synthetic oracle observations only; no student program executed','cases':len(rows),'rejected_mutations':mutants,'missing_observation_rejected':True}


def execute(module,output):
    if os.name!='posix' or not Path('/.dockerenv').is_file():raise RuntimeError('Never run submitted code on host; use constrained Docker')
    if output.exists():raise ValueError('Never overwrite oracle evidence')
    driver=r'''const fs=require('fs'); const modulePath=process.argv[1]; const cases=JSON.parse(fs.readFileSync('/tmp/g05-cases.json','utf8')); const api=require(modulePath); const rows=[];
for(const c of cases){try{let arg=c.args;
 if(c.fn==='r')arg={n:BigInt(arg.n),m:BigInt(arg.m)};
 if(c.fn==='s'&&Array.isArray(arg))arg=[BigInt(arg[0]),arg[1]];
 if(c.fn==='fact')arg=BigInt(arg);
 const v=api[c.fn](arg); rows.push({id:c.id,type:typeof v,value:typeof v==='bigint'?v.toString():v});
}catch(e){rows.push({id:c.id,error:String(e)});}}
process.stdout.write(JSON.stringify(rows));'''
    Path('/tmp/g05-cases.json').write_text(json.dumps([{k:v for k,v in c.items() if k!='expected'} for c in cases()]))
    process=subprocess.run(['node','-e',driver,str(module.resolve())],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=15)
    record={'scope':'External finite functional oracle, not full written/spec/style acceptance','module_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),'exit_code':process.returncode,'stdout':process.stdout[:1000000],'stderr':process.stderr[:1000000]}
    try:record['observations']=json.loads(process.stdout);record['errors']=check(record['observations'])
    except (ValueError,TypeError,AttributeError):record['errors']=['Invalid candidate observation stream']
    if process.returncode:record['errors'].append('Node driver exited nonzero')
    output.write_text(json.dumps(record,indent=2,ensure_ascii=False)+'\n');return bool(record['errors'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--self-test',action='store_true');parser.add_argument('--module',type=Path);parser.add_argument('--output',type=Path);args=parser.parse_args()
    if args.self_test:print(json.dumps(self_test(),ensure_ascii=False));return 0
    if not args.module or not args.output:parser.error('--module and --output required')
    return execute(args.module,args.output)


if __name__=='__main__':sys.exit(main())
