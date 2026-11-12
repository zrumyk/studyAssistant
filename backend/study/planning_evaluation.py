"""Development judge evaluation; expected labels never enter the judge prompt."""
import httpx
from .artifact_contracts import validate_contract
from .engine import validate_generated_plan
from .plan_review import plan_review_prompt
from .schemas import Assessment, Plan


def evaluate_case(case, platform_facts, reviewer=None):
    row = {'id': case['id'], 'expected_structural': case['expected_structural'],
           'expected_semantic': case['expected_semantic'], 'semantic': 'not_run'}
    try:
        plan = Plan.model_validate(case['plan'])
        validate_generated_plan(plan)
        validate_contract(plan, require=True)
    except ValueError as error:
        row.update(structural=False, structural_error=str(error))
    else:
        row['structural'] = True
        if reviewer is not None:
            prompt = plan_review_prompt(task=case['task'], variant='', sources=case['sources'],
                                        plan=plan, platform_facts=platform_facts)
            row['prompt'] = prompt
            try:
                assessment = reviewer(prompt, Assessment)
                row.update(semantic='reviewed', assessment=assessment.model_dump(),
                           semantic_match=assessment.satisfied == case['expected_semantic'])
            except (RuntimeError, ValueError, httpx.HTTPError) as error:
                row.update(semantic='error', semantic_error=str(error))
    row['structural_match'] = row['structural'] == case['expected_structural']
    return row


def summarize(rows, expected_count):
    reviewed = [row for row in rows if row['semantic'] == 'reviewed']
    return {
        'cases': len(rows),
        'complete': len(rows) == expected_count,
        'structural_mismatches': [row['id'] for row in rows if not row['structural_match']],
        'semantic_reviewed': len(reviewed),
        'false_accepts': [row['id'] for row in reviewed if row['assessment']['satisfied'] and not row['expected_semantic']],
        'false_rejects': [row['id'] for row in reviewed if not row['assessment']['satisfied'] and row['expected_semantic']],
        'errors': [row['id'] for row in rows if row['semantic'] == 'error'],
        'semantic_not_run': [row['id'] for row in rows if row['structural'] and row['semantic'] == 'not_run'],
    }
