# SA120 / SA106 / SA121 — ремонт плану і факти декларацій

04.11.2026. Загальні механізми, без розгалужень за предметом, кейсом чи відомою відповіддю. Статус: implemented/unit and offline tested; нове model behavior ще не прийняте.

## SA106 — власник файла запуску

Для contract1 code-step не може споживати root entrypoint свого runtime як immutable input. Перевіряється до збереження/виконання плану. Усі10 профілів використовують ті самі RUNTIMES facts. Module під work/source/main.ts дозволений; report може читати source main.ts без code-driver conflict. Legacy contract0 не переписується. Input paths і Windows case-insensitive collisions враховані через спільний overlaps helper.

Frozen SA119 validator приймає старий6-step G05 draft; новий відхиляє step4/main.ts deterministic gate. Це proof дефекту/ремонту валідації, не виконання лабораторної. Shared prompt/schema/facts тепер однаково вимагають canonical FILE paths, не directories.

## SA120 — коротший ремонт і output headroom

Repair prompt замінює попередній набір boilerplate, а не додає весь draft до початкового prompt. Збережено task/variant/subject,останні user clarifications,report profiles,повні актуальні sources,platform facts,previous draft та exact error. Структурна/semantic перевірка після ремонту не пропускається. Невалідну/обрізану відповідь не дописують вручну.

Planner BudgetedModel має minimum4096 output tokens. Floor застосовується після safe cache lookup,до нового quote/send/reserve. Якщо budgeted output менший — explicit stop без платного запиту; чинні$0.03/$5 і max-output settings не змінюються. Complete cached result залишається придатним до локальної перевірки незалежно від нового floor. Unknown/truncated cache не replay. Для інших callers floor optional, попередня поведінка збережена.

4096 — консервативний мінімум для повного PlanDraft, не гарантія, що кожна відповідь поміститься. Дуже довгі джерела/плани можуть чесно зупинитися; patch-based repair не реалізований. Thinking review теж може вичерпати output; наявний bounded fallback не розширюється.

На збереженому G05 request: old68184 characters/output1784; replacement60686/output4976 при чинних settings/schema. Збережено всі inputs,нічого не відправляли. [Offline evidence](../data/verification/g05-plan-repair-facts-offline-2026-11-04.json).

## SA121 — факти для reviewer

plan_spec_facts обчислює для кожного кроку dependencies,entrypoint,consumed entrypoint flag,input→prior producer,declared input/output overlap. Таблиця й вимога назвати конкретне step/field/path стоять після повного плану. Installed packages,real file existence,OCR/subject correctness не заявляються. Imported namespace сам по собі не доводить доступність файла. Пакет,уже названий у dependencies,не слід вважати неоголошеним; output поточного кроку не є його input; копія в новому path не є перезаписом source.

Ця таблиця не є semantic acceptance і не примушує satisfied=true. Cloud reviewer все ще може помилятися; actual verdict потребує незалежного read-only review. Unit controls розрізняють genuine missing producer/driver/overlap і правильні counterexamples. Факти tool shortcut не забороняють коду викликати встановлений binary у власному контейнері.

## Перевірки

98 targeted tests passed перед останнім додаванням збереження subject/clarifications/report profiles; final full suite після всіх змін зберігається окремо. Окремі controls:10 runtime driver collisions/valid module paths,output-floor zero quote/no reserve,existing complete-cache reuse,invalid floor types,optional callers,repair evidence preservation,declared package/producer/copy counterexamples. Реальне планування робитиметься лише після source freeze; без придатного плану й user approval студентське виконання не починається.

Фінальна перевірка після всіх змін:618 full tests/Ruff F821/F811 passed. Frontend unchanged. До нового normal analyse source freeze151 files; нові model verdicts не включені в unit proof.
