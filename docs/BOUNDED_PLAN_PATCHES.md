# SA120 — обмежені зміни чернетки плану

04.11.2026. Generic workflow для unapproved plans;без умов за предметом або кейсом. Старий full-plan repair module лишається для історичних контролів,але analyse після complete draft використовує PlanPatch.

## Гарантії та межі

Model-authored PlanPatch містить base_sha256,requires_replan,пояснення і до6 edits. Replace дозволений лише для визначених top-level PlanDraft полів або одного поля Step за canonical zero-based path. Sources/conflict_evidence/contract-version/whole steps replacement заборонені. Before/after — цілі JSON values;поточний typed base hash і before_json мають точно збігтися,включно з різницеюbool/number. Nonfinite values,stale/malformed/duplicate/noop paths відхиляються. До6000 chars на кожний before/after,до14000 UTF8 bytes сумарно.

Зміни застосовуються до незалежної копії й повертаються лише після PlanDraft schema validation. Оригінал не мутується навіть якщо остання з кількох правок невалідна. Неадресовані поля канонічно незмінні;зміст зміненого поля все ще може бути хибним і потребує перевірки. Одну bounded insert_step дозволено для нового report/fetch тощо;індекс/before Step перевіряються,новий Step типізований. Delete/reorder/масштабна перебудова не підтримуються:requires_replan явно зупиняє дію,не обходить перевірки.

## Контролер

Analyse має той самий максимум3 attempts,не додає прихованих repair loops. Initial schema generation — PlanDraft;після complete candidate будь-який подальший ремонт/clarification використовує PlanPatch з fresh source facts,actual error,previous full draft,user clarifications і report profiles. Некоректна первинна schema без typed candidate може потребувати початкового generation знову в тому самому cap. Patch model має окремий purpose/cache й minimum2048 output tokens,initial full draft minimum4096. Ліміти$0.03/request,$5/month не змінені. Низький headroom блокує quote;truncated/unknown не replay.

Після patch повторюються generated-plan/dependency/artifact/source/manual-grounding validators та semantic review. Approval не надається;save_plan тільки після всіх checks. Planning-coverage receipt зберігає exact phase/prompt SHA/length. Content-addressed planning-patches checkpoint містить base,patch,result,хеші й request ID зі станом schema_valid_only/unapproved;це evidence candidate change,не приймання плану. Requires_replan та truncated patch зупиняють цикл,не створюють нову спробу автоматично.

## Перевірки

72 targeted tests passed. Нормативні controls:поля поза patch збережені,stale SHA/before rejected,forbidden paths/provenance blocked,atomic invalid later edit,type distinctions,no-op/size cap,report insertion,idempotent unapproved checkpoint,truncated/replan не retry. Existing workflow tests оновлені на typed patch replies замість full draft mock. Контрольовані semantic mocks доводять flow/gates,не якість реального model verdict.

Offline current request на complete G05 draft і справжньому review з fresh source configs:64571 characters/output allowance4224 при floor2048. Жодних ручних student-plan edits,запису Plan або paid calls. [Offline budget/context](../data/verification/g05-plan-patch-request-offline-2026-11-04.json). Це не доказ майбутнього full patch completion або semantic correctness. Final full suite/freeze/real outcome зберігаються окремо.

Цей repair не пише student code і не підміняє результати. Якщо потрібний структурний replan або багато changed fields не вміщуються,зупинка є чесною межею;не вирізати обов'язкові вимоги заради short output. Підтримка довільних JSON Patch operations або plain-text patch не заявляється.


Фінальні уточнення:при невалідній правці зберігається до3000 chars початкового review плюс1200 chars patch error;patch prompt вміщує до4500 chars error context,щоб не загубити tail вимог. Clarification просить змінити лише missing/пов’язані кроки,без суперечливої команди переписати весь план.78 targeted/662 current full tests/Ruff passed після всіх змін. Старий conflict mock адаптовано до2-field patch,інші660 controls уже проходили до цього test-fixture update. [Final suite](../data/verification/pytest-bounded-plan-patches-current-2026-11-04.txt),[current offline prompt](../data/verification/g05-plan-patch-current-offline-2026-11-04.json). Actual model patch ще не прийнятий на момент freeze.
