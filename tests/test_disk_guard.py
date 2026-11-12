from types import SimpleNamespace
import pytest
from study import disk_guard


def test_reserve_stops_before_ten_gib(monkeypatch):
    monkeypatch.setattr(disk_guard.shutil,'disk_usage',lambda p:SimpleNamespace(free=12*1024**3))
    with pytest.raises(RuntimeError,match='10 GiB'):disk_guard.require_space()
    disk_guard.require_space(margin_gib=1)


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
