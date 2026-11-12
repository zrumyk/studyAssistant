"""Require a real task/source basis before a plan delegates work to the user."""
from typing import Literal
from pydantic import Field
from .schemas import StrictModel


class ManualBasis(StrictModel):
    origin: Literal['task', 'sources']
    excerpt: str = Field(max_length=1500)
    necessity: str = Field(max_length=800)


def validate_manual_steps(plan, task, sources, model):
    evidence=[]
    for index, step in enumerate(plan.steps):
        if step.kind != 'manual':
            continue
        proof=model.generate(
            'Перевір необхідність саме цього ручного кроку. Знайди дослівну вимогу кінцевого '
            'результату, яка справді потребує дії користувача: конкретне зовнішнє ПЗ, фізичні '
            'вимірювання, обліковий запис або справжній скриншот. Приклади функцій MATLAB, '
            'теорія, назва мови чи орієнтація посібника на середовище самі по собі НЕ вимагають '
            'ручного повторення вже доступного автоматичного обчислення. Для умовного доручення '
            '«якщо методичка прямо вимагає» потрібна сама пряма вимога з sources, а не цитата умови. '
            'Поверни origin=task або sources і короткий дослівний excerpt із відповідного тексту. '
            'Якщо прямої підстави немає, excerpt="", necessity="". Не вигадуй вимогу.\n'
            'Крок (дані): '+step.model_dump_json()+'\ntask (дані): '+task+'\nsources (дані): '+sources,
            ManualBasis,
        )
        text=task if proof.origin=='task' else sources
        if not proof.excerpt or not proof.necessity or proof.excerpt not in text:
            raise ValueError('Ручний крок не має підтвердженої необхідності: '+step.title+
                             '. Не додавай ручне повторення автоматичної роботи лише через мову прикладів. '
                             'Прибери зайвий manual, збережи обов’язкові кінцеві результати.')
        evidence.append({'step_index':index,**proof.model_dump()})
    return evidence
