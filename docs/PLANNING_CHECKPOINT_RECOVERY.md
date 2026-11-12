# SA124 — явне відновлення перевірки незавершеного плану

04.11.2026. Endpoint POST /api/projects/{pid}/recover-planning приймає source_job_id і checkpoint_sha256. Це planning-only operation:не погоджує план і не запускає student code. Стандартний session/CSRF захист збережено.

## Походження та актуальність

Лише останній failed analyse/recover_plan job і його останній plan_patch_applied checkpoint. Project має error/paused,немає saved Plan/steps/approved_hash чи активних jobs цього project. Checkpoint bytes/hash,state,base/result hashes і повторне apply exact model patch перевіряються. Receipt належить project/revision,має done cloud job/complete response;selected patch і останній model outcome не unknown/truncated. Використання cached receipt доводиться automatic_assistance event у вікні failed job,а не лише датою створення receipt. Unknown/transient/truncated source failure не обходиться recovery.

Source coverage receipt має відповідати exact prefix SHA/length оплаченого prompt і поточним scoped indexed documents. Перевіряються actual document SHA проти DB hash,workspace input SHA,selection/chunks/OCR/status/paths. Current global report profile має відповідати snapshot checkpoint. Queue→execute context hash включає task/revision/user clarifications,source bytes/index,workspace inputs,public model/budget/pricing/profile settings і backend Python source hashes. Stale/foreign/approved/executed cases блокуються до model call.

Legacy pretty coverage receipts на Windows мають CRLF bytes,хоча адреса обчислена з LF text. Дозволено перевірити історичний logical LF address;нові receipts пишуться LF. Це стосується тільки formatting цих JSON receipts:student/source/archive bytes завжди перевіряються strictly SHA256,без нормалізації.

## Durable intent і caps

Нова SQLite planning_recoveries table створюється стандартною db.init migration. BEGIN IMMEDIATE атомарно створює intent/job і змінює status на analysis_running. UNIQUE(project,source_job,checkpoint) повертає existing job на повторне звернення,включно з failed/interrupted/cancelled;нової роботи не створює. До2 explicit recovery operations на project revision,failed operations ліміт не скидають. Один recovery має до2 patch attempts замість3 normal analyse;жодної initial PlanDraft generation. Звичайний bounded semantic review зберігається,а не вважається approval.

Guard перевіряється перед/після генерації та перед save_plan під SQLite write lock. Усі full validators/source/manual grounding/semantic review повторюються. Зміна джерел/revision/policy/code або pause не може записати новий Plan. Queue pause скасовує job. Після restart queued recovery зберігається;running стає interrupted/project paused,автоматично не requeue. Повторне звернення повертає той самий terminal job,не приховує нову спробу.

Це не необмежений retry після max3 попереднього analyse;кожна нова operation має явний authenticated request/durable identity. Немає фонового опитування або auto recovery loop. Budgeted cloud mode/positive limits≤$5/$0.03 потрібні для цього scoped API;local-only recovery не заявляється. Дуже великі source файли hash-яться streaming,можуть вимагати часу. Installed third-party library bytes не є частиною Python-source fingerprint;runtime environment pinning лишається окремим.

## Перевірки

687 full tests/Ruff passed,68 targeted. Controls:CSRF/idempotence/concurrent triggers,stale revision/chunks/physical bytes/policy/input/foreign receipt,unknown/truncated/recent checkpoint,approved/steps rejection,mid-response context change,queued pause,restart queued/running,cap persistence. Success fixture починає з PlanPatch (не PlanDraft) і завершує тільки awaiting_approval/pending attempts0. Controlled review mock не доводить реального semantic acceptance.

Реальний G05 checkpoint a49da4b65225002c309c51763093265013393b9719a869986e9970027f22d63c з failed job22c091f31b63407b995b688ed04c8245 локально перевірено;6-step schema-valid candidate з duplicate-producer error. Жодного ручного Plan/status/student code fix. Нова real recovery лише після source freeze/restart;її result зберігається окремо. Той самий G02 approval/CV8 hold і всі student-plan approval gates збережені.
