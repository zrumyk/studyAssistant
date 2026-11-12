# SA-111 — Supporting source files

PythonProgram now accepts optional model-authored `supporting_files` alongside its main driver and checker. These files become Action.files and can satisfy declared source deliverables. Runtime data remain in expected_files. No student solution is inserted by the controller.

Canonical relative paths, case-insensitive uniqueness, reserved driver/checker names and input paths are validated. Existing consumed artifacts remain read-only through the prepared-action contract. Legacy responses without this field remain valid. The codegen contract version changed so an incompatible checkpoint is not reused.

Verification 03.11.2026: 480 full tests and Ruff F821/F811 passed; 55 targeted tests cover compatibility, nested source/SQL text, path rejection and upstream immutability. A real Docker developer fixture imported a supporting geometry module, checked its output and published source/data SHA256 receipts. This is mechanism verification, not laboratory acceptance.

Evidence: [tests](../data/verification/pytest-supporting-source-2026-11-03.txt), [runtime fixture](../data/verification/supporting-source-smoke-2026-11-03.json), [original G02 failure](../data/verification/g02-source-artifact-contract-failure-2026-11-03.json).
