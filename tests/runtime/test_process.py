import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import free_port
from lai_runtime import _posix
from lai_runtime.config import RuntimeConfig
from lai_runtime.ports import PortConflictError, port_in_use
from lai_runtime.process import RuntimeManager, RuntimeStartError, select_ops
from lai_setup.paths import resolve_paths

SRC = Path(__file__).resolve().parents[2] / "src"


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _wait_dead(pid, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def paths(tmp_path):
    return resolve_paths("linux", {"LAI_HOME": str(tmp_path / "lai")}, tmp_path)


@pytest.fixture
def mgr(paths, fake_binary):
    m = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=str(fake_binary),
                       base_env={"PATH": os.environ["PATH"], "HTTPS_PROXY": "http://proxy:1"})
    yield m
    m.stop()


def test_start_stop(mgr):
    st = mgr.start(timeout=15)
    assert st.running and st.owned and st.version == "0.0.0-fake"
    assert port_in_use("127.0.0.1", mgr.cfg.port)
    pid = st.pid
    assert mgr.stop() is True
    assert _wait_dead(pid)
    assert not port_in_use("127.0.0.1", mgr.cfg.port)
    assert not mgr.record_file.exists()
    assert mgr.status().running is False


def test_started_process_has_isolated_env(mgr):
    st = mgr.start(timeout=15)
    env = dict(e.split("=", 1) for e in
               Path(f"/proc/{st.pid}/environ").read_bytes().decode().split("\0") if "=" in e)
    assert env["OLLAMA_NO_CLOUD"] == "1"
    assert env["OLLAMA_HOST"] == f"127.0.0.1:{mgr.cfg.port}"
    assert env["OLLAMA_MODELS"] == str(mgr.paths.models)
    assert "HTTPS_PROXY" not in env


def test_second_start_reuses_own_process(mgr):
    a = mgr.start(timeout=15)
    b = mgr.start(timeout=15)
    assert a.pid == b.pid


def test_restart_gives_new_process(mgr):
    a = mgr.start(timeout=15)
    b = mgr.restart(timeout=15)
    assert b.running and b.pid != a.pid
    assert _wait_dead(a.pid)


def test_port_conflict_leaves_other_service_untouched(paths, fake_binary, fake_server):
    base, _, _ = fake_server
    port = int(base.rsplit(":", 1)[1])
    m = RuntimeManager(RuntimeConfig(port=port), paths, binary=str(fake_binary))
    with pytest.raises(PortConflictError):
        m.start(timeout=5)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base + "/api/version", timeout=2) as r:
        assert json.load(r)["version"] == "0.0.0-fake"
    assert m.stop() is False
    with opener.open(base + "/api/version", timeout=2) as r:
        assert r.status == 200


def test_stop_never_kills_foreign_process(mgr):
    victim = subprocess.Popen(["sleep", "60"])
    try:
        mgr.paths.runtime.mkdir(parents=True, exist_ok=True)
        mgr.record_file.write_text(json.dumps({
            "pid": victim.pid, "start_id": _posix.PosixOps().start_id(victim.pid),
            "port": mgr.cfg.port, "owner": "baskasinin", "binary": "/usr/bin/sleep"}))
        assert mgr.stop() is False
        time.sleep(0.2)
        assert victim.poll() is None
        assert not mgr.record_file.exists()  # bayat kayıt temizlenir
    finally:
        victim.kill()
        victim.wait()


def test_stale_record_of_dead_process_is_cleaned(mgr):
    mgr.paths.runtime.mkdir(parents=True, exist_ok=True)
    mgr.record_file.write_text(json.dumps({"pid": 999999, "start_id": "1", "port": mgr.cfg.port,
                                           "owner": "x", "binary": "y"}))
    assert mgr.status().running is False
    assert mgr.start(timeout=15).running


def test_crashing_binary_reports_log_and_clears_record(paths, tmp_path):
    b = tmp_path / "cokus"
    b.write_text("#!/bin/sh\necho 'Error: listen tcp: bilerek çöktü' >&2\nexit 1\n")
    b.chmod(0o755)
    m = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=str(b))
    with pytest.raises(RuntimeStartError, match="bilerek çöktü"):
        m.start(timeout=10)
    assert not m.record_file.exists()


def test_bind_race_reported_as_port_conflict(paths, tmp_path):
    b = tmp_path / "yaris"
    b.write_text("#!/bin/sh\necho 'Error: listen tcp 127.0.0.1:1: bind: address already in use' >&2\nexit 1\n")
    b.chmod(0o755)
    m = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=str(b))
    with pytest.raises(PortConflictError):
        m.start(timeout=10)


def test_missing_binary(paths, monkeypatch):
    monkeypatch.setenv("PATH", "/yok")
    m = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=None,
                       base_env={"PATH": "/yok"})
    with pytest.raises(RuntimeStartError, match="bulunamadı"):
        m.start(timeout=5)


def test_child_dies_when_app_is_killed(paths, fake_binary, tmp_path):
    port = free_port()
    helper = tmp_path / "app.py"
    helper.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(SRC)!r})
        import lai_runtime.guard as guard
        guard._verified = lambda: True  # sahte ikili; gerçek kapı test_model_guard'da
        from lai_runtime.config import RuntimeConfig
        from lai_runtime.process import RuntimeManager
        from lai_setup.paths import resolve_paths
        from pathlib import Path
        p = resolve_paths("linux", {{"LAI_HOME": {str(tmp_path / "lai2")!r}}}, Path("/tmp"))
        m = RuntimeManager(RuntimeConfig(port={port}), p, binary={str(fake_binary)!r})
        print(m.start(timeout=15).pid, flush=True)
        time.sleep(120)
    """))
    app = subprocess.Popen([sys.executable, str(helper)], stdout=subprocess.PIPE, text=True)
    child = int(app.stdout.readline())
    assert _alive(child)
    app.send_signal(signal.SIGKILL)
    app.wait()
    assert _wait_dead(child), "uygulama öldüğünde Ollama süreci kapanmadı"


def test_select_ops_per_platform():
    assert type(select_ops("linux")).__name__ == "PosixOps"
    assert type(select_ops("windows")).__name__ == "WindowsOps"


class FakeKernel32:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def f(*args):
            self.calls.append(name)
            return 1234 if name in ("CreateJobObjectW", "OpenProcess") else 1
        return f


def test_windows_job_object_kills_children_on_close():
    from lai_runtime._windows import JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, WindowsOps
    k = FakeKernel32()
    ops = WindowsOps(kernel32=k)
    ops.attach_job(proc_handle=55)
    assert k.calls[:3] == ["CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject"]
    assert ops.last_job_flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE


def test_port_change_never_orphans_owned_process(paths, fake_binary):
    a_mgr = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=str(fake_binary))
    a = a_mgr.start(timeout=15)
    b_mgr = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=str(fake_binary))
    b = b_mgr.start(timeout=15)
    try:
        assert b.pid != a.pid
        assert _wait_dead(a.pid), "eski sahipli süreç sahipsiz bırakıldı"
        assert not port_in_use("127.0.0.1", a_mgr.cfg.port)
    finally:
        b_mgr.stop()
        if _alive(a.pid):
            os.kill(a.pid, signal.SIGKILL)


def test_failed_terminate_keeps_record(mgr, monkeypatch):
    st = mgr.start(timeout=15)
    monkeypatch.setattr(mgr.ops, "terminate", lambda pid, timeout: None)  # ör. EPERM
    assert mgr.stop(timeout=0.5) is False
    assert mgr.record_file.exists(), "süreç yaşarken sahiplik kaydı silindi"
    monkeypatch.undo()
    assert mgr.stop() is True
    assert _wait_dead(st.pid)


def test_start_refuses_when_previous_owned_process_cannot_be_stopped(paths, fake_binary, monkeypatch):
    a_mgr = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=str(fake_binary))
    a = a_mgr.start(timeout=15)
    b_mgr = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=str(fake_binary))
    monkeypatch.setattr(b_mgr.ops, "terminate", lambda pid, timeout: None)
    with pytest.raises(RuntimeStartError, match="kapatılamadı"):
        b_mgr.start(timeout=5)
    assert json.loads(a_mgr.record_file.read_text())["pid"] == a.pid
    assert not port_in_use("127.0.0.1", b_mgr.cfg.port)
    assert a_mgr.stop() is True
