import json
import pytest
from study import billing,cloud,config,db


@pytest.mark.parametrize('at,factor',[
    ('2026-10-21T00:59:59+00:00',.5),('2026-10-21T01:00:00+00:00',1),
    ('2026-10-21T03:59:59+00:00',1),('2026-10-21T04:00:00+00:00',.5),
    ('2026-10-21T06:00:00+00:00',1),('2026-10-21T10:00:00+00:00',.5),
    ('2026-10-24T07:00:00+00:00',.5),('2026-10-21T09:00:00+03:00',1),
    (None,1),('unknown',1),('2026-10-21T20:00:00',1),
])
def test_cache_and_utc_tariff_boundaries_without_double_counting_reasoning(at,factor):
    usage={'prompt_tokens':1000000,'prompt_cache_hit_tokens':750000,'prompt_cache_miss_tokens':250000,'completion_tokens':100000,'completion_tokens_details':{'reasoning_tokens':90000}}
    expected=(750000*.006+250000*.3+100000*1.2)/1e6*factor
    assert cloud.usage_cost('deepseek',usage,billing.TARIFF,9,at=at)==pytest.approx(expected)


def test_old_manual_prices_and_missing_cache_data_remain_conservative():
    usage={'prompt_tokens':100,'completion_tokens':20}
    old={'cloud_input_price':.3,'cloud_output_price':1.2}
    assert cloud.usage_cost('deepseek',usage,old,1)==pytest.approx(54/1e6)
    assert cloud.usage_cost('deepseek',{**usage,'prompt_cache_hit_tokens':90,'prompt_cache_miss_tokens':99},billing.TARIFF,1)==pytest.approx(54/1e6)
    assert cloud.usage_cost('deepseek',{},billing.TARIFF,.03)==.03
    assert cloud.usage_cost('deepseek',{**usage,'prompt_tokens':-1},billing.TARIFF,.03)==.03
    assert cloud.usage_cost('gemini',{'promptTokenCount':100,'candidatesTokenCount':10,'thoughtsTokenCount':10},old,1,at='2026-10-24T20:00:00+00:00')==pytest.approx(54/1e6)


def test_reconciliation_is_idempotent_retains_raw_receipts_and_holds_uncertain_reserve(project):
    pid=project['id'];rid='original-request';old=.03
    db.execute('INSERT INTO cloud_requests(id,project_id,month,actual,reserved,state,config,created) VALUES(?,?,?,?,?,?,?,?)',(rid,pid,'2026-10',old,.03,'done',json.dumps(billing.TARIFF),'2026-10-21T20:00:00+00:00'))
    result={'usage':{'prompt_tokens':1000,'completion_tokens':100,'prompt_cache_hit_tokens':800},'cost_usd':old}
    db.execute('INSERT INTO jobs(id,project_id,kind,status,payload,result,created) VALUES(?,?,?,?,?,?,?)',('receipt',pid,'cloud','done',json.dumps({'id':rid}),json.dumps(result),db.now()))
    db.execute('INSERT INTO cloud_requests(id,project_id,month,reserved,state,created) VALUES(?,?,?,?,?,?)',('uncertain',pid,'2026-10',.02,'uncertain',db.now()))
    preview=billing.reconcile_deepseek_usage()
    assert preview['requests_with_saved_usage']==1 and not db.query('SELECT * FROM cloud_cost_adjustments')
    billing.reconcile_deepseek_usage(apply=True);billing.reconcile_deepseek_usage(apply=True)
    assert len(db.query('SELECT * FROM cloud_cost_adjustments'))==1
    assert db.one('SELECT actual FROM cloud_requests WHERE id=?',(rid,))['actual']==pytest.approx(preview['corrected_estimate_usd'])
    assert db.one('SELECT original_estimate FROM cloud_cost_adjustments WHERE request_id=?',(rid,))['original_estimate']==old
    assert json.loads(db.one('SELECT result FROM jobs WHERE id=?',('receipt',))['result'])==result
    with db.connect() as c:assert cloud.spent(c,'2026-10')==pytest.approx(.02+preview['corrected_estimate_usd'])


@pytest.mark.parametrize('value',[-1,float('nan'),float('inf')])
def test_invalid_cache_prices_rejected(value):
    with pytest.raises(ValueError):config.save_settings({'cloud_cache_input_price':value})
