import pytest
from study.checker_runner import run


@pytest.mark.parametrize('source',[
    'def test():\n    assert 2+2==4\n',
    'assert True\n',
    'if False:\n    assert 2+2==4\n',
    'import sys\nsys.exit(0)\nassert 2+2==4\n',
])
def test_uncalled_or_trivial_checks_fail(tmp_path,source):
    path=tmp_path/'check.py';path.write_text(source,'utf-8')
    with pytest.raises(RuntimeError,match='assert'):
        run(path)


def test_executed_asserts_are_recorded_and_failures_propagate(tmp_path):
    path=tmp_path/'check.py'
    path.write_text('def test():\n    assert 2+2==4\ntest()\nassert 4>1\n','utf-8')
    assert run(path)==2
    path.write_text('assert 2+2==5\n','utf-8')
    with pytest.raises(AssertionError):
        run(path)
