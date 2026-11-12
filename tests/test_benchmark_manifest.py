import copy
import hashlib

import pytest

from study.benchmark_manifest import GATES, check_manifest


@pytest.fixture
def complete(tmp_path):
    def pin(name):
        path = tmp_path / name
        path.write_text(name, encoding='utf-8')
        return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    case = dict(id='new-topic', family='algorithms', profile='python', kind='lab',
                exposure='independent', status='passed', criterion='Check invariant and counterexample',
                next_action='Review evidence', sources=[pin('primary.txt')], gates={})
    for key in ('brief', 'oracle', 'resources', 'freeze', 'selection_record', 'result', 'independent_review'):
        case[key] = pin(key + '.json')
    case['gates'] = {gate: pin(gate + '.json') for gate in GATES}
    return tmp_path, {'schema_version': 1, 'cases': [case]}


def test_pinned_evidence_is_not_semantic_acceptance(complete):
    root, manifest = complete
    result = check_manifest(manifest, root)
    assert not result['errors']
    assert result['evidence_complete'] == 1
    assert 'No semantic acceptance' in result['limitation']


@pytest.mark.parametrize('field', ['brief', 'oracle', 'resources', 'freeze', 'result',
                                  'independent_review', 'selection_record', 'sources', 'gates'])
def test_missing_required_evidence_rejects_claim(complete, field):
    root, manifest = complete
    del manifest['cases'][0][field]
    result = check_manifest(manifest, root)
    assert result['errors']
    assert result['evidence_complete'] == 0


@pytest.mark.parametrize('path', ['../outside', 'D:/private.txt', '.env', 'folder/.env.local', r'..\outside'])
def test_unsafe_reference_rejected_without_read(complete, path):
    root, manifest = complete
    manifest['cases'][0]['brief']['path'] = path
    result = check_manifest(manifest, root)
    assert 'brief: unsafe path' in result['cases'][0]['issues']


def test_changed_and_missing_files(complete):
    root, manifest = complete
    (root / 'primary.txt').write_text('changed')
    (root / 'oracle.json').unlink()
    result = check_manifest(manifest, root)
    assert 'source[0]: sha256 mismatch' in result['cases'][0]['issues']
    assert 'oracle: missing file' in result['cases'][0]['issues']
    assert result['evidence_complete'] == 0


def test_oracle_copy_in_prompt_detected_by_hash(complete):
    root, manifest = complete
    case = manifest['cases'][0]
    (root / 'brief.json').write_bytes((root / 'oracle.json').read_bytes())
    case['brief']['sha256'] = case['oracle']['sha256']
    assert 'oracle overlaps producer input' in check_manifest(manifest, root)['cases'][0]['issues']


def test_reserved_or_mechanism_cannot_count_as_lab(complete):
    root, manifest = complete
    case = manifest['cases'][0]
    case['kind'] = 'mechanism'
    assert check_manifest(manifest, root)['evidence_complete'] == 0
    case['kind'] = 'lab'
    case['exposure'] = 'reserved_unseen'
    assert check_manifest(manifest, root)['errors']


def test_empty_planned_slots_are_honest_and_not_ready(tmp_path):
    manifest = {'schema_version': 1, 'cases': [dict(
        id='any', family='unknown', profile='pending', kind='lab', exposure='development',
        status='planned', criterion='pending source selection', next_action='find primary source')]}
    result = check_manifest(manifest, tmp_path)
    assert not result['errors']
    assert not result['cases'][0]['ready']
    assert result['evidence_complete'] == 0


def test_duplicate_ids_and_unknown_status(complete):
    root, manifest = complete
    manifest['cases'].append(copy.deepcopy(manifest['cases'][0]))
    manifest['cases'][1]['status'] = 'green'
    assert len(check_manifest(manifest, root)['errors']) == 2


def test_symlink_escape_rejected(complete, tmp_path):
    root, manifest = complete
    external = tmp_path.parent / (tmp_path.name + '-external.txt')
    external.write_text('outside', encoding='utf-8')
    try:
        try:
            (root / 'link.txt').symlink_to(external)
        except OSError:
            pytest.skip('OS does not permit symlink creation')
        manifest['cases'][0]['brief']['path'] = 'link.txt'
        result = check_manifest(manifest, root)
        assert 'brief: path escapes repository' in result['cases'][0]['issues']
    finally:
        external.unlink()


def test_failed_attempt_requires_evidence_and_never_counts(complete):
    root, manifest = complete
    case = manifest['cases'][0]
    case['status'] = 'failed'
    assert not check_manifest(manifest, root)['errors']
    assert check_manifest(manifest, root)['evidence_complete'] == 0
    del case['result']
    assert check_manifest(manifest, root)['errors']


def test_pinned_files_do_not_override_declared_resource_or_approval_blocker(complete):
    root, manifest = complete
    case = manifest['cases'][0]
    case['missing'] = ['External dependency unverified', 'Plan not approved']
    result = check_manifest(manifest, root)
    assert result['errors'] and not result['cases'][0]['ready']
    assert result['evidence_complete'] == 0
    case['status'] = 'planned'
    result = check_manifest(manifest, root)
    assert not result['errors'] and not result['cases'][0]['ready']
