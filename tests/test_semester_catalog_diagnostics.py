import json

import pytest

from study import config, semester


@pytest.fixture
def intake(client, tmp_path):
    folder = tmp_path / 'materials'
    folder.mkdir()
    config.save_settings({'semester_root': str(folder)})
    return folder


def test_relocated_same_name_never_inherits_old_scope(intake):
    tmp_path = intake
    for directory in ('Algorithms/task', 'Databases/task'):
        path = tmp_path / directory / 'lab.txt'
        path.parent.mkdir(parents=True)
        path.write_text(directory, encoding='utf-8')
    old = {'Old/theory/lab.txt': {'subject': 'Old', 'role': 'theory'}}
    catalog = tmp_path / '_catalog.json'
    catalog.write_text(json.dumps(old), encoding='utf-8')
    before = catalog.read_bytes()
    result = semester.preview()
    assert result['catalog_unmatched'] == ['Old/theory/lab.txt']
    assert {(f['subject'], f['role'], f['assignment_source']) for f in result['files']} == {
        ('Algorithms', 'task', 'folders'), ('Databases', 'task', 'folders')}
    assert catalog.read_bytes() == before


def test_exact_catalog_assignment_wins_and_no_warning_for_unsupported_existing_file(intake):
    tmp_path = intake
    (tmp_path / 'one.txt').write_text('primary source', encoding='utf-8')
    (tmp_path / 'raw.unknown').write_bytes(b'raw')
    assignments = {name: {'subject': 'Chosen', 'role': 'task'} for name in ('one.txt', 'raw.unknown')}
    (tmp_path / '_catalog.json').write_text(json.dumps(assignments), encoding='utf-8')
    result = semester.preview()
    assert not result['catalog_unmatched']
    assert result['files'][0]['assignment_source'] == 'catalog'
    assert result['files'][0]['subject'] == 'Chosen'
    assert result['unsupported'] == ['raw.unknown']


def test_catalog_cannot_disguise_unassigned_file_by_matching_basename(intake):
    tmp_path = intake
    (tmp_path / 'lab.txt').write_text('needs explicit subject', encoding='utf-8')
    (tmp_path / '_catalog.json').write_text(json.dumps({
        'old/lab.txt': {'subject': 'Other', 'role': 'task'}}), encoding='utf-8')
    result = semester.preview()
    assert result['unassigned'] == ['lab.txt']
    assert not result['files']
    with pytest.raises(ValueError, match='розподіліть'):
        semester.import_semester()


def test_invalid_catalog_structure_is_explained(intake):
    tmp_path = intake
    (tmp_path / '_catalog.json').write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError, match='_catalog.json'):
        semester.preview()
