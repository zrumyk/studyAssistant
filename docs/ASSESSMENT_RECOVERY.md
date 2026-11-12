# SA-110 — explicit service recovery of saved assessments

Existing `/reassess` remains one bounded semantic recheck. A completed negative or
needs_user verdict cannot be bypassed. A failed5xx cloud service call with no
usable verdict may instead enable `POST /api/projects/{pid}/steps/{sid}/recover-assessment`.
This is one explicit new appraisal, not a resend of an uncertain receipt and not
program execution. The UI offers the action only when server eligibility allows it.

## Boundaries

- At most2 new explicit recovery operations per saved run;60s cooldown from prior
  failed service request. No automatic loop, provider ping or background task.
- Must have current approved plan, successful code snapshot/checks, waiting_user,
  review_only_attempts1, and proven failed5xx cloud job/uncertain receipt.
- New controllers persist request/run identity. Legacy records require matching
  project/step/reassess-job window AND result_reassessed run event; error text alone
  cannot unlock recovery. Only known5xx service errors are covered, not arbitrary
  network errors, malformed verdicts or uncertain process crashes.
- Inputs/outputs and saved checker/result bytes revalidated queue→worker. No code,
  dataset, step attempts, old review_only_attempts or old receipt statuses rewritten.
- Current cloud consent/normal$5/$0.03 budget gates still apply. Recovery uses the
  cloud reviewer explicitly, without silent local judge fallback.
- A distinct recovery ID is persisted before dispatch and is included in cache
  purpose. Duplicate POST/job does not create a second appraisal. Old uncertain
  requests/reserves remain intact; new operations have their own receipts/history.
- Completed verdict, unconfirmed crash, failed snapshot/approval check or unsupported
  error closes this path. Pause never resumes computation; queued recovery survives
  worker restart, interrupted unknown operations are not replayed.

History lives in result.assessment_recoveries (requested/completed/transport_failed/
error). Successful recovery marks the saved result done and enqueues ordinary
continuation if the project is still running under the same approval. The same
program run_id is retained. Rejected verdicts preserve waiting_user and stop.

## Verification

469 full-suite tests/Ruff F821/F811 and frontend build passed.19 targeted include
Python/C/JavaScript, legacy proof, semantic rejection/needs_user, multiple503/cap,
duplicate POST, tampered artifact/checker/approval, cooldown/consent/pause and queued
worker restart. Tests use isolated fake services; they do not prove live provider
availability. Real G02 evidence is recorded separately.

This changes the platform version. Old G02 independent failure remains immutable;
subsequent G02 continuation is development under the new source freeze. G03 source
was already exposed before this version; do not count it as a new post-new-freeze
holdout. Plan approval for unchanged G02 scope persists; no Gradescope submission
is authorized. Successful computational results must never be replayed merely to
retry model assessment. New freeze/evidence must identify actual source/image/input.
