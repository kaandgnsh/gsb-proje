import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lai_runtime import sandbox

REPO = Path(__file__).resolve().parents[2]


def test_controlled_external_targets_and_unix_brokers(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Gerçek Linux ad alanı testi; Windows kanıtı değildir")
    result = subprocess.run(
        [
            "unshare",
            "-Urnmfp",
            "--mount-proc",
            sys.executable,
            str(Path(__file__).with_name("network_lab.py")),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    proof = json.loads((tmp_path / "network-proof.json").read_text())
    assert all(proof["baseline"].values())
    assert not any(proof["protected"].values())
    assert not any(proof["protected_child"].values())
    assert proof["namespace_only"]["glibc_nss_resolve"]


def test_missing_bwrap_fails_closed(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda *a: None)
    with pytest.raises(sandbox.IsolationError, match="bubblewrap"):
        sandbox.launch([sys.executable, "-c", 'raise AssertionError("Çalışmamalı")'])


def test_windows_is_explicitly_unverified(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    with pytest.raises(sandbox.IsolationError, match="Windows"):
        sandbox.launch(["not-executed"])


def test_environment_flag_is_not_proof(monkeypatch):
    monkeypatch.setenv("LAI_SANDBOX_ACTIVE", "1")
    monkeypatch.setenv("LAI_SANDBOX_HOST_NETNS", "0")
    with pytest.raises(sandbox.IsolationError):
        sandbox.require_isolation()


def test_environment_drops_brokers_proxies_and_loaders():
    env = sandbox.clean_env(
        {
            "PATH": "/usr/bin",
            "LD_PRELOAD": "bad",
            "LD_LIBRARY_PATH": "bad",
            "HTTP_PROXY": "http://127.0.0.1",
            "ALL_PROXY": "unix:///tmp/proxy",
            "DBUS_SESSION_BUS_ADDRESS": "unix:/run/user/bus",
            "SSH_AUTH_SOCK": "/tmp/agent",
            "QT_PLUGIN_PATH": "/tmp/plugins",
            "OLLAMA_HOST": "0.0.0.0:11434",
        }
    )
    for key in [
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "HTTP_PROXY",
        "ALL_PROXY",
        "DBUS_SESSION_BUS_ADDRESS",
        "SSH_AUTH_SOCK",
        "QT_PLUGIN_PATH",
    ]:
        assert key not in env
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"


def test_unavailable_namespace_never_runs_fallback(monkeypatch, tmp_path):
    def denied(*a, **kw):
        return subprocess.CompletedProcess(a[0], 1, "", "namespace denied")

    monkeypatch.setattr(sandbox.subprocess, "run", denied)
    result = sandbox.launch(
        [sys.executable, "-c", f'open({str(tmp_path / "bad")!r},"w").write("BAD")']
    )
    assert result.returncode == 1 and not (tmp_path / "bad").exists()


def test_offline_workflows_inside_kernel_boundary(tmp_path):
    result = sandbox.launch(
        [
            sys.executable,
            str(Path(__file__).with_name("offline_workflow.py")),
            str(tmp_path),
        ],
        writable=[tmp_path],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(REPO / "src"), str(REPO / "tests/support")]
            ),
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["stored"] == 4 and report["reopened"] and not report["model_used"]
    assert all(report["kernel"].values())


def test_missing_seccomp_fails_closed(monkeypatch):
    monkeypatch.setattr(sandbox.ctypes.util, "find_library", lambda name: None)
    with pytest.raises(sandbox.IsolationError, match="libseccomp"):
        sandbox.launch(["not-executed"])


def test_desktop_missing_boundary_does_not_create_database(
    tmp_path, monkeypatch, capsys
):
    from lai.ui.__main__ import main

    monkeypatch.delenv("LAI_SANDBOX_ACTIVE", raising=False)
    monkeypatch.setenv("LAI_HOME", str(tmp_path / "app"))
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    db = tmp_path / "not-opened.db"
    assert main(["--db", str(db)]) == 2
    assert not db.exists()
    assert "bubblewrap" in capsys.readouterr().err


def test_desktop_spoofed_flag_is_rejected(tmp_path, monkeypatch, capsys):
    from lai.ui.__main__ import main

    monkeypatch.setenv("LAI_SANDBOX_ACTIVE", "1")
    monkeypatch.setenv("LAI_SANDBOX_HOST_NETNS", "0")
    db = tmp_path / "not-opened.db"
    assert main(["--db", str(db)]) == 2
    assert not db.exists()
    assert "değerlendirme açılmadı" in capsys.readouterr().err


def test_desktop_windows_unverified_blocks_evaluation(tmp_path, monkeypatch, capsys):
    from lai.ui.__main__ import main

    monkeypatch.delenv("LAI_SANDBOX_ACTIVE", raising=False)
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    db = tmp_path / "windows.db"
    assert main(["--db", str(db)]) == 2
    assert not db.exists()
    assert "Windows" in capsys.readouterr().err


def test_socket_families_and_escape_calls_are_kernel_blocked(tmp_path):
    code = """
import ctypes,errno,json,socket
from lai_runtime.sandbox import require_isolation
require_isolation()
results={}
for name in ['AF_UNIX','AF_NETLINK','AF_PACKET','AF_VSOCK']:
    try:
        s=socket.socket(getattr(socket,name)); s.close(); results[name]=False
    except OSError as e:
        results[name]=e.errno==errno.EPERM
libc=ctypes.CDLL(None,use_errno=True)
results['unshare']=libc.unshare(0)==-1 and ctypes.get_errno()==errno.EPERM
results['setns']=libc.setns(-1,0)==-1 and ctypes.get_errno()==errno.EPERM
# socketpair aynı korunan süreçte Qt/işçi iletişimi için çalışmalı.
a,b=socket.socketpair(); a.sendall(b'OK'); results['private_socketpair']=b.recv(2)==b'OK'
a.close(); b.close()
s=socket.socket(); s.bind(('127.0.0.1',0)); results['private_loopback']=True; s.close()
print(json.dumps(results))
"""
    result = sandbox.launch(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout).values())


def test_doctor_network_is_readonly_and_not_enforcement_proof(
    tmp_path, monkeypatch, capsys
):
    import socket

    from lai_runtime.netaudit import diagnostic
    from lai_setup.cli import main

    with socket.socket(socket.AF_UNIX) as unix:
        unix.bind(str(tmp_path / "visible.sock"))
        data = diagnostic(os.getpid())
        assert any(
            s["path"] == str(tmp_path / "visible.sock") for s in data["unix_sockets"]
        )

    def forbidden(*a, **kw):
        raise AssertionError("doctor --network normal kurulum/servis tespiti yapmamalı")

    monkeypatch.setattr("lai_setup.cli.detect_system", forbidden)
    main(["doctor", "--network", "--pid", str(os.getpid()), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["sampling_only"] and not report["enforcement_verified"]


def test_runtime_source_has_no_external_urls():
    import re

    paths = [
        *(REPO / "src/lai").rglob("*.py"),
        *(REPO / "src/lai_runtime").rglob("*.py"),
    ]
    allowed = [
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main}",
        "http://<loopback>:<port>",
        "http://{u.netloc}",
        "http://{self.address}",
    ]
    found = []
    for path in paths:
        for url in re.findall(r"https?://[^\s\x22\x27)]+", path.read_text()):
            if url not in allowed:
                found.append((str(path.relative_to(REPO)), url))
    assert not found


def test_inherited_connected_socket_is_closed(tmp_path):
    import socket

    a, b = socket.socketpair()
    try:
        code = f"import os,stat; fd={b.fileno()};\ntry: print(stat.S_ISSOCK(os.fstat(fd).st_mode))\nexcept OSError: print(False)"
        result = sandbox.launch(
            [sys.executable, "-c", code],
            pass_fds=(b.fileno(),),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "False"
    finally:
        a.close()
        b.close()


def test_boundary_parent_exit_kills_only_owned_tree(tmp_path):
    import time

    def alive(pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[
                0
            ] not in ("Z", "X")
        except OSError:
            return False

    def descendants(pid):
        found = []
        try:
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        except OSError:
            return found
        for value in children:
            child = int(value)
            found += [child, *descendants(child)]
        return found

    ready = tmp_path / "tree-ready"
    code = f"import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path({str(ready)!r}).write_text('ready'); time.sleep(60)"
    parent_code = f"from lai_runtime.sandbox import launch; import sys; launch([sys.executable,'-c',{code!r}],writable=[{str(tmp_path)!r}])"
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    supervisor = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        end = time.monotonic() + 10
        while (
            not ready.exists() and time.monotonic() < end and supervisor.poll() is None
        ):
            time.sleep(0.03)
        assert ready.exists(), (
            supervisor.communicate(timeout=2)[1]
            if supervisor.poll() is not None
            else "Hazır değil"
        )
        owned = descendants(supervisor.pid)
        assert len(owned) >= 3
        supervisor.terminate()
        supervisor.wait(5)
        end = time.monotonic() + 5
        while any(alive(pid) for pid in owned) and time.monotonic() < end:
            time.sleep(0.03)
        assert not any(alive(pid) for pid in owned)
        assert foreign.poll() is None
    finally:
        if supervisor.poll() is None:
            supervisor.terminate()
            supervisor.wait(5)
        foreign.terminate()
        foreign.wait(5)


def test_private_home_survives_writable_tmp():
    code = "import json,os,tempfile; from pathlib import Path; print(json.dumps([str(Path.home()), tempfile.gettempdir(), Path.home().is_dir()]))"
    result = sandbox.launch(
        [sys.executable, "-c", code],
        writable=["/tmp"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["/run/lai/home", "/run/lai/tmp", True]


def test_desktop_preserves_runtime_root_with_private_home(tmp_path, monkeypatch):
    from lai.ui.__main__ import main

    monkeypatch.delenv("LAI_SANDBOX_ACTIVE", raising=False)
    for key in ["LAI_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "host-home"))
    called = []

    def launch(command, **kwargs):
        called.append(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(sandbox, "launch", launch)
    assert main(["--db", str(tmp_path / "dbdir/data.db")]) == 0
    assert called[0]["env"]["LAI_HOME"] == str(
        tmp_path / "host-home/.local/share/gsb-proje"
    )
