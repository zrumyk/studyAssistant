"""Preserve the user's 10 GiB reserve with earlier stop margins.

The reserve applies to the drive holding the project data and the Docker storage
(D: on the user's machine). The system drive C: is no longer checked: by the user's
decision of 13.11.2026 Docker images live on D:, and the C: check blocked builds
while D: had 30 GiB free.
"""
import shutil
import subprocess
import time
from . import config


def require_space(margin_gib=3):
    paths=[config.DATA,config.ROOT]
    for path in paths:
        free=shutil.disk_usage(path).free
        if free < (10+margin_gib)*1024**3:
            raise RuntimeError(f'Резерв диска: потрібно зберігати 10 GiB вільно; доступно {free/1024**3:.2f} GiB, запас зупинки {margin_gib} GiB')


def build(command,log,timeout=1200):
    require_space()
    started=time.monotonic()
    with log.open('w',encoding='utf-8') as output:
        proc=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                require_space()
                if time.monotonic()-started>timeout:
                    raise RuntimeError('Перевищено час збірки середовища')
                time.sleep(0.5)
        except BaseException:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait(timeout=5)
            raise
        return proc.returncode
