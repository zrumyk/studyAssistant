from pathlib import Path
from typing import Protocol
import hashlib
import json
import re
import shutil
import subprocess
import time
from . import config, db

SQL_RUNNER = """import sqlite3, pathlib, json
c = sqlite3.connect('results.sqlite')
text = pathlib.Path('main.sql').read_text(encoding='utf-8')
pending = ''
results = []
def execute(statement):
    cursor = c.execute(statement)
    if cursor.description:
        result = {'columns': [d[0] for d in cursor.description], 'rows': cursor.fetchall()}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))
for char in text:
    pending += char
    if char == ';' and sqlite3.complete_statement(pending):
        execute(pending)
        pending = ''
if pending.strip():
    execute(pending)
c.commit()
pathlib.Path('results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
c.close()
"""

CSHARP_RUNNER = r"""set -e
printf '%s\n' '-nologo' '-target:exe' '-out:/tmp/program.dll' '-langversion:latest' > /tmp/compiler.rsp
for library in /usr/share/dotnet/packs/Microsoft.NETCore.App.Ref/*/ref/net8.0/*.dll; do
    printf '%s\n' "-r:$library" >> /tmp/compiler.rsp
done
printf '%s\n' 'global using System; global using System.IO; global using System.Linq; global using System.Collections.Generic; global using System.Threading; global using System.Threading.Tasks;' > /tmp/GlobalUsings.cs
printf '%s\n' 'Program.cs' '/tmp/GlobalUsings.cs' >> /tmp/compiler.rsp
for compiler in /usr/share/dotnet/sdk/*/Roslyn/bincore/csc.dll; do
    dotnet "$compiler" @/tmp/compiler.rsp
    break
done
printf '%s' '{"runtimeOptions":{"tfm":"net8.0","framework":{"name":"Microsoft.NETCore.App","version":"8.0.0"}}}' > /tmp/program.runtimeconfig.json
dotnet /tmp/program.dll
"""

RUNTIMES = {
    "python": ("python:3.12-slim", "main.py", ["python", "main.py"]),
    "javascript": ("node:22-bookworm-slim", "main.js", ["node", "main.js"]),
    "typescript": (
        "node:22-bookworm-slim",
        "main.ts",
        [
            "sh",
            "-c",
            "/opt/deps/node_modules/.bin/tsc main.ts --target ES2022 --module commonjs --moduleResolution node --skipLibCheck --baseUrl /opt/deps/node_modules --typeRoots /opt/deps/node_modules/@types --outDir /tmp/compiled && node /tmp/compiled/main.js",
        ],
    ),
    "c": (
        "gcc:14-bookworm",
        "main.c",
        ["sh", "-c", "gcc main.c -O2 -lm -o /tmp/program && /tmp/program"],
    ),
    "cpp": (
        "gcc:14-bookworm",
        "main.cpp",
        ["sh", "-c", "g++ main.cpp -std=c++20 -O2 -o /tmp/program && /tmp/program"],
    ),
    "java": (
        "eclipse-temurin:21-jdk",
        "Main.java",
        ["sh", "-c", "javac -d /tmp Main.java && java -cp /tmp Main"],
    ),
    "csharp": (
        "mcr.microsoft.com/dotnet/sdk:8.0",
        "Program.cs",
        [
            "sh",
            "-c",
            CSHARP_RUNNER,
        ],
    ),
    "r": ("r-base:4.4.3", "main.R", ["Rscript", "main.R"]),
    "sql": (
        "python:3.12-slim",
        "main.sql",
        [
            "python",
            "-c",
            SQL_RUNNER,
        ],
    ),
    "octave": (
        "gnuoctave/octave:9.2.0",
        "main.m",
        ["octave", "--no-gui", "--quiet", "main.m"],
    ),
}


def owner_label():
    return hashlib.sha256(str(config.DATA).encode()).hexdigest()[:16]


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').digest()


class RuntimeAdapter(Protocol):
    def run(self, pid, step_id, language, dependencies, timeout, cancelled,
            expected_files=None, check_paths=None, resource_probe=None, stdin_text=None,
            visible_files=None, publish_files=None, readonly_artifacts=None): ...


def docker_available():
    try:
        r = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=8,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def dependencies(language, items):
    items = list(dict.fromkeys(items))
    pattern = r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:==[A-Za-z0-9_.+-]+)?"
    if language in ('javascript', 'typescript'):
        # Registry names and exact SemVer only: never shell options, URLs or paths.
        name = r"[a-z0-9][a-z0-9._-]*"
        version = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
        pattern = rf"(?:@{name}/)?{name}(?:@{version})?"
    for item in items:
        if not isinstance(item, str) or len(item) > 214 or not re.fullmatch(pattern, item):
            raise ValueError(f"Неприпустима назва залежності: {item}")
        if language == 'typescript':
            for bootstrap, pinned in (('typescript', '5.7.3'), ('@types/node', '22.10.2')):
                if item.startswith(bootstrap + '@') and item != bootstrap + '@' + pinned:
                    raise ValueError(f"Середовище TypeScript використовує {bootstrap}@{pinned}; запитано {item}. Інша версія потребує окремого профілю середовища.")
        if language == "python":
            import sys

            if item.split("==")[0].lower() in sys.stdlib_module_names | {
                "python",
                "pip",
            }:
                raise ValueError(
                    f"{item} уже входить до середовища Python; приберіть його зі сторонніх залежностей"
                )
    if items and language not in ("python", "javascript", "typescript", "r"):
        raise ValueError(
            "Для цього середовища сторонні залежності потребують ручного кроку"
        )
    return sorted(items)


def container_packages(language,packages):
    if language!='python':
        return list(packages)
    replacements={'opencv-python':'opencv-python-headless','opencv-contrib-python':'opencv-contrib-python-headless'}
    result=[]
    for package in packages:
        name,separator,version=package.partition('==')
        normalized=name.lower().replace('_','-')
        result.append(replacements.get(normalized,name)+(separator+version if separator else ''))
    return sorted(set(result))


def effective_dependencies(language, packages):
    """The actual image install requests, not an installed-package inventory."""
    packages = container_packages(language, dependencies(language, packages))
    if language == 'typescript':
        bootstrap = ('typescript', '@types/node', 'typescript@5.7.3', '@types/node@22.10.2')
        packages = sorted(set([p for p in packages if p not in bootstrap]
                              + ['typescript@5.7.3', '@types/node@22.10.2']))
    return packages


class DockerRuntime:
    def prepare(self, language, packages, *, python_checker=False):
        if language not in RUNTIMES:
            raise ValueError("Непідтримувана мова")
        packages = effective_dependencies(language, packages)
        base, _, _ = RUNTIMES[language]
        native_python = language == "python" and bool(packages)
        profile = [base, packages] + (["libgomp1"] if native_python else [])
        if python_checker:
            if language not in ('c', 'javascript'):
                raise ValueError('Unsupported shared checker image profile')
            profile += ['stdlib-python-checker-v1']
        image = (
            "study-runtime:"
            + hashlib.sha256(json.dumps(profile).encode()).hexdigest()[:16]
        )
        check = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, timeout=15
        )
        if check.returncode == 0:
            return image
        # The free-space reserve for builds is enforced by disk_guard on the data drive.
        folder = config.DATA / "runtime-builds" / image.split(":")[1]
        folder.mkdir(parents=True, exist_ok=True)
        lines = ["FROM " + base, "WORKDIR /opt/deps"]
        if python_checker:
            lines += ['RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*']
        if native_python:
            lines += [
                "RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*"
            ]
        if packages:
            if language == "python":
                lines += ["RUN pip install --no-cache-dir " + " ".join(packages)]
            elif language in ("javascript", "typescript"):
                lines += [
                    "RUN npm install --no-audit --no-fund " + " ".join(packages),
                    "ENV NODE_PATH=/opt/deps/node_modules",
                ]
            elif language == "r":
                names = ",".join('"' + p + '"' for p in packages)
                lines += [
                    "RUN Rscript -e 'install.packages(c("
                    + names
                    + '), repos="https://cloud.r-project.org")\''
                ]
        lines += ["WORKDIR /work"]
        (folder / "Dockerfile").write_text("\n".join(lines) + "\n", "utf-8")
        from .disk_guard import build
        exit_code=build(["docker", "build", "--pull", "-t", image, str(folder)],folder/'build.log')
        if exit_code:
            raise RuntimeError(
                "Не вдалося підготувати середовище. Перевірте журнал збірки."
            )
        return image

    def run(
        self,
        pid,
        step_id,
        language,
        packages,
        timeout,
        cancelled,
        expected_files=None,
        check_paths=None,
        resource_probe=None,
        stdin_text=None,
        visible_files=None,
        publish_files=None,
        readonly_artifacts=None,
    ):
        from .execution_protocol import stdin_bytes, stdin_identity, validate_checker_paths, read_exit_status, stream_identity
        input_bytes = stdin_bytes(stdin_text)
        validate_checker_paths(language, check_paths)
        from .disk_guard import require_space
        require_space()
        if not docker_available():
            raise RuntimeError(
                "Docker Desktop не запущено. Запустіть його та продовжте роботу."
            )
        tracked = bool(db.query("SELECT id FROM projects WHERE id=?", (pid,)))

        def emit(kind, payload):
            if tracked:
                db.event(pid, kind, payload)

        emit(
            "runtime_prepare",
            {
                "language": language,
                "step_id": step_id,
                "message": "Підготовка ізольованого середовища",
            },
        )
        image = (self.prepare(language, packages, python_checker=True)
                 if check_paths and language != 'python' else self.prepare(language, packages))
        effective_packages=container_packages(language,dependencies(language,packages))
        if effective_packages!=sorted(set(packages)):
            emit('runtime_packages',{'requested':packages,'effective':effective_packages,'reason':'Контейнер працює без графічного інтерфейсу; використано OpenCV headless з тим самим cv2 API'})
        emit("runtime_ready", {"language": language, "step_id": step_id, "image": image})
        root = config.project_dir(pid)
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        runid = db.uid()
        run_dir = root / "runs" / runid
        run_dir.mkdir(parents=True)
        stdin_path = run_dir.parent / (runid + '.stdin')
        stdin_path.write_bytes(input_bytes)
        # Snapshot only regular project files. Never expose host user directories.
        expected_files = {name.replace('\\','/').rstrip('/') for name in (expected_files or [])}
        for p in workspace.rglob("*"):
            try:
                regular = not p.is_symlink() and p.is_file()
            except OSError:
                continue
            if regular:
                if p.name.startswith('.study_'):
                    continue
                rel = p.relative_to(workspace)
                if rel.parts[0] == "inputs":
                    continue
                relative_name=str(rel).replace('\\','/')
                if visible_files is not None:
                    from .artifact_contracts import covers
                    if not any(covers(name, relative_name) for name in visible_files):
                        continue
                if relative_name in expected_files or any(relative_name.startswith(name+'/') for name in expected_files):
                    continue
                src = config.safe_path(workspace, rel)
                dest = config.safe_path(run_dir, rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        name = "study-" + runid
        s = config.settings()
        output_limit=int(s['container_output_mb'])*1024**2
        command = [
            "docker",
            "run",
            "--name",
            name,
            "--label",
            "study-assistant.owner=" + owner_label(),
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            str(s["container_memory_mb"]) + "m",
            "--memory-swap",
            str(s["container_memory_mb"]) + "m",
            "--cpus",
            str(s["container_cpus"]),
            "--user",
            "65534:65534",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,exec,size=512m",
            "--mount",
            f"type=bind,source={run_dir},target=/work",
            "--workdir",
            "/work",
            "-e",
            "HOME=/tmp",
            "-e",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "-e",
            "MPLBACKEND=Agg",
            "-e",
            "DOTNET_CLI_HOME=/tmp",
            "-e",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1",
            "-e",
            "OMP_NUM_THREADS=" + str(max(1, int(s["container_cpus"]))),
            "-e",
            "OPENBLAS_NUM_THREADS=" + str(max(1, int(s["container_cpus"]))),
            "-e",
            "MKL_NUM_THREADS=" + str(max(1, int(s["container_cpus"]))),
            "-e",
            "TF_NUM_INTRAOP_THREADS=" + str(max(1, int(s["container_cpus"]))),
            "-e",
            "TF_NUM_INTEROP_THREADS=2",
            "--entrypoint",
            "sh",
            image,
            "-c",
        ]
        inputs = config.safe_path(workspace, "inputs")
        if inputs.is_dir():
            # Large source datasets stay read-only and outside the output quota.
            command[2:2] = [
                "--mount",
                f"type=bind,source={inputs},target=/work/inputs,readonly",
            ]
        if input_bytes:
            command[2:2] = ['--mount', f'type=bind,source={stdin_path},target=/study-stdin,readonly']
        if readonly_artifacts:
            import csv
            import io
            for relative in readonly_artifacts:
                if relative.split('/')[0] == 'inputs':
                    continue  # Entire imported-input namespace is already readonly.
                source = config.safe_path(run_dir, relative)
                if not source.exists():
                    raise ValueError('Readonly artifact missing from snapshot: ' + relative)
                spec = io.StringIO()
                csv.writer(spec, lineterminator='').writerow(['type=bind', 'source='+str(source), 'target=/work/'+relative, 'readonly'])
                command[2:2] = ['--mount', spec.getvalue()]
        # All shell commands here are fixed, never supplied by the model.
        cmd = RUNTIMES[language][2]
        import shlex

        line = " ".join(shlex.quote(x) for x in cmd)
        if resource_probe is not None:
            if language!='python' or resource_probe!='resource_probe.py' or not (run_dir/resource_probe).is_file():
                raise ValueError('Некоректна ресурсна проба поточного кроку')
            # Separate processes release probe memory and RNG state before the
            # actual experiment. A failed probe prevents the expensive main run.
        checks = []
        if language in ('python', 'c', 'javascript'):
            checks = (
                [config.safe_path(run_dir, relative) for relative in check_paths]
                if check_paths is not None
                else sorted(run_dir.glob("check_*.py")) if language == 'python' else []
            )
            if any(
                not path.is_file() or path.suffix.lower() != ".py" for path in checks
            ):
                raise ValueError("Файл незалежної перевірки не створено")
        if checks:
            shutil.copyfile(Path(__file__).with_name('checker_runner.py'),run_dir / '.study_checker_runner.py')
        capture_main = stdin_text is not None or bool(checks)
        input_source = '/study-stdin' if input_bytes else '/dev/null'
        if capture_main:
            line = ('(' + line + ') < ' + input_source
                    + ' > .study_main.stdout 2> .study_main.stderr; study_status=$?; '
                    + 'printf "%s" "$study_status" > .study_main.status; '
                    + 'cat .study_main.stdout; cat .study_main.stderr >&2; '
                    + '[ "$study_status" -eq 0 ] || exit "$study_status"')
        else:
            line += ' < ' + input_source
        for index, p in enumerate(checks):
            interpreter = 'python' if language == 'python' else 'python3'
            line += (' && { ' + interpreter + ' .study_checker_runner.py '
                     + shlex.quote(p.relative_to(run_dir).as_posix())
                     + ' < /dev/null; study_check_status=$?; printf "%s" "$study_check_status" > '
                     + f'.study_check_{index}.status; [ "$study_check_status" -eq 0 ]; }}')
        if resource_probe is not None:
            line = 'python resource_probe.py < /dev/null && { ' + line + '; }'
        command.append(line)
        stdout_path = run_dir.parent / (runid + ".stdout.log")
        stderr_path = run_dir.parent / (runid + ".stderr.log")
        started = time.monotonic()
        next_disk_check = started + 2
        next_progress = started + 30
        emit(
            "run_started", {"run_id": runid, "step_id": step_id, "timeout_seconds": timeout}
        )
        reason = ""
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            while proc.poll() is None:
                if time.monotonic() >= next_progress:
                    tail = ""
                    progress_path = run_dir / '.study_main.stdout' if capture_main else stdout_path
                    if progress_path.is_symlink() or not progress_path.is_file():
                        progress_path = stdout_path
                    with progress_path.open("rb") as progress_log:
                        progress_log.seek(max(0, progress_path.stat().st_size - 1800))
                        tail = progress_log.read().decode("utf-8", errors="replace")
                    emit(
                        "run_progress",
                        {
                            "run_id": runid,
                            "step_id": step_id,
                            "elapsed_seconds": int(time.monotonic() - started),
                            "log_tail": tail,
                        },
                    )
                    next_progress = time.monotonic() + 30
                too_large = False
                if time.monotonic() >= next_disk_check:
                    size = 0
                    for path in run_dir.rglob("*"):
                        try:
                            if not path.is_symlink() and path.is_file():
                                size += path.stat().st_size
                        except OSError:
                            continue
                    from .disk_guard import require_space
                    try:
                        require_space(margin_gib=1)
                        low_space=False
                    except RuntimeError:
                        low_space=True
                    too_large = size > output_limit or low_space
                    next_disk_check = time.monotonic() + 2
                if (
                    cancelled()
                    or time.monotonic() - started > timeout
                    or too_large
                    or stdout_path.stat().st_size + stderr_path.stat().st_size
                    > 10_000_000
                    or sum(path.stat().st_size for path in
                           (run_dir / '.study_main.stdout', run_dir / '.study_main.stderr')
                           if not path.is_symlink() and path.is_file()) > 10_000_000
                ):
                    reason = (
                        "Зупинено користувачем"
                        if cancelled()
                        else "Перевищено ліміт часу, файлів, журналу або доступного місця"
                    )
                    subprocess.run(
                        ["docker", "kill", name], capture_output=True, timeout=15
                    )
                    try:
                        proc.wait(15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                time.sleep(0.4)
        inspect = subprocess.run(
            ["docker", "inspect", name, "--format", "{{json .State}}"],
            capture_output=True,
            timeout=15,
            encoding="utf-8",
        )
        try:
            state = json.loads(inspect.stdout)
        except ValueError:
            state = {}
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        logs = (
            stdout_path.read_text("utf-8", errors="replace")
            + "\n"
            + stderr_path.read_text("utf-8", errors="replace")
        )[-30000:]
        generated = []
        total = 0
        produced = []
        for p in run_dir.rglob("*"):
            if "node_modules" in p.parts:
                continue
            if p.name.startswith('.study_'):
                continue
            try:
                regular = not p.is_symlink() and p.is_file()
            except OSError:
                continue
            if not regular:
                continue
            try:
                config.safe_path(run_dir, p.relative_to(run_dir))
            except ValueError:
                continue
            size = p.stat().st_size
            if size > output_limit or total + size > output_limit:
                continue
            total += size
            rel = p.relative_to(run_dir)
            produced.append(str(rel).replace("\\", "/"))
            if publish_files is not None:
                from .artifact_contracts import covers
                if not any(covers(name, rel.as_posix()) for name in publish_files):
                    continue
            dest = config.safe_path(workspace, rel)
            changed = (
                not dest.exists()
                or file_hash(dest) != file_hash(p)
            )
            if changed or str(rel).replace("\\", "/") in expected_files:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)
                generated.append(rel.as_posix())
        checker_results = []
        main_status_path = run_dir / '.study_main.status'
        main_status = read_exit_status(main_status_path)
        chain_active = main_status == 0
        for index, path in enumerate(checks):
            status_path = run_dir / f'.study_check_{index}.status'
            status = None
            if chain_active:
                status = read_exit_status(status_path)
            chain_active = chain_active and status == 0
            checker_results.append({'path': path.relative_to(run_dir).as_posix(),
                                    'completed': status is not None, 'exit_code': status})
        result = {
            "run_id": runid,
            "exit_code": proc.returncode,
            "oom": bool(state.get("OOMKilled")),
            "reason": reason,
            "log": logs,
            "files": generated,
            "produced_files": produced,
            "seconds": round(time.monotonic() - started, 2),
            "image": image,
            "resource_limits": {
                "memory_mb": s['container_memory_mb'],
                "cpus": s['container_cpus'],
                "timeout_seconds": timeout,
            },
            "checks": [item['path'] for item in checker_results if item['exit_code'] == 0],
            "checks_requested": [path.relative_to(run_dir).as_posix() for path in checks],
            "checker_results": checker_results,
            "resource_probe": resource_probe,
            "artifact_visibility": {'visible_files': visible_files, 'publish_files': publish_files,
                                    'readonly_artifacts': readonly_artifacts},
            "execution_protocol": {
                "version": 2, "stdin": stdin_identity(stdin_text),
                "stdin_artifact": stdin_path.name,
                "main_stdout": '.study_main.stdout' if capture_main else None,
                "main_stderr": '.study_main.stderr' if capture_main else None,
                "main_exit_code": main_status,
                "streams": {name: stream_identity(run_dir / ('.study_main.' + name))
                            for name in ('stdout', 'stderr')} if capture_main else {},
                "checker_language": 'python' if checks else None,
                "checker_runtime": 'stdlib' if language != 'python' and checks else 'producer',
            },
        }
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), "utf-8"
        )
        return result
