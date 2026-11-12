"""Public downloads broker. Generated programs have no network access."""

from pathlib import Path
import ipaddress
import socket
from urllib.parse import urlparse, urljoin
import httpx


def public_url(url):
    p = urlparse(url)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.port not in (None, 443)
    ):
        raise ValueError("Дозволені тільки публічні HTTPS-посилання")
    for item in socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(item[4][0]).is_global:
            raise ValueError("Локальні й приватні адреси заборонені")
    return url


def fetch(url, destination, *, max_bytes=50_000_000, progress=None,allowed_hosts=None):
    if not isinstance(max_bytes, int) or not 1 <= max_bytes <= 3_000_000_000:
        raise ValueError("Неприпустимий ліміт завантаження")
    with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as c:
        for _ in range(5):
            if allowed_hosts is not None and urlparse(url).hostname not in allowed_hosts:
                raise ValueError('Перенаправлення за межі дозволених сайтів документації')
            public_url(url)
            parsed = urlparse(url)
            addresses = socket.getaddrinfo(
                parsed.hostname, 443, type=socket.SOCK_STREAM
            )
            address = addresses[0][4][0]
            if not ipaddress.ip_address(address).is_global:
                raise ValueError("Адреса змінилася на приватну")
            # Pin the validated IP for this request while preserving TLS hostname verification.
            target = httpx.URL(url).copy_with(host=address)
            with c.stream(
                "GET",
                target,
                headers={"User-Agent": "StudyAssistant/0.1", "Host": parsed.hostname},
                extensions={"sni_hostname": parsed.hostname},
            ) as r:
                if r.is_redirect:
                    url = urljoin(url, r.headers["location"])
                    continue
                r.raise_for_status()
                total = int(r.headers.get("content-length", "0"))
                if total > max_bytes:
                    raise ValueError("Файл перевищує погоджений ліміт завантаження")
                length = 0
                destination = Path(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with destination.open("wb") as f:
                        for block in r.iter_bytes():
                            length += len(block)
                            if length > max_bytes:
                                raise ValueError("Перевищено ліміт завантаження")
                            f.write(block)
                            if progress:
                                progress(length, total)
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                return {
                    "url": url,
                    "bytes": length,
                    "content_type": r.headers.get("content-type", ""),
                }
    raise ValueError("Забагато перенаправлень")
