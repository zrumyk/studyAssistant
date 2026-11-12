import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from study import cloud, config, db


@pytest.fixture
def control(monkeypatch):
    path = Path(__file__).resolve().parents[1]/'scripts/compare-planning-reviewers.py'
    spec = importlib.util.spec_from_file_location('technical_reviewer_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.psutil, 'process_iter', lambda *args: [])
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda *args: SimpleNamespace(free=12*2**30))
    return module


@pytest.mark.parametrize('state', ['quoted', 'approved', 'sending', 'uncertain', 'failed'])
def test_control_stops_after_unconfirmed_receipt(control, project, state):
    config.save_settings({'cloud_prices_date': db.now()[:10]})
    row = cloud.quote(project['id'], 'Synthetic guard fixture', 'test only')
    db.execute('UPDATE cloud_requests SET state=? WHERE id=?', (state, row['id']))
    with pytest.raises(RuntimeError, match='Unconfirmed or failed receipt'):
        control.guard(project['id'], 20)


def test_control_cannot_start_with_competing_worker(control, project, monkeypatch):
    worker = SimpleNamespace(info={'cmdline': [str(config.ROOT/'.venv/Scripts/python.exe'), '-m', 'study.worker']})
    monkeypatch.setattr(control.psutil, 'process_iter', lambda *args: [worker])
    with pytest.raises(RuntimeError, match='competing consumers'):
        control.guard(project['id'], 20)
    assert not db.query('SELECT id FROM cloud_requests')


def test_control_ceiling_and_disk_guard_precede_model_calls(control, project, monkeypatch):
    with pytest.raises(RuntimeError, match='ceiling'):
        control.guard(project['id'], 0)
    monkeypatch.setattr(control.shutil, 'disk_usage', lambda *args: SimpleNamespace(free=10*2**30))
    with pytest.raises(RuntimeError, match='reserve'):
        control.guard()
    assert not db.query('SELECT id FROM cloud_requests')
