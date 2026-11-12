# Спільний протокол виконання — SA-104

Стан02.11.2026: механізм реалізований і перевірений технічними сценаріями.
Це не незалежне приймання лабораторних чи доказ усієї універсальності.

## Контракт

`Action.stdin_text` — рядок до65536 **байтів UTF-8**, default `""` (негайний EOF).
PythonProgram та PythonBundle/CompactPythonBundle зберігають його в Action;
модульний cache відновлює його без додаткового виклику моделі. Старі JSON без
поля завантажуються з default. Nullable не додається до wire-схеми: чинний
конвертер strict JSON-schema відхиляє null. Схеми перевірені локально, платного
API-виклику для перевірки схеми не було. До майбутніх paid calls перевіряти cache
та quote: нові поля/промпти змінюють контракт запиту, старі receipts не стираються.

Runtime приймає optional `stdin_text` (внутрішній None також EOF), перевіряє ліміт
до Docker, записує bytes через write_bytes у `runs/<run_id>.stdin` поза writable
mount програми. Непорожній файл монтується read-only як `/study-stdin` та подається
через `< /study-stdin`; порожній — `< /dev/null`. Popen має stdin=DEVNULL.
Немає shell-інтерполяції вводу, stdin PIPE чи TTY. Клавіатура користувача не читається.

## Профілі перевірок

| Producer | Окремі checks | Середовище checker |
|---|---|---|
| Python | Python .py зі змістовними assert | Поточний Python-профіль |
| C | Python .py зі змістовними assert | Стандартна бібліотека Python3, opt-in image |
| JavaScript | Python .py зі змістовними assert | Стандартна бібліотека Python3, opt-in image |
| Інші профілі | Явне відхилення непорожнього check_paths до Docker | Не реалізовано |

Це кілька мов producer зі спільною мовою перевірки, а не виконання native C/JS
checker-файлів. check.c/check.js відхиляються. Нові C/JS code-actions потребують
checks так само, як Python. Функції перевірки мають справді викликатися;
checker_runner відхиляє відсутні/невикликані assert та `assert True`.

Opt-in образи C/JS додають python3 з Debian через штатний disk-guarded build.
Маркер `stdlib-python-checker-v1` входить у ключ образу; звичайні профілі без
перевірок і старі образи не змінюються. Автоматично всі10 мов не збираються.
Python-пакети з Python producer не стають доступними в checker іншої мови.

## Дані й життєвий цикл

З checks stdout/stderr основної програми зберігаються у snapshot як
`.study_main.stdout`/`.study_main.stderr`. Checker читає їх або звичайні результати,
не повторюючи експеримент. Після завершення main вивід додається до загального
журналу. run_progress читає хвіст поточного main stdout; для probe до створення
цього файлу — звичайний журнал. Resource probe окремо отримує EOF, його stdout
не змішується з captured main stdout. Probe-failure запобігає основному запуску.

Main, компіляція, probe і checkers мають один runtime timeout/cancel/output/disk
контролер. Тривалість image prepare, як і раніше, не входить у runtime timeout;
її контролює окремий build guard. Резерв10GiB, stop build13/runtime11GiB, мережа
none, non-root, read-only rootfs, quotas та правила погодження не послаблені.
Це polling-контроль, не абсолютна дискова квота проти сторонніх програм.

`checks_requested` — список запитаних файлів. `checker_results` містить completed
та exit_code кожного checker; `checks` — лише успішно завершені перевірки в
послідовності. Після main-failure checkers пропускаються; після checker-failure
наступні не запускаються. Наявність старого службового status-файлу не є доказом:
службові `.study_*` не копіюються з workspace в новий run і назад як результати.
У snapshot вони зберігаються для діагностики. Статус незавершеної через kill
перевірки не перетворюється на успіх.

`execution_protocol` записує version, stdin bytes/SHA256/artifact, captured paths,
main exit code та мову/профіль checker. Середовище зберігає raw host stdout/stderr
та result.json. Відновлення успішного запуску звіряє stdin identity і raw bytes
артефакту, виконані checks, фактичні джерела й вихідні файли. Старий результат
без input metadata допустимий лише для EOF; змінений stdin вимагає нового запуску.
Звіт використовує основний файл актуальної мови й input identity; plaintext stdin
не додається до report prompt автоматично. Довгі непайтонівські файли можуть бути
пропущені через чинний бюджет контексту; це не твердження про повний аналіз коду.

## Докази й межі

Відтворення технічних C/JS fixtures (не студентських рішень):

```powershell
.venv/Scripts/python.exe scripts/verify-execution-protocol.py --output data/verification/NEW-UNIQUE-EVIDENCE.json
```

Скрипт відмовляється перезаписувати доказ чи починати з active jobs; перед двома
image builds вимагає консервативні18GiB вільного місця. Кожен результат зберігається
до переходу далі; перший failure зупиняє harness. Лабораторних/LLM-запитів немає.

Пройшли10 реальних сценаріїв C/JS: finite stdin, EOF обох профілів, Unicode та
буквальний shell-подібний ввід, wrong-result, невикликаний checker, main-failure,
checker short-circuit, timeout, cancel. Окремо Python-проба на наявному training
image перевірила stdin/capture/checker та EOF для resource probe. Це не перевірка
чистої збірки Python image. Збережені images IDs, source/raw-log/input hashes,
перевірка незмінності джерел, вільне місце й відсутність залишених контейнерів:
`data/verification/execution-protocol-evidence-2026-11-02.json` і пов’язані smoke/probe JSON.

Виконаний assert і exit0 не доводять правильності oracle, повноти перевірок або
незалежності від generator. Checker і producer працюють в одному sandbox і мають
спільний writable workdir; це не ізоляція checker від навмисного підроблення
producer. Raw logs і службові status-files потребують незалежної змістової
перевірки; цей протокол не сертифікує ворожий код. Критичне приймання SA-060 окреме.

Один Action задає один finite input. Для різних входів потрібні різні snapshot/
run identities і загальний облік бюджету; автоматичний corpus-runner лабораторних
ще не реалізований. Довільні argv/build-команди та native checker-мови не додані.
Entrypoints лишаються фіксованими, сторонні C-залежності лишаються явною межею.
CS50 library, primary filename, freeze і погоджений план G01 ще не підтверджені.


## Продовження аудиту й планування

Protocol2 додає bytes/SHA256 captured streams і fail-closed recovery при зміні чи
відсутності цих доказів. Status parsing bounded4 bytes/regular files; див.
EXECUTION_PROTOCOL_AUDIT_2026-11-02.md. 401 final unit/integration tests passed.

Тести власної дешевої детерміністичної програми допустимі в checker того самого
sandbox з finite input/child timeout; C — /tmp/program, JS — node main.js.
Навчання/дорогий експеримент не дублюється. Це не новий generic corpus-controller.
Source /tmp, compiler, процеси й private captured stdout не успадковуються іншим
code-кроком. Для наступних етапів потрібні явні output transcripts/artifacts.
Реальні3 scope probes: data/verification/execution-scope-probes-2026-11-02.json.

planning_facts ділить факти між author/reviewer: globals/capabilities/input paths/
execution scope. Input manifest — максимум32 файли з truncation flag; відсутність
у неповній вибірці не доводить, що файл відсутній. Модельний план G01 r2 все одно
провалив незалежний dataflow-аудит, тому ще не є дозволом на виконання.
