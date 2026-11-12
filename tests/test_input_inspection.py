import json
import pytest
from study import config
from study.input_inspection import inspect_inputs,InputInspection
from study import input_inspection


def enable():config.save_settings({'cloud_consent_mode':'budgeted'})


def test_reads_selected_actual_data_and_refreshes_it_without_rebilling_selection(project,monkeypatch):
    enable();root=config.project_dir(project['id'])/'workspace';root.mkdir()
    file=root/'manifest.csv';file.write_text('path,split\nimage_a,train\n','utf-8')
    calls=[]
    def generate(*args):calls.append(1);return InputInspection(paths=['manifest.csv'],why='Need actual split columns')
    monkeypatch.setattr(input_inspection.BudgetedModel,'generate',generate)
    spec={'instruction':'Prepare dataset'}
    text=inspect_inputs(project['id'],'step',spec,root)
    assert 'path,split' in text and 'image_a,train' in text
    file.write_text('path,split\nimage_b,test\n','utf-8')
    assert 'image_b,test' in inspect_inputs(project['id'],'step',spec,root)
    assert len(calls)==1


@pytest.mark.parametrize('path',['../outside.txt','D:/StudyAssistant/.env','.env'])
def test_inspector_cannot_request_host_files_or_hidden_secrets(project,monkeypatch,path):
    enable();root=config.project_dir(project['id'])/'workspace';root.mkdir()
    (root/'data.csv').write_text('value\n1','utf-8')
    (root/'.env').write_text('NEVER_READ_THIS','utf-8')
    monkeypatch.setattr(input_inspection.BudgetedModel,'generate',lambda *a:InputInspection(paths=[path]))
    with pytest.raises(ValueError,match='лише показані файли'):inspect_inputs(project['id'],'step',{'instruction':'Read data'},root)


def test_large_tables_are_sampled_and_permission_is_required_for_selection(project,monkeypatch):
    root=config.project_dir(project['id'])/'workspace';root.mkdir()
    (root/'rows.csv').write_text('value\n'+'\n'.join('row-'+str(i) for i in range(10000)),'utf-8')
    (root/'shape.json').write_text(json.dumps({'items':list(range(10000))}),'utf-8')
    calls=[]
    def generate(*a):calls.append(1);return InputInspection(paths=['rows.csv','shape.json'])
    monkeypatch.setattr(input_inspection.BudgetedModel,'generate',generate)
    assert inspect_inputs(project['id'],'step',{'instruction':'Read data'},root)=='' and not calls
    enable();text=inspect_inputs(project['id'],'step',{'instruction':'Read data'},root)
    assert len(text)<13000 and 'row-9999' not in text
    assert '10000' in text and 'first_items' in text


def test_failure_reads_new_diagnostics_instead_of_caching_only_initial_input_files(project,monkeypatch):
    enable();root=config.project_dir(project['id'])/'workspace';root.mkdir()
    (root/'info.json').write_text('{"optimal_shift":{"dx":0,"dy":0}}','utf-8')
    calls=[]
    def generate(self,prompt,schema):
        calls.append(prompt)
        return InputInspection(paths=['checks_report.json','info.json'] if 'ACTUAL_FAILURE' in prompt else ['info.json'])
    monkeypatch.setattr(input_inspection.BudgetedModel,'generate',generate)
    inspect_inputs(project['id'],'step',{'instruction':'Check shift'},root)
    report={'frames':[{'status':'PASS','large':'x'*1000} for _ in range(8)],'shift':[{'status':'FAIL','details':{'error':'ACTUAL_MISSING_FIELDS','missing_keys':['best_dx','best_dy'],'info_keys':['optimal_shift','sad_min']}}]}
    (root/'checks_report.json').write_text(json.dumps(report),'utf-8')
    text=inspect_inputs(project['id'],'step',{'instruction':'Check shift'},root,previous_error='ACTUAL_FAILURE')
    assert len(calls)==2 and 'ACTUAL_MISSING_FIELDS' in text and 'best_dx' in text and 'optimal_shift' in text
    assert 'x'*1000 not in text


def test_verbose_first_reports_cannot_hide_later_selected_data_shapes(project,monkeypatch):
    enable();root=config.project_dir(project['id'])/'workspace';root.mkdir()
    names=['report.txt','report_copy.txt','metrics.csv','parameters.json','actual_fields.json']
    for name in names[:2]:(root/name).write_text('successful diagnostic '*2000,'utf-8')
    (root/'metrics.csv').write_text('k,PSNR\n3,34.7','utf-8')
    (root/'parameters.json').write_text('{"step":1}','utf-8')
    (root/'actual_fields.json').write_text('{"optimal_shift":{"dx":0,"dy":0},"sad_min":8.8}','utf-8')
    monkeypatch.setattr(input_inspection.BudgetedModel,'generate',lambda *a:InputInspection(paths=names))
    text=inspect_inputs(project['id'],'step',{'instruction':'Verify results'},root)
    assert all(name in text for name in names)
    assert 'optimal_shift' in text and 'sad_min' in text and 'k,PSNR' in text
    assert len(text)<13000


def test_verbose_complete_selection_reason_keeps_same_wire_schema():
    import pytest
    from pydantic import ValidationError
    from study.input_inspection import InputInspection
    reason='reason '+('x'*1000)
    assert InputInspection(paths=['inputs/data.json'],why=reason).why==reason
    assert InputInspection.model_json_schema()['properties']['why']['maxLength']==800
    with pytest.raises(ValidationError):InputInspection(paths=[],why='x'*4001)
    with pytest.raises(ValidationError):InputInspection(paths=['file']*9,why=reason)
