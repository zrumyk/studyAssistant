# SA-118 — залежності JavaScript / TypeScript

04.11.2026. Спільна перевірка застосовується до плану й Docker recipe, без умов за ID роботи.

Підтримуються registry names `package`, `@scope/package` і точні версії `package@1.2.3`, включно з prerelease/build suffix. Імена lowercase; довжина до214 символів. Версійні діапазони, tags, aliases, URL, local/git paths, shell options/metacharacters не підтримуються. Python зберігає `name==version`.

TypeScript bootstrap залишається TypeScript5.7.3 / @types/node22.10.2. Явний запит іншої версії зупиняється до Docker з поясненням необхідності окремого профілю; однакові pinned/unpinned bootstrap entries не дублюють install. Це не відтворення довільного package-lock.json. Правильна назва не доводить наявності пакета або сумісності курсів.

Тести охоплюють дві мови, scope/version, небезпечні та непідтримувані specs, ізоляцію Python, конфлікт bootstrap і побудову recipe без Docker. Реальної інсталяції нових npm-пакетів цей етап не виконував.

Первинний опис npm: https://docs.npmjs.com/cli/v11/using-npm/package-spec/ . Платформа навмисно підтримує лише описану підмножину npm specs.

Доказ: [frozen regression](../data/verification/sa118-frozen-regression-2026-11-04.json), [tests](../data/verification/pytest-registry-dependencies-2026-11-04.txt). SA-118 на перевірці до реального bounded install/resolve smoke у зафіксованому середовищі; G05 не прийнятий.
