# SA126 — явне продовження після terminal planning outcome

04.11.2026. Розширення recover-planning;це не загальне автоматичне перепланування і не новий cap. Повтор існуючого intent і надалі повертає старий job,навіть якщо він failed/cancelled/interrupted. Жодних прихованих requeue або background loops.

## Два різні випадки

1. Failed analyse/recover_plan має власний останній applied checkpoint:чинні SA124 provenance/source/receipt guards перевіряють його без змін.
2. Failed recover_plan не застосував жодного checkpoint і завершився exact `PlanReplanRequired`:дозволена лише окрема bounded переоцінка,якщо нова локально enforced graph policy ще не використовувалася в його останньому prompt. Справжню структурну перебудову поза PlanPatch ця операція не виконує.

Другий випадок успадковує checkpoint тільки через durable planning_recoveries intent тієї самої revision/project. Рекурсивно перевіряється root checkpoint із model-authored patch,complete receipt і source coverage. Ancestry обмежена чинним max2;не можна підставити довільну стару чернетку або вибрати інший SHA.

Останній automatic_assistance event має посилатися на однозначний done cloud job/complete response. Terminal typed PlanPatch мусить мати requires_replan=true,edits=[],base_sha256 точного успадкованого draft. Текст terminal error повинен точно відповідати explanation з цієї відповіді. Sending/uncertain/truncated/foreign/revision mismatch не відкривають продовження.

## Нова перевірна підстава

`graph_policy_marker` зв’язує поточні complete ownership witnesses і SHA реалізації artifact_repair. Він записується в graph repair prompt. Якщо complete locally enforced ownership scope немає або той самий marker уже був у terminal request,pереоцінка відхиляється. Лише зміна model/settings/source або пояснення моделі не є такою підставою. Hash позначає вміст реалізації/свідчень;сам по собі не доводить поліпшення алгоритму.

Це дозволяє перевірити нову загальну гіпотезу після виправлення контролера,зберігаючи старий результат незмінним. Source/revision/physical-byte/index/profile/permission checks SA124 залишаються чинними. Перед enqueue→execute→save повторно перевіряється fingerprint актуального context. Caps залишаються2 explicit operations/revision і2 patch attempts/operation,до6 edits;друга operation враховується,навіть якщо відхилена/скасована/перервана.

## Погодження та межі доказу

Endpoint і payload незмінні:POST recover-planning з source_job_id **останньої terminal recovery** і тим самим checkpoint_sha256. Це створює новий durable intent з новим source_job_id;повтор першого request продовжує повертати першу failed operation. Нова дія починається з PlanPatch,не PlanDraft. Усі graph/source/generated/manual/semantic validators повторюються;навіть позитивний результат дає лише awaiting_approval/pending steps/attempts0,не виконання.

Після вичерпання caps або повторного requires_replan автоматичного шляху немає. Не збільшувати cap,не скидати revision без справжньої зміни входу і не запускати той самий initial cached cycle задля обходу зупинки. Інші незалежні доступні задачі продовжуються окремо.

81 focused tests:позитивна fixture другої operation,відсутність initial generation/approval/execution,old/new idempotence,unknown/truncated/false/nonempty/forged terminal signals,same policy,foreign checkpoint/revision/root tamper,cap ancestry,permission change,pause/restart. Controlled fixture не є real model success. [Read-only actual eligibility probe](../data/verification/g05-terminal-reconsideration-probe-2026-11-04.json) зберігає G05 state без нових calls;actual run/result фіксуються окремо після freeze.
