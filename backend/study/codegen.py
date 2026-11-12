"""Bounded Python generation for models that cannot return a whole bundle at once."""

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from pydantic import Field, ValidationError
from .schemas import StrictModel, Action, FileWrite, ActionCheckpoint
from .providers import OllamaProvider
from . import config, db

CODEGEN_CONTRACT='inspected-inputs-supporting-source-v2'


class ModuleSpec(StrictModel):
    path: str = Field(pattern=r'^[^\\\r\n]+\.py$')
    contract: str = Field(min_length=1, max_length=4000)


class PythonBundle(StrictModel):
    files: list[ModuleSpec] = Field(min_length=1, max_length=12)
    checks: list[ModuleSpec] = Field(min_length=1, max_length=4)
    expected_files: list[str] = Field(max_length=100)
    explanation: str = Field(max_length=1500)
    stdin_text: str = Field(default='', max_length=65536,
                           description='Finite UTF-8 stdin; empty means EOF, at most65536 bytes')


class CompactModuleSpec(ModuleSpec):
    contract: str = Field(min_length=1, max_length=240)


class CompactPythonBundle(PythonBundle):
    files: list[CompactModuleSpec] = Field(min_length=1, max_length=12)
    checks: list[CompactModuleSpec] = Field(min_length=1, max_length=4)
    expected_files: list[str] = Field(max_length=30)
    explanation: str = Field(max_length=500)


class ModuleSource(StrictModel):
    content: str = Field(min_length=1, max_length=40000)


def normalize_checks(action):
    """Resolve checker references to supplied files without inventing source."""
    for check in action.checks:
        same = next((f for f in action.files if f.path == check.path), None)
        if same is not None and check.path != action.entrypoint:
            if check.content.strip() and check.content != same.content:
                raise ValueError("Суперечливі версії перевірки: " + check.path)
            check.content = same.content
            action.files.remove(same)
    if not action.checks:
        action.checks = [
            f
            for f in action.files
            if Path(f.path).name.startswith("check_")
            and f.path.endswith(".py")
            and f.path != action.entrypoint
        ]
        action.files = [f for f in action.files if f not in action.checks]
    return action


def checkpoint_write(path, model):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(model.model_dump_json(), "utf-8")
    temporary.replace(path)


def save_candidate(pid, sid, revision, model, action):
    path = config.project_dir(pid) / "codegen" / sid / "last-action.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_write(
        path, ActionCheckpoint(model=model, revision=revision, action=action,generation_contract=CODEGEN_CONTRACT)
    )


def repair_action(pid,sid,revision,workspace,repair,regenerate=None):
    """Apply model-authored exact replacements only to this step's own source.

    No files are written here; normal static validation and sandbox execution
    still follow. A mismatched or ambiguous edit is rejected, never guessed.
    """
    checkpoint=config.project_dir(pid)/'codegen'/sid/'last-action.json'
    saved=ActionCheckpoint.model_validate_json(checkpoint.read_text('utf-8'))
    if saved.revision!=revision:
        raise ValueError('Точковий ремонт потребує версії саме поточного кроку')
    action=saved.action.model_copy(deep=True)
    files={Path(file.path.replace('\\','/')).as_posix():file for file in action.files+action.checks}
    for path,file in files.items():
        if not saved.written_to_workspace:
            continue # Repair exact bytes of the staged candidate, not old workspace.
        actual=config.safe_path(workspace,path)
        if not actual.is_file() or actual.stat().st_size>100000:
            raise ValueError('Вихідний модуль ремонту недоступний: '+path)
        file.content=actual.read_text('utf-8')
    grouped={}
    for edit in repair.edits:
        path=Path(edit.path.replace('\\','/')).as_posix()
        if path not in files or Path(path).suffix.lower()!='.py':
            raise ValueError('Ремонт може змінювати лише власні Python-модулі поточного кроку: '+path)
        grouped.setdefault(path,[]).append(edit)
    for path,edits in grouped.items():
        file=files[path]
        original=file.content;updated=original
        for edit in edits:
            if not edit.before or updated.count(edit.before)!=1:
                if regenerate is None:
                    raise ValueError('before має дослівно й однозначно відповідати одному фрагменту '+path+'; файл не змінено')
                # Never guess a fuzzy match. Ask for this whole module from its
                # actual original bytes; no partially applied changes are used.
                updated=regenerate(path,original,edits)
                validate_source(path,updated)
                break
            updated=updated.replace(edit.before,edit.after,1)
        file.content=updated
    if repair.replace_expected_files:
        action.expected_files=repair.expected_files
    action.explanation=repair.explanation
    return action


def validate_source(path, content):
    tree = ast.parse(content, filename=path)
    statements=[node for node in tree.body if not (isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str))]
    if len(statements)==1 and isinstance(statements[0],ast.Expr) and isinstance(statements[0].value,(ast.Dict,ast.List)):
        raise ValueError(f'{path}: отримано JSON/маніфест замість коду Python. content має містити сам код цього модуля, без вкладеного Action або списку файлів.')
    if any(
        isinstance(n, ast.ImportFrom) and n.module == Path(path).stem
        for n in ast.walk(tree)
    ):
        raise ValueError(
            f"Модуль {path} імпортує сам себе; реалізуй власні функції без самоімпорту"
        )
    lint = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "F821",
            "--output-format",
            "json",
            "--stdin-filename",
            path,
            "-",
        ],
        input=content,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
    )
    if lint.returncode == 2:
        raise RuntimeError("Статична перевірка Python недоступна: " + lint.stderr[:500])
    if lint.returncode:
        diagnostics = json.loads(lint.stdout)
        raise ValueError(
            f"Статичні помилки {path}: "
            + "; ".join(
                f"рядок {item['location']['row']}: {item['message']}"
                for item in diagnostics[:8]
            )
        )
    return tree


def signatures(path, content):
    tree = ast.parse(content)
    parts = [path + ":"]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(f"def {node.name}({ast.unparse(node.args)})")
        elif isinstance(node, ast.ClassDef):
            parts.append("class " + node.name)
            for method in node.body:
                if isinstance(method, ast.FunctionDef):
                    parts.append(f"  def {method.name}({ast.unparse(method.args)})")
        elif isinstance(node, ast.Assign):
            parts.extend(ast.unparse(target) for target in node.targets)
    return "\n".join(parts)[:1800]


def generate_bundle(pid, sid, prompt, cancelled, assisted=False,thinking_override=None):
    root = config.project_dir(pid) / "codegen" / sid
    model_name = config.settings().get("coding_model") or config.settings()["model"]
    if assisted:
        model_name = (
            "cloud/"
            + config.settings()["cloud_provider"]
            + "/"
            + config.settings()["cloud_model"]
        )
    mode_key=model_name if thinking_override is None else model_name+'\nthinking='+str(thinking_override)
    key = hashlib.sha256(('compact-manifest-v2\n' + mode_key + "\n" + prompt).encode()).hexdigest()[:20]
    cache = config.safe_path(root, key)
    cache.mkdir(parents=True, exist_ok=True)
    model = OllamaProvider(model_override=config.settings().get("coding_model") or None)
    if assisted:
        from .assistance import BudgetedModel

        model = BudgetedModel(
            pid,
            model,
            "Модульна генерація коду",
            prefer_cloud=True,
            cancelled=cancelled,
            thinking_override=thinking_override,
        )
    manifest_path = cache / "manifest.json"
    if manifest_path.exists():
        bundle = PythonBundle.model_validate_json(manifest_path.read_text("utf-8"))
    else:
        try:
            bundle = model.generate(
                prompt
                + "\nСтворення коду розділене на модулі. Зараз поверни ТІЛЬКИ маніфест PythonBundle, БЕЗ КОДУ. "
                "Розбий реалізацію на невеликі .py модулі, кожен до 80 рядків. main.py лише координує їх. "
                "Маніфест має бути стислим: contract кожного модуля до 240 символів, лише сигнатури й коротка роль; не переказуй завдання. "
                "У contract кожного модуля задай точні назви функцій, аргументи, типи повернення, "
                "спільні назви файлів і відповідальність. Перелічуй тільки власні файли, не пакети залежностей. "
                "Окремі checks мають читати фактичні результати та містити assert. Не дублюй навчання в перевірках. "
                "expected_files містить вихідні дані/графіки, не вихідний код.",
                CompactPythonBundle,
            )
        except ValidationError as error:
            # Compactness is a request preference, not a reason to discard a
            # complete paid contract. Keep the same wire schema/cache key and
            # validate the original response against the bounded full schema.
            only_contract_length = all(
                item['type'] == 'string_too_long' and len(item['loc']) == 3
                and item['loc'][0] in ('files', 'checks') and item['loc'][2] == 'contract'
                for item in error.errors()
            )
            if not assisted or not only_contract_length:
                raise
            bundle = model.revalidate_last_response(PythonBundle)
        checkpoint_write(manifest_path, bundle)
    paths = [item.path for item in bundle.files + bundle.checks]
    workspace = config.project_dir(pid) / "workspace"
    check_specs = {
        str(config.safe_path(workspace, item.path)).casefold(): item
        for item in bundle.checks
    }
    implementation = []
    for item in bundle.files:
        duplicate = check_specs.get(
            str(config.safe_path(workspace, item.path)).casefold()
        )
        if duplicate:
            if Path(item.path).name == "main.py":
                raise ValueError("main.py не може бути одночасно окремою перевіркою")
            if duplicate.contract != item.contract:
                duplicate.contract = item.contract + "\n" + duplicate.contract
            continue
        implementation.append(item)
    bundle.files = implementation
    paths = [item.path for item in bundle.files + bundle.checks]
    canonical = [str(config.safe_path(workspace, path)).casefold() for path in paths]
    if "main.py" not in [item.path for item in bundle.files] or len(
        set(canonical)
    ) != len(paths):
        raise ValueError("Маніфест повинен мати main.py та унікальні шляхи")
    for path in paths:
        resolved = config.safe_path(workspace, path)
        if (
            Path(path).suffix.lower() != ".py"
            or resolved.relative_to(workspace.resolve()).parts[0].casefold() == "inputs"
        ):
            raise ValueError(
                "Маніфест коду може містити лише власні .py файли поза inputs"
            )
    for path in bundle.expected_files:
        output=config.safe_path(config.project_dir(pid) / "workspace", path)
        if str(output).casefold() in canonical:
            raise ValueError('expected_files містить лише вихідні результати, не файли реалізації чи перевірок')
    order = sorted(bundle.files, key=lambda item: item.path == "main.py")
    generated, contracts = {}, []
    manifest = bundle.model_dump_json()
    for index, spec in enumerate(order + bundle.checks):
        if cancelled():
            raise RuntimeError("Генерацію призупинено")
        checkpoint = cache / f"file-{index}.json"
        if checkpoint.exists():
            source = ModuleSource.model_validate_json(checkpoint.read_text("utf-8"))
        else:
            source = model.generate(
                prompt
                + f"\nМаніфест узгоджених модулів (дані): {manifest}\n"
                + "Реально створені інтерфейси модулів:\n"
                + "\n".join(contracts)
                + "\nФактичний код уже створених модулів (дані; пропуски позначені):\n"
                + generated_source_context(generated)
                + f"\nСтвори ТІЛЬКИ вміст файлу {spec.path}. Контракт: {spec.contract}. "
                'Поле content містить лише вихідний код Python цього файлу. Не вкладайте у content JSON, Action, PythonBundle, перелік files/checks або об’єкт path/content. '
                "До 80 рядків, без заглушок, без Markdown-огорож. "
                "Імпортуй допоміжні функції саме з названих у маніфесті модулів. "
                "Не змінюй інтерфейси вже створених модулів. При імпорті модуля не запускай навчання. "
                "Для спільних JSON/CSV/NPZ звір точні шляхи, вкладені ключі, типи й назви полів з фактичним кодом producer. "
                "Назва функції не визначає формат її результату. Checker має читати записаний producer формат, "
                "не вигадувати іншу структуру та не відтворювати випадковий експеримент іншим алгоритмом. "
                "Якщо потрібного producer немає в контексті, не видавай припущення про його схему за перевірений контракт. "
                "Для перевірок використовуй assert за фактичними файлами, не безумовне assert True.",
                ModuleSource,
            )
            tree = validate_source(spec.path, source.content)
            if spec in bundle.checks and not any(
                isinstance(n, ast.Assert) for n in ast.walk(tree)
            ):
                raise ValueError(f"Перевірка {spec.path} повинна містити assert")
            checkpoint_write(checkpoint, source)
        generated[spec.path] = FileWrite(path=spec.path, content=source.content)
        contracts.append(signatures(spec.path, source.content))
        db.event(
            pid,
            "codegen_file",
            {"step_id": sid, "file": spec.path, "done": index + 1, "total": len(paths)},
        )
    return Action(
        files=[generated[item.path] for item in bundle.files],
        checks=[generated[item.path] for item in bundle.checks],
        entrypoint="main.py",
        expected_files=bundle.expected_files,
        explanation=bundle.explanation,
        stdin_text=bundle.stdin_text,
    )


def generated_source_context(generated,budget=60000):
    """Pass real producer bodies, not just function names, to later modules.

    Include whole modules only; an omitted body must be explicit. Existing
    paid module checkpoints remain reusable and are still checked by preflight.
    """
    included=[];omitted=[]
    for path,file in reversed(list(generated.items())):
        block=f'[{path}]\n{file.content}\n'
        if len(block)<=budget:
            included.append(block);budget-=len(block)
        else:
            omitted.append(path)
    return '\n'.join(reversed(included))+('\nКод не вмістився: '+', '.join(omitted) if omitted else '')
