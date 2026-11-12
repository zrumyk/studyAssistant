import hashlib

import pytest
from pydantic import ValidationError

from study.execution_protocol import stdin_bytes, stdin_identity
from study.schemas import Action, PythonProgram


def test_input_identity_tracks_bytes_and_preserves_unicode_and_newlines():
    value = 'дані\n\r\n\x00end'
    assert stdin_bytes(value) == value.encode('utf-8')
    assert stdin_identity(value)['sha256'] == hashlib.sha256(value.encode('utf-8')).hexdigest()
    assert stdin_identity(None) == stdin_identity('')
    assert stdin_identity('1\n') != stdin_identity('1')


def test_limit_applies_to_encoded_bytes_not_character_count():
    assert len(stdin_bytes('x' * 65536)) == 65536
    with pytest.raises(ValueError):
        stdin_bytes('я' * 40000)
    with pytest.raises(ValidationError):
        Action(explanation='test', stdin_text='я' * 40000)
    with pytest.raises(ValidationError):
        Action(explanation='test', stdin_text=123)


def test_old_actions_keep_eof_and_python_program_preserves_input():
    assert Action(explanation='old checkpoint').stdin_text == ''
    program = PythonProgram(main_code='print(input())', checker_code='assert 2+2==4',
                            explanation='test', stdin_text='дані\n')
    assert program.as_action().stdin_text == 'дані\n'


@pytest.mark.parametrize('data,expected', [(b'0', 0), (b'255', 255), (b'256', None),
                                        (b'0'*10000, None), (b'0\xff', None),
                                        (b'', None), (b'-1', None), (b'1\n', None)])
def test_status_parser_is_bounded_and_rejects_malformed_data(isolated, data, expected):
    from study.execution_protocol import read_exit_status
    path = isolated / 'status'
    path.write_bytes(data)
    assert read_exit_status(path) == expected


def test_execution_loop_preserves_input_and_checker_paths(project, plan, monkeypatch):
    from study import engine, db
    pid = project['id']
    plan['steps'][0]['language'] = 'c'
    engine.save_plan(pid, plan)
    engine.approve(pid)
    action = Action(files=[{'path': 'main.c', 'content': 'int main(void){return 1;}'}],
                    checks=[{'path': 'check_result.py', 'content': 'assert 2+2==4'}],
                    entrypoint='main.c', stdin_text='дані\n', explanation='technical fixture')
    monkeypatch.setattr(engine, 'prepare_action', lambda *args: action)
    monkeypatch.setattr(engine.OllamaProvider, 'unload', lambda self: None)
    observed = []

    def run(*args, **kwargs):
        observed.append(kwargs)
        return dict(exit_code=1, reason='', oom=False, log='controlled failure',
                    run_id='a'*32, produced_files=[], files=[])

    monkeypatch.setattr(engine.DockerRuntime, 'run', run)
    engine.execute_project(pid)
    assert observed
    assert all(row['stdin_text'] == 'дані\n' and row['check_paths'] == ['check_result.py'] for row in observed)
    assert db.project(pid)['status'] == 'needs_user'
