from types import SimpleNamespace

import pytest

from study import runtime


@pytest.mark.parametrize('language', ['javascript', 'typescript'])
def test_registry_scopes_and_exact_versions(language):
    items = ['@types/mocha', '@example/api@1.2.3-beta.1+build.7', 'lodash@4.17.21']
    assert runtime.dependencies(language, items + items) == sorted(items)


@pytest.mark.parametrize('item', [
    '--registry=evil', '@scope/pkg;id', '@scope/pkg\nid', '@scope/../pkg',
    '@scope/pkg@$(id)', 'https://evil/pkg.tgz', 'file:../pkg', 'git+https://evil/x',
    'pkg@^1.2.3', 'pkg@1.2.3 || id', 'pkg@latest', 'pkg==1.2.3',
    '@scope', '@scope/', '@scope/pkg/sub', 'Uppercase', 'x' * 215,
])
@pytest.mark.parametrize('language', ['javascript', 'typescript'])
def test_registry_specs_cannot_escape_install_argument(language, item):
    with pytest.raises(ValueError):
        runtime.dependencies(language, [item])


@pytest.mark.parametrize('item', ['typescript@4.8.4', '@types/node@20.0.0'])
def test_conflicting_bootstrap_pin_stops_before_docker(item, monkeypatch):
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: pytest.fail('No Docker before validation'))
    with pytest.raises(ValueError, match='окремого профілю'):
        runtime.DockerRuntime().prepare('typescript', [item])


def test_scoped_dependency_reaches_image_recipe_without_duplicate_bootstrap(isolated, monkeypatch):
    from study import disk_guard

    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    monkeypatch.setattr(runtime.shutil, 'disk_usage', lambda *a: SimpleNamespace(free=30 * 1024**3))
    monkeypatch.setattr(disk_guard, 'build', lambda *a: 0)
    runtime.DockerRuntime().prepare('typescript', ['@types/mocha@10.0.10', 'typescript', 'typescript@5.7.3'])
    recipe = next((isolated / 'runtime-builds').glob('*/Dockerfile')).read_text()
    assert 'RUN npm install --no-audit --no-fund @types/mocha@10.0.10 @types/node@22.10.2 typescript@5.7.3\n' in recipe
    assert recipe.count('typescript@5.7.3') == 1


def test_registry_grammar_does_not_leak_into_python():
    assert runtime.dependencies('python', ['numpy==2.0.0']) == ['numpy==2.0.0']
    with pytest.raises(ValueError):
        runtime.dependencies('python', ['@types/mocha'])
