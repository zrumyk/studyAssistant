"""Bounded, durable recovery of one study-guide section after truncation.

The model reviewer is advisory for the study guide: a malformed review or remarks
that survive one repair do not stop the whole defense. Such a section is kept with
a visible note listing what was not verified, so the student sees the doubt.
"""
import hashlib
import json
from pydantic import ValidationError
from . import db, config
from .providers import ModelOutputTruncated
from .schemas import ContentReview
from .reports import review_section_structure

MAX_REPAIRS = 1


def unverified_note(reason, remarks=()):
    lines = ['> **Розділ не пройшов автоматичну перевірку.** ' + reason]
    lines += ['> - ' + str(item).replace('\n', ' ') for item in remarks]
    lines.append('> Перевірте цей розділ самостійно перед захистом.')
    return '\n'.join(lines) + '\n\n'


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def reviewed_section(pid, heading, prompt, headings, state, save, model, reviewer, cancelled=lambda: False):
    policy = {k: config.settings()[k] for k in ('cloud_provider', 'cloud_model', 'cloud_max_output')}
    key = digest(json.dumps(['defense-split-v1', prompt, headings, policy], ensure_ascii=False))
    progress = state.setdefault('section_progress', {})
    record = progress.get(heading)
    if not isinstance(record, dict) or record.get('context_sha256') != key:
        record = {'context_sha256': key, 'split': False, 'parts': {}, 'receipts': []}
        progress[heading] = record

    def guard():
        if cancelled():
            raise RuntimeError('Підготовку до захисту призупинено; перевірені частини збережено')

    def request(client, text, schema=None):
        guard()
        try:
            return client.generate(text, schema) if schema else client.generate(text)
        finally:
            rid = getattr(client, 'last_request_id', None)
            if rid and rid not in record['receipts']:
                record['receipts'].append(rid)
            save()

    def accepted(item):
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            return False
        if item.get('text_sha256') != digest(item['text']):
            return False
        if item.get('unverified'):
            return True
        try:
            review = ContentReview.model_validate(item['review'])
        except (ValueError, KeyError, TypeError):
            return False
        return review_section_structure(review, heading, item['text'], headings).satisfied

    existing = state['sections'].get(heading)
    if existing and existing.get('context_sha256') == key and accepted(existing):
        return existing['text']
    if record.get('terminal_truncation'):
        raise RuntimeError('Частина підготовки вже повторно обрізана; автоматичні повтори зупинено: ' + heading)

    review_rule = (
        'Перевір навчальне пояснення за умовою, джерелами та фактичними результатами. '
        'Блокуй неправильну математику, хибне пояснення коду, вигадані результати й дії. '
        'Навчальні приклади й явно позначені рекомендації не є виконаними експериментами. '
        'Перевір саме вказану частину, не вимагай повторення інших розділів. '
        'blocking_issues лише для суттєвих помилок; стиль у notes.\n'
    )

    def review_of(text_prompt):
        """One review; a malformed response gets one differently worded retry, then None."""
        try:
            return request(reviewer, text_prompt, ContentReview)
        except ValidationError:
            db.event(pid, 'defense_review_invalid', {'heading': heading})
        try:
            return request(reviewer, text_prompt + '\nПопередня відповідь перевірки не була коректним JSON. '
                           'Поверни лише один JSON-об’єкт за схемою, без тексту поза ним.', ContentReview)
        except ValidationError:
            return None

    def keep_unverified(item, text, reason, remarks=()):
        text = unverified_note(reason, remarks) + text
        item.update(text=text, unverified=reason, remarks=list(remarks))
        item.pop('draft', None)
        item['text_sha256'] = digest(text)
        save()
        db.event(pid, 'defense_section_unverified', {'heading': heading, 'reason': reason})
        return text

    def produce(part_id, instruction):
        item = record['parts'].setdefault(part_id, {'repairs': 0})
        if accepted(item):
            return item['text']
        if 'draft' not in item:
            item['draft'] = request(model, instruction)
            item['generation_request_id'] = getattr(model, 'last_request_id', None)
            save()
        while True:
            review = review_of(review_rule + instruction + '\nТекст для перевірки:\n' + item['draft'])
            if review is None:
                return keep_unverified(item, item['draft'], 'Відповідь рецензента двічі була некоректною.')
            review = review_section_structure(review, heading, item['draft'], headings)
            if review.satisfied:
                item.update(text=item.pop('draft'), review=review.model_dump(),
                            review_request_id=getattr(reviewer, 'last_request_id', None))
                item['text_sha256'] = digest(item['text'])
                item['context_sha256'] = digest(instruction)
                save()
                return item['text']
            item['last_review'] = review.model_dump()
            save()
            if item['repairs'] >= MAX_REPAIRS:
                return keep_unverified(item, item['draft'], 'Після виправлення рецензент залишив зауваження:',
                                       review.blocking_issues)
            instruction_repair = instruction + '\nВиправ ці помилки: ' + json.dumps(review.model_dump(), ensure_ascii=False) + '\nПопередній текст:\n' + item['draft']
            item['draft'] = request(model, instruction_repair)
            item['generation_request_id'] = getattr(model, 'last_request_id', None)
            item['repairs'] += 1
            save()

    if not record['split']:
        # A legacy accepted section is re-reviewed against the current exact
        # context, without paying to rewrite it merely to migrate the checkpoint.
        if existing and not existing.get('context_sha256'):
            try:
                legacy = ContentReview.model_validate(existing['review'])
                if legacy.satisfied:
                    record['parts'].setdefault('whole', {'repairs': 0, 'draft': existing['text']})
            except (ValueError, KeyError, TypeError):
                pass
        try:
            text = produce('whole', prompt)
        except ModelOutputTruncated:
            record['split'] = True
            save()
            db.event(pid, 'defense_section_split', {'heading': heading, 'context_sha256': key})
        else:
            state['sections'][heading] = record['parts']['whole'] | {'context_sha256': key, 'model': getattr(model, 'last_source', 'local')}
            save()
            return text

    try:
        first = produce('1', prompt + '\nСтвори лише першу з двох частин цього розділу: основи, вхідні дані й початок пояснення. До 600 слів. Другу частину з продовженням буде створено окремо. Не повторюй заголовок розділу.')
        guard()
        second = produce('2', prompt + '\nСтвори лише другу з двох частин: продовження, фактичні результати, перевірки й обмеження, доречні саме цьому розділу. До 600 слів. Доповни першу частину без повторення.\nПерша перевірена частина (дані):\n' + first)
        text = first + '\n\n' + second
        # Part acceptance does not establish completeness or consistency of the
        # assembled section. Never render it until this whole-section review passes.
        review = review_of(review_rule + prompt + '\nПеревір також цілісність двох частин і відсутність суперечностей та дублювання.\nТекст для перевірки:\n' + text)
    except ModelOutputTruncated:
        record['terminal_truncation'] = True
        save()
        raise RuntimeError('Повторне обрізання частини підготовки; автоматичні повтори зупинено: ' + heading)
    if review is not None:
        review = review_section_structure(review, heading, text, headings)
    if review is None or not review.satisfied:
        merged = {'repairs': 0}
        reason = ('Відповідь рецензента двічі була некоректною.' if review is None
                  else 'Об’єднаний розділ має зауваження рецензента:')
        text = keep_unverified(merged, text, reason, [] if review is None else review.blocking_issues)
        state['sections'][heading] = merged | {'context_sha256': key, 'model': getattr(model, 'last_source', 'local'), 'split': True}
        save()
        return text
    state['sections'][heading] = {'text': text, 'text_sha256': digest(text), 'context_sha256': key,
                                  'review': review.model_dump(), 'review_request_id': getattr(reviewer, 'last_request_id', None),
                                  'model': getattr(model, 'last_source', 'local'), 'split': True}
    save()
    return text
