"""Finite input and shared checker capability, independent of lab/topic."""
import hashlib

CHECKER_PROFILES = frozenset(('python', 'c', 'javascript'))
MAX_STDIN_BYTES = 65536


def stdin_bytes(value):
    if value is not None and not isinstance(value, str):
        raise ValueError('stdin_text must be text or null')
    data = (value or '').encode('utf-8')
    if len(data) > MAX_STDIN_BYTES:
        raise ValueError('stdin_text exceeds 65536 UTF-8 bytes')
    return data


def stdin_identity(value):
    data = stdin_bytes(value)
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def validate_checker_paths(language, paths):
    if paths and language not in CHECKER_PROFILES:
        raise ValueError('Окремі checker-файли цього профілю ще не підтримуються: ' + language
                         + '. Їх не можна вважати виконаними; потрібен підтримуваний протокол перевірки.')
    if paths and any(not path.endswith('.py') for path in paths):
        raise ValueError('Спільний checker-протокол потребує Python .py з assert, незалежно від мови producer')


def read_exit_status(path):
    """Never load an untrusted status file wholesale or accept a numeric prefix."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open('rb') as stream:
            value = stream.read(4)
        if not value or len(value) > 3 or any(byte < 48 or byte > 57 for byte in value):
            return None
        status = int(value)
        return status if status <= 255 else None
    except OSError:
        return None


def stream_identity(path):
    if path.is_symlink() or not path.is_file():
        return None
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(block)
            digest.update(block)
    return {'bytes': size, 'sha256': digest.hexdigest()}
