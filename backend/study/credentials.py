"""Read the one allowed .env credential without exporting it to child processes."""

from . import config


def deepseek_key():
    path = config.ROOT / ".env"
    if not path.is_file():
        return None
    if path.stat().st_size > 65536:
        raise ValueError("Файл .env завеликий")
    found = []
    for line in path.read_text("utf-8-sig").splitlines():
        name, separator, value = line.strip().removeprefix("export ").partition("=")
        if not separator or name.strip() != "DEEPSEEK_API_KEY":
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (
                value[end + 1 :].strip()
                and not value[end + 1 :].strip().startswith("#")
            ):
                raise ValueError("Перевірте лапки DEEPSEEK_API_KEY у .env")
            value = value[1:end]
        else:
            value = value.split(" #", 1)[0].rstrip()
        if any(ch.isspace() for ch in value) or any(
            ch in value for ch in ("'", '"', "$", "`")
        ):
            raise ValueError("Перевірте формат DEEPSEEK_API_KEY у .env")
        found.append(value)
    if len(found) > 1:
        raise ValueError("Залиште тільки один DEEPSEEK_API_KEY у .env")
    return found[0] or None if found else None
