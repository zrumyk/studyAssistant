"""Auditable recalculation from saved usage; never calls a provider or resets spend."""
import json
from . import db,cloud

SOURCE='https://api-docs.deepseek.com/quick_start/pricing/'
TARIFF={'cloud_provider':'deepseek','cloud_model':'deepseek-flash','cloud_input_price':.3,'cloud_cache_input_price':.006,'cloud_output_price':1.2,'cloud_deepseek_offpeak':True,'cloud_prices_date':'2026-10-22'}


def reconcile_deepseek_usage(apply=False):
    requests={r['id']:r for r in db.query("SELECT r.id,r.created,r.config,coalesce(a.original_estimate,r.actual) actual,r.reserved FROM cloud_requests r LEFT JOIN cloud_cost_adjustments a ON a.request_id=r.id WHERE r.state='done'")}
    seen=set();changes=[]
    for job in db.query("SELECT id,payload,result FROM jobs WHERE kind='cloud' AND status='done' ORDER BY created"):
        try:
            rid=json.loads(job['payload']).get('id')
            if rid in seen or rid not in requests:continue
            row=requests[rid];settings=json.loads(row['config']);result=json.loads(job['result'])
            if settings.get('cloud_provider')!='deepseek' or settings.get('cloud_model') not in ('deepseek-flash','deepseek-v4-flash','deepseek-v4-flash-vision-exp'):continue
            if not '2026-09-10'<=row['created'][:10]<=TARIFF['cloud_prices_date'] or row['actual'] is None:continue
            usage=result.get('usage',{})
            if not all(isinstance(usage.get(k),int) and not isinstance(usage[k],bool) and usage[k]>=0 for k in ('prompt_tokens','completion_tokens')):continue
            at=result.get('billing_at') or row['created']
            cost=cloud.usage_cost('deepseek',usage,TARIFF,row['reserved'],at=at)
            evidence={'source':SOURCE,'verified_on':TARIFF['cloud_prices_date'],'tariff':TARIFF,'usage':usage,'billing_at':at,'job_id':job['id'],'timestamp_basis':'provider-or-send-time' if result.get('billing_at') else 'request-created-time','note':'Estimate from usage, not a provider invoice. Unknown Chinese holidays conservatively use weekday peak. Original cost is retained in this adjustment; original job receipts and chat messages are unchanged.'}
            changes.append((rid,row['actual'],cost,json.dumps(evidence,ensure_ascii=False),db.now()))
            seen.add(rid)
        except (ValueError,TypeError,KeyError):continue
    if apply:
        with db.connect() as c:
            c.executemany('INSERT OR IGNORE INTO cloud_cost_adjustments VALUES(?,?,?,?,?)',changes)
            c.execute("UPDATE cloud_requests SET actual=(SELECT corrected_estimate FROM cloud_cost_adjustments a WHERE a.request_id=cloud_requests.id) WHERE state='done' AND id IN (SELECT request_id FROM cloud_cost_adjustments)")
    return {'requests_with_saved_usage':len(changes),'original_estimate_usd':sum(v[1] for v in changes),'corrected_estimate_usd':sum(v[2] for v in changes),'applied':apply,'provider_invoice_imported':False,'source':SOURCE}
