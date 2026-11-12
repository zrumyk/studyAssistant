from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("STUDY_DATA", str(ROOT / "data"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "study.sqlite3"
DEFAULTS = {
    "ollama_url": "http://127.0.0.1:11434",
    "model": "qwen3.5:4b",
    "coding_model": "",
    "embedding_model": "embeddinggemma",
    "context": 4096,
    "library_root": "",
    "semester_root": str(ROOT / "materials" / "semester"),
    "auto_defense": True,
    "cloud_provider": "gemini",
    "cloud_model": "gemini-3.1-flash-lite",
    "cloud_thinking": False,
    "cloud_consent_mode": "per_request",
    "cloud_request_limit_usd": 0.03,
    "cloud_budget": 5.0,
    "cloud_input_price": 0.25,
    "cloud_output_price": 1.5,
    "cloud_cache_input_price": None,
    "cloud_deepseek_offpeak": False,
    "cloud_prices_date": "2026-10-19",
    "cloud_max_output": 4096,
    "author": "",
    "group": "",
    "institution": "",
    "poppler": "",
    "tesseract": "",
    "soffice": "",
    "container_memory_mb": 4096,
    "container_cpus": 4,
    "container_output_mb": 1024,
}


def settings():
    p = DATA / "settings.json"
    return DEFAULTS | (json.loads(p.read_text("utf-8")) if p.exists() else {})


def save_settings(values):
    current = settings()
    current.update({k: v for k, v in values.items() if k in DEFAULTS})
    if current["cloud_provider"] not in ("gemini", "deepseek"):
        raise ValueError("Невідомий хмарний провайдер")
    if current["cloud_consent_mode"] not in ("per_request", "budgeted"):
        raise ValueError("Невідомий режим дозволу хмари")
    if not isinstance(current["cloud_thinking"], bool):
        raise ValueError("Режим міркування має бути логічним значенням")
    if not isinstance(current['cloud_deepseek_offpeak'],bool):
        raise ValueError('Режим позапікового тарифу має бути логічним значенням')
    if current['cloud_cache_input_price'] is not None:
        import math
        price=float(current['cloud_cache_input_price'])
        if not math.isfinite(price) or price<0:raise ValueError('Некоректний тариф кешованого входу')
        current['cloud_cache_input_price']=price
    if not isinstance(current["auto_defense"], bool):
        raise ValueError("Автоматична підготовка до захисту має бути логічним значенням")
    if current["context"] not in (4096, 8192):
        raise ValueError("Контекст має бути 4096 або 8192")
    from urllib.parse import urlparse

    u = urlparse(current["ollama_url"])
    if u.hostname not in ("localhost", "127.0.0.1") or u.scheme != "http":
        raise ValueError("Ollama має працювати локально через HTTP")
    for name in (
        "cloud_budget",
        "cloud_input_price",
        "cloud_output_price",
        "cloud_request_limit_usd",
    ):
        current[name] = float(current[name])
        import math

        if not math.isfinite(current[name]) or current[name] < 0:
            raise ValueError("Значення не може бути від’ємним")
    for name in ("model", "embedding_model", "coding_model"):
        if "cloud" in current[name].lower():
            raise ValueError(
                "У локальному режимі дозволені тільки завантажені моделі Ollama, без cloud"
            )
    current["container_memory_mb"] = min(
        6144, max(256, int(current["container_memory_mb"]))
    )
    current["container_cpus"] = min(6, max(1, int(current["container_cpus"])))
    current['container_output_mb']=min(4096,max(64,int(current['container_output_mb'])))
    current["cloud_max_output"] = min(8192, max(256, int(current["cloud_max_output"])))
    temp = DATA / "settings.tmp"
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), "utf-8")
    temp.replace(DATA / "settings.json")
    return current


def project_dir(pid):
    if not pid.isalnum():
        raise ValueError("Некоректний ідентифікатор")
    p = DATA / "projects" / pid
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_path(base, relative):
    base = Path(base).resolve()
    rel = Path(relative)
    if rel.is_absolute() or rel.drive:
        raise ValueError("Потрібен відносний шлях")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }
    if any(
        ":" in part
        or part.rstrip(". ").split(".")[0].upper() in reserved
        or part.endswith((" ", "."))
        and part not in (".", "..")
        for part in rel.parts
    ):
        raise ValueError("Неприпустиме ім’я файлу")
    p = (base / relative).resolve()
    if not p.is_relative_to(base) or p == base:
        raise ValueError("Шлях виходить за межі робочої папки")
    # Reject symlinks/junctions in all existing ancestors as well.
    for parent in (p, *p.parents):
        if parent == base:
            break
        if parent.is_symlink() or (
            hasattr(parent, "is_junction") and parent.is_junction()
        ):
            raise ValueError("Посилання на інші папки заборонені")
    return p
