# SA121 — походження повідомлень для ремонту плану

05.11.2026. Загальний механізм,без умов за дисципліною,project ID або очікуваними результатами. Дані/числа/student Plan не виправляються оператором.

`RepairFeedback` зберігає controller-assigned origin/check/detail. Engine розрізняє local_validator,semantic_review,model_output_validation,source_clarification і historical_job_error. Некласифікований caller-supplied текст залишається unclassified;його слова,exception prefixes або вкладений JSON не призначають собі origin чи verified status.

Змістове зауваження reviewer і успадкований job error не називаються підтвердженими constraints. Точна complete receipt доводить отримання заяви,а не істинність її пояснення. Зокрема requires_replan або «більше6 edits» не є незалежним доказом мінімальної кількості змін. Це не примушує reviewer повернути satisfied=true і не скасовує справжніх вимог.

Перед patch prompt повторно виконується `validate_contract` для поточних декларацій. Цей вузький current_local_checks не перевіряє physical input existence без workspace,не доводить читання всіх джерел,змістову коректність,OCR/formulas чи успішність лабораторної. Статус passed не є прийманням Plan. Origin local_validator означає спрацювання конкретного правила,не математичне доведення всіх його інтерпретацій.

Непідтверджені зауваження не видаляються:передаються як JSON data окремо від current checks. Після невдалого patch зберігаються initial finding та latest failed repair з окремими origins;історія не росте безмежно. До3000 characters первинного detail і1200 останнього patch detail;для довгого тексту збережені head/tail з explicit middle-omission marker. Current local error також bounded1200. Правила джерел/пакетів/артефактів/semantic review/approval повторюються штатно.

Жодні caps,CSRF/session permissions,budget,$5/$0.03,patch max6 чи max2 recoveries/revision не змінені. G05 revision4 вичерпав2/2 operations;цей adapter не надає нової operation і не є підставою reset/replay. Старі request rows/checkpoint/statuses не редагуються.

94 focused controls:Python/TypeScript/C ownership fixtures,valid graph проти unsupported claim,untrusted JSON/prefix injection,head/tail bounds,retained reviewer origin after invalid patch,current local validator і inherited recovery integration. [Actual read-only probe](../data/verification/g05-feedback-provenance-offline-2026-11-05.json):old model claim historical/unconfirmed,current artifact conflict separate,24 request rows unchanged,new calls0. Це не позитивна real model repair verification.

SA121 ширше залишається на перевірці:потрібне незалежне підтвердження достовірності змістових зауважень на допустимих нових inputs після freeze. SA117 model SVG repair — незалежна наступна задача;G02 old approval чинний,успішні обчислення не повторювати.
