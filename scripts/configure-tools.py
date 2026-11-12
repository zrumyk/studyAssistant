from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from study import config

values = {}
for setting, folder, exe in [
    ("soffice", "LibreOffice", "soffice.exe"),
    ("tesseract", "tesseract", "tesseract.exe"),
    ("poppler", "poppler", "pdftoppm.exe"),
]:
    matches = list((config.ROOT / "tools" / folder).rglob(exe))
    if matches:
        values[setting] = str(matches[0])
config.save_settings(values)
print("Configured tools:", ", ".join(values) or "Set paths manually in Settings")
