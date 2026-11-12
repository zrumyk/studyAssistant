# SA-119 — повнота джерел планування: контракт до реалізації

04.11.2026. Статус: implemented/tested; bounded real planning ще перевіряється. Історичний діагноз нижче стосується SA117 frozen version.

## Перевірена проблема

[Аудит трьох уже оплачених prompt](../data/verification/g05-planning-source-coverage-2026-11-04.json): останній запит містить повні тексти7/13 indexed chunks primary task,0/2 testing notes,0/91 lecture. Source markers показують лише частини сторінок1–4 і8; одна сторінка може мати кілька chunks. task_passages повертає0 characters, бо англомовні headings не потрапляють у поточний переважно український відбір. Workspace має лише data ZIP, тоді як PDF імпортовані в бібліотеку та проіндексовані. Це різні види доступності. Containment не доводить увагу/розуміння моделі або повноту всієї сторінки.

## Потрібна поведінка

1. Спільний для author/reviewer реєстр scoped documents: ID/name/role/status; imported/indexed chunk counts; доступний для code точний workspace path або явне пояснення, що документ зберігається у бібліотеці, а не серед runtime inputs. Не повідомляти, що PDF не наданий, якщо обрізано лише context.
2. Coverage receipt кожної спроби: included chunk IDs/locators, omitted/unread частини, truncation/budget, OCR/visual uncertainty. Не використовувати відсутність у top-k як доказ відсутності вимоги. Scope/selection/approved pins зберігаються, чужі джерела не підмішуються.
3. Короткі primary task documents дочитуються повністю в межах явного сумарного budget; довгі джерела — bounded послідовні requirement passages/цільові theory reads зі збереженням coverage. Відбір не повинен залежати лише від українських заголовків або номерів конкретної роботи. Повторні запити не дублюють ті самі сторінки за відсутності нового факту.
4. До генерації остаточного плану невідомі необхідні частини дочитуються штатно або дія зупиняється з конкретною причиною: недоступні байти, unread OCR/formula, resource/token/request cap. Безумовне додавання91 сторінки в один prompt забороняє бюджетний критерій, а не вирішує проблему.
5. Погодження плану не обходиться. Не вписувати відповіді/код/expected values вручну; різницю доступності джерела й обмежень середовища пояснювати окремо. Старі paid receipts/unknown/truncated responses не повторювати заради демонстрації.

## Перевірки до платного повтору

- Короткі English та Ukrainian primary sources з вимогами на останній сторінці; таблиця й formula/OCR uncertainty.
- Довга theory, кілька task documents, обмежений budget, chunk більший за budget: жодного тихого пропуску з твердженням full-read.
- Imported/indexed PDF не є workspace data; opaque data ZIP не стає прочитаним текстом. Missing/failed/superseded/інший project/pinned selection мають достовірні стани.
- Однакові coverage facts author/reviewer, stable IDs/caching і stop/restart без повторення paid unknown requests.
- Лише після tests/freeze — bounded development planning на G05 з записаними пропусками/вартістю; придатний конкретний новий план передати на погодження користувачу. Не оголошувати це новим незалежним тестом: G05 вже exposed.

Наступна дія: реалізувати generic document availability/coverage receipt та language-neutral bounded requirement selection; повторно виміряти prompt coverage offline перед cloud call. SA-118 actual npm smoke — окреме незавершене свідчення.

## Реалізований контракт

`planning_sources.py` передає всі indexed task chunks без language/heading filter. Якщо вони не вміщуються у content allowance — analyse зберігає coverage і завершується до model call. Ліміт bundle40000 characters, резерв7000 під metadata, theory/reference до9000 і5500 на документ; final metadata overflow також зупиняє дію. До64 documents/10000 chunks на документ, перевищення явно блокується. Це обмежена реалізація: поетапне узагальнення довгої primary methodbook ще не додане; не оголошувати таку роботу виконаною.

Короткі notes включаються повністю, довга theory відбирається за явно названими сторінками та лексичною відповідністю, omitted counts/locator samples відкриті. Це не semantic completeness гарантія. Уточнення моделі дочитують task/theory до8k additional text у межах чинних3 attempts, не повторюють chunk IDs; scoped/pinned documents зберігаються. Однаковий початок двох chunks однієї сторінки не робить їх одним прочитаним фрагментом.

Receipt має index hash, actual included IDs, omitted count, warnings, source status і точний workspace path лише якщо файл справді доступний. Complete indexed text означає текст індексу, а не правильність OCR/формул/зображень. Author/reviewer отримують однаковий bundle. `planning-coverage/<content-hash>.json` незмінний; event посилається на receipt. Кожна спроба записує prompt SHA/length до виклику моделі, clarification updates покриття явно замінюють попередні counts. Це receipt підготовки запиту, не доказ доставки/розуміння; cloud receipt лишається окремим.

Offline production capture: G0513/13 primary task,2/2 testing notes,15/91 lecture chunks; actual prompt55767 characters, conservative request reserve$0.0299979/output6009tokens у межах$0.03.0 cloud calls, plan/status/steps/paid receipts незмінні. [Dry run](../data/verification/g05-source-coverage-dryrun-2026-11-04.json). Початкові old paid3 responses та independent failure не змінені.54 targeted tests passed; full final suite/result зберігаються окремо.


Перед real planning також виконано read-only перевірку всіх3 cached G05 drafts на новому валідаторі: вони все ще відхиляються через directory output `work/starter/`, а не тільки npm grammar. Програма тепер явно повідомляє author/reviewer canonical file-only paths, immutable consumed inputs та registry/bootstrap constraints; студентський план оператор не виправляє.

Оновлений release dry run після цих facts: prompt56484 characters, output budget5830, conservative reserve$0.0299982; без new calls/status/plan mutations. Дві потрібні лекційні сторінки14/34 є серед15 реально включених chunks. [Release capture](../data/verification/g05-source-coverage-release-dryrun-2026-11-04.json), [cached validation](../data/verification/g05-old-plan-local-validation-sa119-2026-11-04.json). Лексичний відбір theory не гарантує, що всі потрібні визначення знайдені; missing/clarification та review залишаються обов'язковими.
