import json
import pytest
from study.report_facts import file_facts, bounded_facts


def test_late_csv_failure_cannot_hide_behind_first_rows(tmp_path):
    p=tmp_path/'checks.csv';p.write_text('check,passed\n'+''.join(f'case{i},True\n' for i in range(8))+'late,False\n')
    f=file_facts(p)
    assert f['row_count']==9 and f['outcome_counts']['passed']=={'passed':8,'failed':1}
    assert not f['all_passed'] and f['failed_or_unknown_rows'][0]['row_index']==9
    assert 'late' in bounded_facts(p,1200)


@pytest.mark.parametrize('value', ['maybe', '', 'None'])
def test_unknown_outcome_is_not_success(tmp_path,value):
    p=tmp_path/'checks.csv';p.write_text('name,passed\nx,'+value+'\n')
    assert not file_facts(p)['all_passed']


def test_truncated_read_never_claims_full_success(tmp_path):
    p=tmp_path/'checks.csv';p.write_text('name,passed\n'+('x,True\n'*100))
    f=file_facts(p,max_bytes=40)
    assert not f['complete'] and not f['all_passed'] and f['sha256'] is None


def test_jsonl_reports_exit_codes_without_equating_nonzero_to_wrong_result(tmp_path):
    p=tmp_path/'cases.jsonl';p.write_text('\n'.join(json.dumps({'argv':['--literal',str(i)],'exit_code':i%2,'timed_out':False,'db_changed':False}) for i in range(19)))
    f=file_facts(p)
    assert f['record_count']==19 and f['exit_code_counts']=={'0':10,'1':9}
    assert f['cases_complete'] and len(f['cases'])==19 and 'all_passed' not in f


def test_malformed_file_and_tiny_budget_mark_coverage_unknown(tmp_path):
    p=tmp_path/'cases.jsonl';p.write_text('{broken')
    assert not json.loads(bounded_facts(p,300))['complete']
    p=tmp_path/'checks.csv';p.write_text('check,passed\n'+'a,True\n'*200)
    f=json.loads(bounded_facts(p,400));assert 'all_passed' in f or f['complete'] is False


def test_json_late_failure_preserved_when_array_shape_is_sampled(tmp_path):
    p=tmp_path/'verification.json';p.write_text(json.dumps({'checks':[{'name':str(i),'passed':True,'long':'x'*100} for i in range(50)]+[{'name':'late','passed':False}]}))
    f=file_facts(p);assert f['failed_records'][-1]['details']['name']=='late'
