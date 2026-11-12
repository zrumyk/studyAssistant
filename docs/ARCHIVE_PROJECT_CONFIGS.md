# SA123 — декларативні конфігурації архіву до планування

04.11.2026. Generic metadata reader для ZIP inputs,без subject/case ID branching. Не генерує студентське рішення й не змінює вхідний ZIP.

## Реалізація

До imported_data_metadata додано bounded читання package.json,package-lock.json,tsconfig.json через archive.open,без extraction/exec/npm install. Зберігаються source member paths,bytes,SHA256 і тільки вибрані декларації: dependency maps/scripts/engines,root package lock metadata,compiler options/include/extends. Кожний status явно read/unread/budget/unsafe/ambiguous. Ці декларації не доводять package installation,code correctness або виконання script. JSONC/TOML/інші конфігурації поки не підтримуються; malformed JSON залишається unread.

До10000 ZIP entries scanned,до6 config candidates,до256KiB/member,512KiB declared aggregate,ratio200,до5000 characters selected fields. Traversal/absolute/drive paths,symlinks/encrypted/duplicate paths відхиляються; normalization raw name не повинна перетворити unsafe member на trusted config. Під час unit test Windows ZipInfo нормалізував backslash уже при створенні архіву; тест виправлений на raw ZIP filename bytes. JSON parse/CRC errors зберігаються як unread,не приховуються як complete. Загальний metadata prompt budget9000 тепер повертає valid JSON з явними omissions замість зрізу рядка.

## Bootstrap facts

runtime.effective_dependencies — спільна pure функція для Docker image recipe і reviewer facts. Bare typescript/@types/node та ті самі точні pins нормалізуються до typescript5.7.3/@types-node22.10.2,не є запитом іншої версії. Справді конфліктна explicit version усе ще відхиляється. Installed versions відрізняються від install requests; жодного нового image build/smoke цей етап не робив. Invalid declarations у standalone review facts дають validation_error,не падають і не перетворюються на valid profile.

## Фактичний G05 результат

Штатний cached-author analyse використав complete draft без нової оплати generation. Reviewer правильно знайшов відсутні test packages; repair запропонував непідтверджений ESLint замість фактичного comfy-tslint. Наступний review хибно назвав bare typescript конфліктом із bootstrap. Останній repair truncated,не повторений. Plan не прийнято/не збережено,steps0,approval absent,student execution0.6 new calls$0.01663923,ledger$0.58063026,old10 receipts unchanged. [Raw outcome](../data/verification/g05-sa122-cache-review-result-2026-11-04.json).

Новий reader read3 оригінальні configs:package scripts test=ts-mocha і lint=comfy-tslint --no-mutation;dependencies з source,без ручного переписування плану. Metadata1449→3520 characters,current initial prompt58991/output5167 при$0.03 cap.0 new model calls,project/receipts unchanged. Новий prompt SHA відрізняється від old author cache через нові факти,тому старе твердження «initial cache match» після цього version change більше не чинне. [Offline real metadata/budget](../data/verification/g05-project-config-facts-2026-11-04.json).

## Межі й наступна дія

639 full tests/Ruff passed.91 targeted перед останнім додатковим facts-error control,усе охоплене final full suite. Немає нового accepted semantic verdict або actual npm environment. Після source freeze перед будь-яким платним плануванням зіставити нові facts з primary brief,request headroom і paid-cache provenance. Не повторювати truncated receipt,не редагувати згенероване рішення/план/SQL statuses вручну. Не вважати partial metadata читанням усього архіву; ZIP document needs_review не очищати вручну. SA120 partial/bounded repair і незалежні нові inputs лишаються окремими задачами.
