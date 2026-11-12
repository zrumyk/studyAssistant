# SA-106 — контракти persistent файлів, v1

Новий аналіз повертає `artifact_contract_version=1`. Крок має `artifact_inputs`
та `artifact_outputs`: точні relative file/directory paths або порожні списки.
Механізм не залежить від назви дисципліни, роботи, dataset чи очікуваного числа.

## Що перевіряється

- Кожний input походить від ранішого declared producer або від існуючого `inputs/`.
  Майбутній producer, невідоме ім'я, абсолютний/host шлях, `..`, private `.study_*`
  й root `result.json` відхиляються. Останній є службовими runtime metadata;
  користувацький `data/result.json` дозволений.
- Шляхи відповідають Linux case; output ownership захищений від Windows aliases.
  Різні producers не можуть перекривати namespace чи перезаписати consumed input.
- Code/write/fetch мають явні outputs. Fetch — один точний файл під
  `inputs/downloads/`. Report/manual продовжують штатні evidence-механізми й
  не оголошують file outputs, яких контролер не може підтвердити.
- API save/approve перевіряє v1 graph; генерація нового плану не може використати
  legacy version0. Semantic reviewer не замінює цей deterministic gate.
- До запуску consumer controller перевіряє producer done, receipt та SHA256/bytes
  кожного переданого файлу. Змінений/відсутній input блокує крок до codegen.

## Action і runtime

У v1 `expected_files` має належати declared outputs. Data outputs потребують
expected_files; source deliverable може бути безпосередньо в model-authored files
(не підмінює результат обчислення). Action не може переписати declared input.
Ця перевірка є й у prepare/staged action, і перед звичайним виконанням.

Новий run snapshot отримує model source/checker та declared persistent inputs;
неоголошений workspace-файл не переноситься. Declared передані файли монтуються
read-only з цього snapshot. Namespace початкових project `inputs/` як і раніше
монтується весь read-only: це project/source permission boundary, не нова ізоляція
кожного imported file. Контракт не є sandbox проти навмисного шкідливого коду.

Назад у workspace публікуються лише declared outputs. Інші produced files
залишаються у snapshot як діагностика. Старий namespace поточного кроку
переноситься в project history, перед rename обидві абсолютні межі перевіряються;
bytes не видаляються. Поточні validated model sources відновлюються з Action.
Це відбувається тільки перед справжнім новим запуском, не при reuse успішного.

Після runtime outputs звіряються з actual run snapshot і записуються в receipt:
`artifact_contract={version:1,outputs:{path:{sha256,bytes}}}`. Write/fetch receipts
підтверджують створення/отримання bytes, а не правильність навчального результату.
Directory contract потребує непорожнього набору; поточний ліміт10000 files — для
більшого набору слід оголосити архів або поділити крок.

Якщо runtime завершився успішно, але receipt не підтверджений, result/run
зберігаються, step переходить у waiting_user. Автоматичного повтору обчислення
немає. Reassess спочатку звіряє contract зі збереженим snapshot без виконання
програми; до цього не викликає оплачений reviewer.

UI нового плану показує два списки шляхів для перевірки та редагування. JSONL
transcripts доступні report evidence/assessment так само, як JSON/CSV/text.

## Сумісність і межі

Stored plans без version є legacy0: старі approved hashes і працюючі процеси
не переписуються. Порожні нові Step-поля не змінюють legacy plan_hash. Ручне
редагування історичного legacy-плану сумісне, але не означає, що v1 guarantees
автоматично застосувалися. Непорожні declarations у version0 відхиляються.

Контракт перевіряє заявлені залежності, фізичну передачу та незмінність bytes.
Він не доводить, що модель задекларувала всі залежності в довільному тексті,
що переданий binary сумісний з наступним image, або що формули/алгоритм правильні.
Readonly initial inputs перевіряються окремими source/permission механізмами;
ця зміна не сертифікує всю ізоляцію SA-060. Звіт/manual мають власні outputs/evidence.
Критерії лабораторної та independent oracle залишаються окремими воротами.

## Відтворення

```powershell
.venv/Scripts/python.exe -m pytest tests/test_artifact_contracts.py -q
.venv/Scripts/python.exe scripts/verify-artifact-contracts.py --output data/verification/NEW-EVIDENCE.json
```

Harness відмовляється перезаписувати старий evidence чи починати за active jobs.
Реальна проба C→JavaScript: producer output → receipt → readonly consumer,
підміна bytes відхиляється до consumer; unrelated workspace/source/undeclared
outputs не передаються. Це developer fixtures, не лабораторні рішення.
Початкова проба03.11 зупинилась до коду через вимкнений Docker; окремий r2 evidence
збережений після штатного запуску Docker. Платних викликів harness не робить.

## Спільні факти планувальника та reviewer

`execution_scope.artifact_contract` явно розділяє sandbox code/write/fetch
і controller-managed report/manual. Для report/manual `artifact_outputs=[]`:
DOCX/PDF зберігає штатний реєстр звітів. Порожні outputs не означають втрату
звіту; таблиці/JSON, потрібні звіту, оголошуються як inputs. Цей однаковий
контекст перевірено regression-тестом, але після уточнення03.11 нового платного
планування ще не було. Результат G01 r5 залишається failed, а не виправленим.
