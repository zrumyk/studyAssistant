"""Export reviewable source only; never include models, user files or credentials."""

from pathlib import Path
import argparse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
args = parser.parse_args()
files = [
    "README.md",
    "STATUS.md",
    "requirements.txt",
    "requirements.lock.txt",
    "pyproject.toml",
    ".gitignore",
    ".env.example",
    "start.cmd",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
]
patterns = {
    "backend": {".py"},
    "scripts": {".py", ".ps1"},
    "tests": {".py", ".json"},
    "docs": {".md"},
    "frontend/src": {".ts", ".tsx", ".css", ".svg"},
    "frontend/public": {".svg", ".png", ".ico"},
    "frontend/dist": {".html", ".js", ".css", ".svg", ".png", ".ico"},
}
for folder, suffixes in patterns.items():
    files.extend(
        str(p.relative_to(ROOT))
        for p in (ROOT / folder).rglob("*")
        if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts
    )
args.output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
    for relative in sorted(set(files)):
        path = ROOT / relative
        if path.is_file():
            archive.write(path, "StudyAssistant/" + path.relative_to(ROOT).as_posix())
with zipfile.ZipFile(args.output) as archive:
    assert archive.testzip() is None
    print(f"{len(archive.namelist())} files; {args.output.stat().st_size} bytes")
