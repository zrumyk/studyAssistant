import pytest

from study import runtime


@pytest.mark.parametrize('language,checker', [('sql', 'check.py'), ('java', 'check.py')])
def test_unsupported_checker_never_silently_disappears_or_builds(language, checker, monkeypatch):
    monkeypatch.setattr(runtime, 'docker_available', lambda: pytest.fail('Docker contacted before capability check'))
    monkeypatch.setattr(runtime.DockerRuntime, 'prepare', lambda *a: pytest.fail('Unexpected build'))
    with pytest.raises(ValueError, match='не можна вважати виконаними'):
        runtime.DockerRuntime().run('fixture', 'step', language, [], 30, lambda: False,
                                    check_paths=[checker])


def test_empty_checks_keep_existing_profile_behavior(monkeypatch):
    monkeypatch.setattr(runtime, 'docker_available', lambda: False)
    monkeypatch.setattr('study.disk_guard.require_space', lambda: None)
    with pytest.raises(RuntimeError, match='Docker Desktop'):
        runtime.DockerRuntime().run('fixture', 'step', 'c', [], 30, lambda: False, check_paths=[])


@pytest.mark.parametrize('language,checker', [('c', 'check.c'), ('javascript', 'check.js')])
def test_native_checker_source_is_not_mistaken_for_shared_python_checker(language, checker, monkeypatch):
    monkeypatch.setattr(runtime, 'docker_available', lambda: pytest.fail('Docker contacted'))
    with pytest.raises(ValueError, match='Python .py'):
        runtime.DockerRuntime().run('fixture', 'step', language, [], 30, lambda: False, check_paths=[checker])
