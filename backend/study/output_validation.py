"""Validate newly produced files or nonempty output directories."""
from . import config


def validate_expected_outputs(workspace,result,expected):
    produced={name.replace('\\','/') for name in result.get('produced_files',[])}
    for relative in expected:
        name=relative.replace('\\','/').rstrip('/')
        path=config.safe_path(workspace,name)
        if not name or path==workspace.resolve():
            raise ValueError('Очікуваний результат має бути файлом чи окремою текою')
        if path.is_file() and name in produced:
            continue
        if path.is_dir() and any(item.startswith(name+'/') and config.safe_path(workspace,item).is_file() for item in produced):
            continue
        raise ValueError('Цей запуск не створив очікуваний результат: '+relative)
