import copy
import json
from pathlib import Path

import httpx
import pytest

from study.planning_evaluation import evaluate_case, summarize
from study.planning_facts import planning_facts
from study.runtime import RUNTIMES
from study.schemas import Assessment


CASES = json.loads((Path(__file__).resolve().parents[1] /
                    'docs/verification/planning-contract-cases-v1.json').read_text('utf-8'))['cases']


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_structural_gate_keeps_semantic_unknowns_visible(case):
    row = evaluate_case(case, planning_facts())
    assert row['structural_match']
    assert row['semantic'] == 'not_run'
    assert 'semantic_match' not in row


def test_eval_catches_always_accepting_and_always_rejecting_judges():
    for verdict, expected in [(True, 'false_accepts'), (False, 'false_rejects')]:
        def judge(prompt, schema):
            return Assessment(satisfied=verdict, explanation='Deliberately broken technical judge')

        rows = [evaluate_case(case, planning_facts(), judge) for case in CASES]
        report = summarize(rows, len(CASES))
        assert report[expected]
        assert report['complete'] and report['semantic_reviewed'] == 7
        assert not report['structural_mismatches']


def test_ground_truth_labels_never_enter_judge_prompt():
    original = copy.deepcopy(CASES[0])
    original['rationale'] = 'SECRET_GROUND_TRUTH_SENTINEL'
    original['expected_semantic'] = False
    prompts = []

    def judge(prompt, schema):
        prompts.append(prompt)
        assert schema is Assessment
        assert 'SECRET_GROUND_TRUTH_SENTINEL' not in prompt
        assert 'expected_semantic' not in prompt
        assert 'report artifact registry' in prompt
        return Assessment(satisfied=True, explanation='Technical response')

    row = evaluate_case(original, planning_facts(), judge)
    assert row['semantic_match'] is False
    assert len(prompts) == 1


def test_structurally_invalid_plan_never_reaches_judge():
    def forbidden(*args):
        pytest.fail('Structural rejection must happen before review')

    row = evaluate_case(next(case for case in CASES if case['id'].startswith('T04')), planning_facts(), forbidden)
    assert row['structural'] is False and row['semantic'] == 'not_run'


@pytest.mark.parametrize('error', [RuntimeError('local timeout'), ValueError('invalid JSON'), httpx.ReadError('broken stream')])
def test_model_errors_are_not_counted_as_rejections(error):
    def judge(*args):
        raise error

    row = evaluate_case(CASES[0], planning_facts(), judge)
    report = summarize([row], len(CASES))
    assert row['semantic'] == 'error'
    assert report['errors'] == [CASES[0]['id']]
    assert not report['complete'] and not report['false_accepts'] and not report['false_rejects']


def test_profile_facts_match_real_runtime_commands():
    profiles = json.loads(planning_facts())['runtime_profiles']
    for language, (base, entry, argv) in RUNTIMES.items():
        assert profiles[language]['run_argv'] == argv
        assert profiles[language]['base_image'] == base
        assert profiles[language]['entrypoint'] == entry
