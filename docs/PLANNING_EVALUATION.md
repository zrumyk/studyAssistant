# SA-105/SA-106/SA-107 — перевірка планувального reviewer


Поточне продовження: [контроль output budget/model та production](PLANNING_REVIEWER_CONTROL_2026-11-03.md).
Production пройшов7 semantic +2 structural controls; нижчий початковий local
baseline збережений історично й не оголошується виправленим.

## Призначення

[Дев’ять frozen технічних кейсів](verification/planning-contract-cases-v1.json)
створені агентом до локального модельного запуску. Це development fixtures,
не лабораторні рішення, holdout чи незалежне приймання. Очікувані labels/rationale
не передаються моделі; вони потрібні лише для порівняння отриманих висновків.

`plan_review.py` містить той самий prompt, який викликає engine.analyse.
Його перенесення з engine перевірене на побайтово тотожні тексти для9 кейсів
проти незмінного source ZIP r6: [доказ](../data/verification/planning-review-extraction-2026-11-03.json).
Спільні profile facts тепер містять base image, entrypoint і точний run_argv
із RUNTIMES; доступність неоголошених compiler/package цим не припускається.

## Рівні перевірки

1. Structural gate: `validate_generated_plan` + `validate_contract(require=True)`.
   Тут відсутні повний analyse/RAG/source-citation/manual-grounding етапи, справжні
   імпортовані лабораторні дані й виконання student code. Gate відхиляє future-owner
   та report output mutation; profile mismatch і private binary у prose проходять
   структурно й повинні оцінюватися окремо.
2. Semantic review: той самий production prompt + Assessment schema, справжня
   встановлена локальна модель через OllamaProvider. Немає BudgetedModel/cloud
   fallback; paid calls0. Зберігаються prompt, model identity, parsed assessment/error,
   case/source hashes. Обрізаний raw local output поточний provider не зберігає;
   error лишається unknown і не зараховується як правильне відхилення.
3. Unit fixtures перевіряють сам вимірювач: always-accept і always-reject judges
   зобов’язані дати mismatch; labels не потрапляють у prompt; error не є verdict.
   Ці mock-тести не підтверджують семантичну здатність реальної моделі.

## Початкова локальна проба03.11.2026 (історія)

[Structural-only](../data/verification/planning-contract-structural-2026-11-03.json):
9/9 очікуваних structural результатів; semantic не запускалась.

[Локальна проба](../data/verification/planning-contract-local-2026-11-03.json):
qwen3.5:4b, context8192, temperature0.15, max_output384, timeout60s/запит,
300s міжкроковий загальний бюджет (поточний запит може завершитися пізніше).
Усі9 кейсів оброблені,7 model calls;2 structurally invalid не надсилалися reviewer.

| Кейси | Structural | Semantic фактично |
|---|---|---|
| T01 C/checker/transcript/report | pass | false reject: вигаданий fetch renderer |
| T02 Python profile з гарантованим gcc | pass | правильне відхилення |
| T03 JavaScript/report | pass | output truncated, unknown |
| T04 report outputs mutation | reject | не викликався |
| T05 SVG + справжня manual передача Inkscape | pass | false reject: manual трактовано як неможливу автоматизацію |
| T06 private C binary/stdout у новому Python | pass | output truncated, unknown |
| T07 Python→JS readonly JSON | pass | false reject: вигадані fetch/inputs вимоги |
| T08 обов’язкова manual дія замінена XML parser | pass | правильне відхилення |
| T09 future owner | reject | не викликався |

П’ять usable verdicts:2 правильні,3 хибні; ще2 unknown. Відомих false accepts
серед п’яти відповідей0, але це не підтвердження безпеки чи надійності. Ліміти
цієї локальної проби відрізняються від cloud reviewer; результат не переноситься
на нього й не доводить причинність лише моделі/лише prompt. Labels і prompt не
підлаштовувалися після відповідей. Модель вивантажено;7 local calls/$0 provider cost.

442 pytest passed, Ruff F821/F811 passed; UI не змінювався, frontend build цього
етапу не повторювався. G01 paused/unapproved;18 старих paid planning calls і
monthly ledger$0.262898958 незмінні, лабораторних runs0, full accepted labs0/4.

## Відтворення й наступний контроль

```powershell
.venv/Scripts/python.exe scripts/evaluate-planning-contracts.py --output data/verification/NEW-STRUCTURAL.json
.venv/Scripts/python.exe scripts/evaluate-planning-contracts.py --local-review --output data/verification/NEW-LOCAL.json
```

CLI create-exclusive: наявний evidence не перезаписується; partial record лишається
після помилки. Перед запуском потрібні jobs0, не менше11GiB диска; для локального
review —5GiB доступної RAM до завантаження та вже встановлена модель. CLI не
завантажує нові моделі, не змінює settings і не створює/погоджує лабораторних планів.

SA-107: наступний контроль має окремо порівняти bounded local output та чинний
budgeted cloud reviewer на незмінених technical cases; зафіксувати обмеження,
receipts і кожен false accept/reject/unknown. До нового paid запиту прочитати
кеш і визначити, яка саме гіпотеза перевіряється; без повтору G01/ручного ремонту
лабораторного плану. Невідомий результат не замінювати позитивною оцінкою.
