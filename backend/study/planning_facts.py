"""Public planning facts; no credentials, environment or inferred package availability."""
import json
import hashlib

from . import config
from .execution_protocol import CHECKER_PROFILES, MAX_STDIN_BYTES
from .runtime import RUNTIMES


def planning_facts(pid=None):
    settings = config.settings()
    inputs = []
    inputs_truncated = False
    if pid:
        workspace = config.project_dir(pid) / 'workspace'
        root = workspace / 'inputs'
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                continue
            if len(inputs) >= 32:
                inputs_truncated = True
                break
            item = {'path': path.relative_to(workspace).as_posix(), 'bytes': path.stat().st_size}
            if item['bytes'] <= 1024 * 1024:
                item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            inputs.append(item)
    return json.dumps({
        'global_report_profile': {key: settings[key] for key in ('author', 'group', 'institution')},
        'runtime_profiles': {
            language: {
                'entrypoint': profile[1],
                'base_image': profile[0],
                'run_argv': profile[2],
                'automatic_dependencies': language in ('python', 'javascript', 'typescript', 'r'),
                'checker_language': 'python' if language in CHECKER_PROFILES else None,
                'checker_packages': 'producer profile' if language == 'python' else 'stdlib only',
                'same_container_test_argv': (['/tmp/program'] if language == 'c' else
                                             ['node', 'main.js'] if language == 'javascript' else None),
            } for language, profile in RUNTIMES.items()
        },
        'dependency_specs': {
            'python': 'Third-party registry name or name==version; no interpreter/stdlib modules.',
            'javascript': 'Registry name, @scope/name, or exact name@1.2.3; no ranges/tags/URLs/local paths.',
            'typescript': 'Same registry syntax as JavaScript. Bare typescript and @types/node are VALID; runtime replaces them with bootstrap pins typescript@5.7.3 and @types/node@22.10.2. A different explicit version requires another runtime profile, not silently replacing the bootstrap. Source package.json/lockfile declarations do not prove installed versions.',
        },
        'stdin': {'max_utf8_bytes': MAX_STDIN_BYTES, 'empty_string_means': 'EOF'},
        'external_files': 'Only imported project inputs or approved fetch before network-none code',
        'availability_limit': 'Capability does not prove a package/header/file is installed or imported',
        'workspace_inputs': inputs,
        'workspace_inputs_truncated': inputs_truncated,
        'execution_scope': {
            'code_step': 'Generate, compile/run and checks share one container; next code step uses a fresh container/image',
            'temporary_state': '/tmp and processes are not transferred to the next step; compiler availability is profile-specific',
            'persistent_state': 'Only explicit workspace output artifacts transfer; declare their names and provenance',
            'artifact_contract': {
                'version': 1,
                'code_write_fetch': 'Declare persistent artifact_outputs and earlier producer/imported artifact_inputs. Code publication is limited to declared outputs.',
                'report': 'artifact_outputs MUST be []; report is a controller-managed workflow, not a code container. Its renderer saves DOCX/PDF through the report artifact registry. Declare source tables/JSON in artifact_inputs; do not declare report.docx/report.pdf as outputs.',
                'manual': 'artifact_outputs MUST be []; external evidence uses the manual evidence workflow, not sandbox publication.',
                'paths': 'Canonical relative FILE paths only; do not declare directories or trailing slashes. Consumed artifact_inputs are immutable: a later producer writes a NEW output path rather than overwriting its input. A code step owns its runtime entrypoint and cannot consume that same root path as an input; transfer modules under separate paths. Declare each persistent file explicitly, including configs and source deliverables.',
            },
            'capture_scope': '.study_main.stdout/.stderr belong to the current run only; publish transcripts as output JSON/CSV for later steps',
            'task_documents': 'Task/RAG text is not automatically a readable file in the container. Listed input paths are available; when workspace_inputs_truncated is true, absence from this sample does not prove a file is missing',
            'bounded_program_tests': 'A checker may test the current generated program with finite inputs and child timeouts in the same sandbox; never repeat training or an expensive experiment',
        },
    }, ensure_ascii=False)
