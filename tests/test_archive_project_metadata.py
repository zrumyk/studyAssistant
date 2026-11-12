import io
import json
import stat
import zipfile

import pytest

from study import config, documents
from study.archive_metadata import project_configs
from study.input_metadata import imported_data_metadata
from study.runtime import effective_dependencies


def inspect(entries, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=compression) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        return project_configs(archive, archive.infolist())


def test_reads_declared_packages_scripts_and_tsconfig_without_running_them():
    data = inspect([('root/package.json', json.dumps({'scripts': {'test': 'echo DO_NOT_EXECUTE'},
        'devDependencies': {'comfy-tslint': '^0.1.9', '@types/mocha': '^5.2.7'}, 'privateToken': 'NOT_A_PROMPT_FIELD'})),
        ('root/tsconfig.json', '{"compilerOptions":{"strict":true,"target":"ES2020"}}'),
        ('root/main.ts', 'throw new Error("MUST_NOT_EXECUTE")')])
    assert len(data['files']) == 2
    package = data['files'][0]
    assert package['status'] == 'read' and len(package['sha256']) == 64
    assert package['declarations']['devDependencies']['comfy-tslint'] == '^0.1.9'
    assert 'privateToken' not in package['declarations']
    assert data['files'][1]['declarations']['compilerOptions']['strict'] is True


def test_lockfile_reports_root_declarations_not_transitive_packages():
    x = inspect([('package-lock.json', json.dumps({'lockfileVersion': 3, 'packages': {
        '': {'devDependencies': {'mocha': '^10'}}, 'node_modules/transitive': {'version': '9'}}}))])
    assert x['files'][0]['declarations'] == {'lockfileVersion': 3, 'root_package': {'devDependencies': {'mocha': '^10'}}}


@pytest.mark.parametrize('name', ['../package.json', '/package.json', 'C:/package.json'])
def test_unsafe_member_paths_are_not_read(name):
    x = inspect([(name, '{}')])
    assert not x['files'] or 'not read' in x['files'][0]['status']
    assert x['bytes_read'] == 0


def test_raw_backslash_member_is_not_normalized_into_a_trusted_config():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('a/package.json', '{}')
    # Windows ZipInfo normalizes names during writing; construct the raw input bytes.
    raw = stream.getvalue().replace(b'a/package.json', b'a\\package.json')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        x = project_configs(archive, archive.infolist())
    assert x['bytes_read'] == 0
    assert not x['files'] or 'not read' in x['files'][0]['status']


def test_symlink_is_not_read():
    info = zipfile.ZipInfo('package.json')
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    x = inspect([(info, 'outside.json')])
    assert x['bytes_read'] == 0 and 'not read' in x['files'][0]['status']


def test_duplicate_config_is_ambiguous_and_not_read():
    with pytest.warns(UserWarning, match='Duplicate'):
        x = inspect([('package.json', '{}'), ('package.json', '{"name":"other"}')])
    assert x['bytes_read'] == 0
    assert all('ambiguous' in row['status'] for row in x['files'])


@pytest.mark.parametrize('content', ['not JSON', '[]', '{"scripts":'])
def test_invalid_or_nonobject_configs_are_explicit(content):
    x = inspect([('package.json', content)])
    assert x['files'][0]['status'].startswith('unread:')


def test_large_and_high_ratio_configs_are_not_decompressed():
    for entries, compression in [([('package.json', 'x' * 270000)], zipfile.ZIP_STORED),
                                 ([('package.json', 'x' * 100000)], zipfile.ZIP_DEFLATED)]:
        x = inspect(entries, compression)
        assert x['bytes_read'] == 0 and 'budget' in x['files'][0]['status']


def test_large_selected_fields_are_not_silently_truncated():
    x = inspect([('package.json', json.dumps({'scripts': {'test': 'x' * 6000}}))])
    assert 'content not included' in x['files'][0]['status']
    assert 'declarations' not in x['files'][0]


def test_only_bounded_number_of_configs_are_read():
    x = inspect([(f'project{i}/package.json', '{}') for i in range(9)])
    assert len(x['files']) == 6 and x['omitted_candidates'] == 3


def test_project_metadata_remains_valid_json_when_omitted(project):
    for i in range(3):
        path = config.DATA / f'inputs{i}.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for j in range(30):
                archive.writestr(f'long_project_{j}_' + 'x' * 200 + '/data.txt', 'value')
        documents.import_file(path, 'data', project['subject'], project['id'])
    text = imported_data_metadata(project['id'])
    records = json.loads(text)
    assert len(text) <= 9000
    assert any('metadata_omitted' in r or 'remaining_records_omitted' in r for r in records)


def test_bootstrap_bare_and_exact_names_share_actual_install_profile():
    bare = effective_dependencies('typescript', ['typescript', '@types/node', 'mocha'])
    pinned = effective_dependencies('typescript', ['typescript@5.7.3', '@types/node@22.10.2', 'mocha'])
    assert bare == pinned == ['@types/node@22.10.2', 'mocha', 'typescript@5.7.3']
    with pytest.raises(ValueError, match='окремого профілю'):
        effective_dependencies('typescript', ['typescript@4.8.4'])


def test_review_facts_show_normalization_and_invalid_requests_without_installing():
    from study.schemas import Plan
    from study.plan_review import plan_spec_facts
    p = Plan.model_validate({'goal': 'Inspect', 'requirements': [], 'missing': [], 'conflicts': [],
        'report_sections': [], 'steps': [{'title': 'Check', 'kind': 'code', 'language': 'typescript',
        'instruction': 'Check', 'expected': 'JSON', 'dependencies': ['typescript', '@types/node']}]})
    row = json.loads(plan_spec_facts(p))['steps'][0]
    assert row['effective_install_requests'] == ['@types/node@22.10.2', 'typescript@5.7.3']
    p.steps[0].dependencies = ['typescript@4.8.4']
    row = json.loads(plan_spec_facts(p))['steps'][0]
    assert 'окремого профілю' in row['effective_install_requests']['validation_error']
