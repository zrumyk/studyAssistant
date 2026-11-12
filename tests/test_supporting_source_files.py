import pytest

from study.schemas import PythonProgram
from study.prepared import validate


def program(**kwargs):
    return PythonProgram(main_code='print("driver")', checker_code='assert 2+2==4', explanation='Technical fixture', **kwargs)


def test_legacy_single_file_response_stays_compatible():
    action = program().as_action()
    assert [file.path for file in action.files] == ['main.py']
    assert [file.path for file in action.checks] == ['check_current_step.py']


@pytest.mark.parametrize('path', ['app/cli.py', 'package/helper.py', 'src/query.sql'])
def test_declared_source_is_supplied_as_source_not_runtime_data(tmp_path, path):
    text = 'def available():\n    return 4\n' if path.endswith('.py') else 'select 1;'
    proposal = program(supporting_files=[{'path': path, 'content': text}])
    action = proposal.as_action()
    spec = {'kind': 'code', 'language': 'python', 'artifact_inputs': [], 'artifact_outputs': [path]}
    validate(action, spec, tmp_path, contract_version=1)
    assert next(file.content for file in action.files if file.path == path) == text
    assert not action.expected_files


@pytest.mark.parametrize('paths', [
    ['main.py'], ['MAIN.PY'], ['check_current_step.py'], ['inputs/seed.py'],
    ['../seed.py'], ['app/helper.py', 'APP/helper.py'],
])
def test_supporting_files_cannot_replace_entrypoint_checker_input_or_alias(paths):
    with pytest.raises(ValueError):
        program(supporting_files=[{'path': name, 'content': 'pass'} for name in paths])


def test_consumer_source_input_is_still_readonly(tmp_path):
    action = program(supporting_files=[{'path': 'data/producer.py', 'content': 'pass'}]).as_action()
    spec = {'kind': 'code', 'language': 'python', 'artifact_inputs': ['data/producer.py'], 'artifact_outputs': []}
    with pytest.raises(ValueError, match='перезаписати'):
        validate(action, spec, tmp_path, contract_version=1)
