# SA122 — межі слів у перевірці передачі змінних

04.11.2026. Загальна лексична перевірка,без прив'язки до предмета або конкретної роботи.

Frozen SA120121r1 двічі відхилив7-step draft на expected «незмінні конфіги»: substring «змінн» помилково означав process variables. Повідомлення про непередавані змінні не відповідало declared persistent source/config outputs. Третій draft додав file paths у package dependencies; це окрема справжня помилка,не підстава послаблювати package grammar.

Нова перевірка потребує меж слова для українського «змінн…» або English variable/variables. «Незмінні» й variableset не є process variables. Real process variables без вказаних persistent outputs у текстовому criterion лишаються відхиленими. Це обмежена евристика expected prose,не повний semantic аналіз або сертифікація dataflow. Artifact contract перевіряється окремо.

Після виправлення перші2 cached drafts проходять generated-plan/artifact validators,третій усе ще відхиляється як invalid package. У production Plan не записувався,steps/status/approval не змінювалися вручну. Немає student execution або semantic reviewer acceptance.

Current normal author prompt збігається з повною cached response00fb7fc8f02a4543a4db62f9e46554d6. Offline probe використав штатний BudgetedModel parser при заблокованому cloud.quote: cache reused,7 steps,missing[],0 new calls; усі10 paid receipts і project state unchanged. Наступне продовження повинне використовувати цей cache й пройти звичайний semantic review,не повторювати оплачений generation. Якщо prompt/settings/source version зміняться,cache match треба перевірити знову.

Докази: [frozen failed operation](../data/verification/g05-sa120121-planning-result-2026-11-04.json), [current local validation](../data/verification/g05-plan-word-validation-2026-11-04.json), [cache probe](../data/verification/g05-complete-plan-cache-probe-2026-11-04.json).102 targeted tests passed,final full suite після word-boundary fix зберігається окремо. Ліміти$5/$0.03/10GiB незмінні,нових paid attempts після цього fix у цьому етапі не буде.

Фінальна перевірка:622 full tests/Ruff F821/F811 passed. Frontend unchanged. Після freeze/restart не буде нового paid operation цього етапу.
