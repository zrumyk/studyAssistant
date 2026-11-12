# SA125 — свідчення й межі ремонту графа файлів

04.11.2026. Загальний механізм для непогоджених чернеток;назви предметів,проєктів і очікувані результати не використовуються як умови.

`validate_contract` відділяє ownership failure через `ArtifactOwnershipError`,залишаючись сумісним із ValueError. Лише поточний локально підтверджений ownership failure активує graph scope. Інші помилки контракту або змістової перевірки зберігають свій штатний workflow. Історичний текст exception сам по собі не надає scope.

`artifact_repair.graph_repair_context` збирає всі overlapping producers і overwritten inputs;додає реальних споживачів,нульові індекси кроків і affected_fields. Використовує ті самі casefold/prefix overlap rules,що validator;наявність producer для input залишається чинним case-sensitive правилом. Це спостереження про декларації,не вибір правильного нового шляху чи готова PlanPatch.

Обмеження:до800 input/output declarations,32 witnesses,12000 UTF8 bytes JSON. При перевищенні coverage явно incomplete,scope_enforced=false;JSON не обрізається до невалідного фрагмента. Часткові свідчення не називаються вичерпними. Невеликі fixtures і явні негативні controls перевіряють межі;це не повний графічний або семантичний аналіз інструкцій.

Перед застосуванням model patch engine викликає `validate_graph_patch`. За complete scope дозволені тільки replace полів artifact_inputs/artifact_outputs/instruction/expected у кроках-власниках/споживачах конфліктних paths. Unrelated artifact paths і їхній порядок мають зберігатися. Дозволено додати input із фактично оголошеного попереднього producer,або з нового model-declared output,пов’язаного з конфліктом. Непов’язаний output producer не перейменовується. Поле списку змінюється одним replace,а не окремою edit на файл.

Gate не доводить,що instruction text,новий output або пропонований ремонт правильні/мінімальні. Він не переписує рішення,не вибирає paths і не доводить повну відповідність лабораторній. Після нього незмінно виконуються CAS/schema checks,whole-plan artifact/source/manual/semantic validators і окреме погодження користувача. Відхилений scope не записується як applied checkpoint.

Max6 edits/14000 patch bytes,normal analyse3 attempts,recovery2 attempts і2 operations/revision не змінені. requires_replan зупиняє операцію;нова схема не створює додатковий retry. Unknown/truncated receipts не replay. Ідемпотентність SA124 зберігається;цей модуль не робить terminal recovery eligible повторно.

Реальна read-only перевірка: [старий checkpoint і model edits](../data/verification/g05-graph-repair-offline-2026-11-04.json) містять10 ownership witnesses;3 unrelated edit proposals відхилені. Сім старих edits перевірені по одному як scope probes,без застосування/нового запиту й без обходу max6. Це не доказ успішного нового model repair або незалежного приймання. G05 залишається development/unapproved/not executed.

Наступна прогалина:позитивна bounded model verification потребує коректного явного workflow після terminal requires_replan;повтор старого intent повертає той самий job,останній recovery не створив нового applied checkpoint. Не обходити це вручну або прихованим збільшенням cap.
