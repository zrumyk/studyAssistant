from types import SimpleNamespace
import pytest
from study import config, disk_guard


def test_reserve_stops_before_ten_gib(monkeypatch):
    monkeypatch.setattr(disk_guard.shutil,'disk_usage',lambda p:SimpleNamespace(free=12*1024**3))
    with pytest.raises(RuntimeError,match='10 GiB'):disk_guard.require_space()
    disk_guard.require_space(margin_gib=1)


def test_only_data_drive_is_checked_not_system_drive(monkeypatch):
    checked=[]
    def usage(path):
        checked.append(str(path))
        return SimpleNamespace(free=20*1024**3)
    monkeypatch.setattr(disk_guard.shutil,'disk_usage',usage)
    disk_guard.require_space()
    assert checked==[str(config.DATA),str(config.ROOT)]


def test_running_build_is_terminated_on_low_space(monkeypatch,tmp_path):
    checks=iter([None,RuntimeError('reserve')]);terminated=[]
    def space():
        value=next(checks)
        if value:raise value
    monkeypatch.setattr(disk_guard,'require_space',space)
    class Process:
        def poll(self):return None
        def terminate(self):terminated.append(True)
        def wait(self,timeout):return 1
    monkeypatch.setattr(disk_guard.subprocess,'Popen',lambda *a,**k:Process())
    with pytest.raises(RuntimeError,match='reserve'):disk_guard.build(['docker','build'],tmp_path/'build.log')
    assert terminated==[True]
