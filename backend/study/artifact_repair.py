"""Bounded observations and preservation rules, never replacement student plans."""
import json
import hashlib
from pathlib import Path

from .artifact_contracts import ArtifactOwnershipError, covers, overlaps, validate_contract


def graph_repair_context(draft):
    """Only a locally proven ownership error activates the narrow repair scope."""
    try:
        validate_contract(draft, require=True)
    except ArtifactOwnershipError:
        pass
    except ValueError:
        return None  # Other failures retain their own repair/review workflow.
    else:
        return None
    outputs = [(i, path) for i, step in enumerate(draft.steps) for path in step.artifact_outputs]
    inputs = [(i, path) for i, step in enumerate(draft.steps) for path in step.artifact_inputs]
    result = {'version': 1, 'complete': True, 'issues': [], 'affected_fields': [],
              'scope_enforced': False, 'omission_reason': None}
    if len(inputs) + len(outputs) > 800:
        result.update(complete=False, omission_reason='More than800 artifact declarations; no exhaustive scope')
        return result
    issues = []
    for position, (owner, path) in enumerate(outputs):
        for other_owner, other in outputs[:position]:
            if overlaps(path, other):
                issues.append({'kind': 'overlapping_producers', 'output': [owner, path],
                               'other_output': [other_owner, other]})
        for consumer, other in inputs:
            if consumer == owner and overlaps(path, other):
                issues.append({'kind': 'overwritten_input', 'output': [owner, path],
                               'input': [consumer, other]})
        if len(issues) > 32:
            result.update(complete=False, omission_reason='More than32 ownership witnesses; no exhaustive scope')
            break
    result['issues'] = issues[:32]
    conflict_paths = {edge[1] for issue in result['issues'] for key, edge in issue.items() if key != 'kind'}
    affected = set()
    consumers = []
    for index, path in inputs:
        if any(overlaps(path, conflict) for conflict in conflict_paths):
            consumers.append([index, path])
            affected.add(index)
    for index, path in outputs:
        if any(overlaps(path, conflict) for conflict in conflict_paths):
            affected.add(index)
    result['consumers'] = consumers
    result['conflict_paths'] = sorted(conflict_paths)
    result['affected_fields'] = [f'/steps/{i}/{field}' for i in sorted(affected)
                                 for field in ('artifact_inputs', 'artifact_outputs', 'instruction', 'expected')]
    # Never truncate JSON and imply that the partial scope is exhaustive.
    if len(json.dumps(result, ensure_ascii=False).encode()) > 12000:
        return {'version': 1, 'complete': False, 'issues': [], 'affected_fields': [],
                'scope_enforced': False, 'omission_reason': 'Witness payload exceeds12000bytes'}
    result['scope_enforced'] = bool(result['complete'] and issues)
    return result


def validate_graph_patch(draft, patch):
    """Reject collateral fields/edges before applying a graph-only model patch.

    This is not semantic verification of instruction text. All normal validators
    and the reviewer must still run on the resulting whole plan.
    """
    context = graph_repair_context(draft)
    if not context or not context['scope_enforced'] or patch.requires_replan:
        return
    if any(edit.op != 'replace' or edit.path not in context['affected_fields'] for edit in patch.edits):
        raise ValueError('Graph repair scope: змінено поле поза підтвердженим ownership conflict; жодних змін не застосовано')
    changed_edges = []
    for edit in patch.edits:
        parts = edit.path.split('/')
        if parts[-1] not in ('artifact_inputs', 'artifact_outputs'):
            continue
        before, after = json.loads(edit.before_json), json.loads(edit.after_json)
        if not isinstance(before, list) or not isinstance(after, list) or not all(isinstance(p, str) for p in before + after):
            raise ValueError('Graph repair scope потребує списки file paths')
        index = int(parts[2])
        actual = getattr(draft.steps[index], parts[-1])
        if before != actual:
            raise ValueError('Graph repair scope before_json не збігається')
        changed_edges.append((index, parts[-1], before, after))
    conflicts = context['conflict_paths']
    def related(path):
        return any(overlaps(path, conflict) for conflict in conflicts)
    # New paths must come from a changed conflicting output, not arbitrary
    # renames of an unrelated log/summary chain. Paths are observed, not chosen.
    introduced = set()
    projected_outputs = [list(step.artifact_outputs) for step in draft.steps]
    for index, field, before, after in changed_edges:
        if field == 'artifact_outputs' and any(related(path) for path in before):
            introduced.update(path for path in after if path not in before)
            projected_outputs[index] = after
    for index, field, before, after in changed_edges:
        old_unrelated = [path for path in before if not related(path)]
        allowed_additions = set(introduced)
        if field == 'artifact_inputs':
            allowed_additions.update(path for path in after if any(
                covers(output, path) for outputs in projected_outputs[:index] for output in outputs))
        new_unrelated = [path for path in after if not related(path)
                         and not (path not in before and path in allowed_additions)]
        if old_unrelated != new_unrelated:
            raise ValueError('Graph repair scope: unrelated artifact paths змінено; жодних змін не застосовано')
        if field == 'artifact_outputs' and not any(related(path) for path in before) and before != after:
            raise ValueError('Graph repair scope: unrelated producer змінено; жодних змін не застосовано')


def graph_policy_marker(draft, context=None):
    context = graph_repair_context(draft) if context is None else context
    if not context or not context['scope_enforced']:
        return None
    identity = {'version': 1, 'context': context,
                'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return 'GraphRepairPolicy:' + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                                           separators=(',', ':')).encode()).hexdigest()


def graph_prompt(draft):
    context = graph_repair_context(draft)
    if context is None:
        return ''
    return ('\n' + (graph_policy_marker(draft, context) or 'GraphRepairPolicy:incomplete') +
            '\nЛокальні ownership witnesses (JSON data; zero-based steps): ' +
            json.dumps(context, ensure_ascii=False, separators=(',', ':')) +
            '\nЯкщо scope_enforced=true:спершу виправ лише цей доведений graph conflict;інші semantic зауваження перевіримо після нього. '
            'Лише replace за affected_fields. Збережи всі unrelated artifact paths/edges і порядок;не перейменовуй logs/summary без ownership conflict. '
            'Поле списку можна виправити одним replace,а не окремою edit на кожний файл. '
            'Обери мінімальні необхідні зміни самостійно з джерел,не розв’язуй лабораторну;witness не є готовим ремонтом. '
            'Якщо scope incomplete,не вважай affected_fields повним. Max6 edits лишається;requires_replan — явна зупинка,не додатковий retry.')
