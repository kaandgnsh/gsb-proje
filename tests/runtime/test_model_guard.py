"""Yapay zekâ yalnız doğrulanmış ağ yalıtımında çalışır; yalıtımsız yedek yol yoktur.

Bu testler gerçek kapıyı kullanır (`real_guard`): diğer teknik testlerdeki sahte
yalıtım yaması burada uygulanmaz.
"""
import json
import shutil
import socket
import threading

import pytest

from conftest import free_port
from lai_runtime import guard
from lai_runtime.client import ModelIsolationError, OllamaClient
from lai_runtime.config import RuntimeConfig
from lai_runtime.process import RuntimeManager
from lai_setup.paths import resolve_paths

pytestmark = pytest.mark.real_guard


@pytest.fixture(autouse=True)
def _fresh_guard(monkeypatch):
    monkeypatch.delenv("LAI_SANDBOX_ACTIVE", raising=False)
    guard._verified.cache_clear()
    yield
    guard._verified.cache_clear()


class Listener:
    """Bağlantı sayan yerel dinleyici: istemci reddederse hiç bağlantı gelmemeli."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.connections = 0
        self.stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            self.connections += 1
            conn.close()

    def close(self):
        self.stop = True
        self.thread.join(2)
        self.sock.close()


@pytest.fixture
def listener():
    lst = Listener()
    yield lst
    lst.close()


def test_outside_isolation_model_calls_are_refused_before_any_connection(listener):
    c = OllamaClient(f"http://127.0.0.1:{listener.port}")
    for call in (
        lambda: c.generate("m", "gizli sınav verisi"),
        lambda: c.show("m"),
        lambda: c.list_models(),
        lambda: c.ps(),
        lambda: c.pull("m"),
        lambda: c.create("m", files={}),
        lambda: c.has_blob("sha256:" + "0" * 64),
    ):
        with pytest.raises(ModelIsolationError):
            call()
    assert listener.connections == 0


def test_environment_flag_alone_is_not_proof(monkeypatch, listener):
    monkeypatch.setenv("LAI_SANDBOX_ACTIVE", "1")
    guard._verified.cache_clear()
    assert guard.isolated() is False
    with pytest.raises(ModelIsolationError):
        OllamaClient(f"http://127.0.0.1:{listener.port}").generate("m", "veri")
    assert listener.connections == 0


def test_download_only_client_may_pull_but_never_infer(fake_server):
    base, _, _ = fake_server
    c = OllamaClient(base, download_only=True)
    assert c.version()
    assert c.pull("qwen3.5:4b")["status"] == "success"
    for call in (lambda: c.generate("m", "veri"), lambda: c.show("m"),
                 lambda: c.list_models()):
        with pytest.raises(ModelIsolationError):
            call()


def test_runtime_refuses_to_start_model_outside_isolation(tmp_path, fake_binary):
    paths = resolve_paths("linux", {"LAI_HOME": str(tmp_path / "lai")}, tmp_path)
    mgr = RuntimeManager(RuntimeConfig(port=free_port()), paths,
                         binary=str(fake_binary), base_env={"PATH": "/usr/bin:/bin"})
    with pytest.raises(ModelIsolationError):
        mgr.start(timeout=5)
    assert not mgr.record_file.exists() and mgr._proc is None


def test_download_runtime_starts_with_download_only_client(tmp_path, fake_binary):
    paths = resolve_paths("linux", {"LAI_HOME": str(tmp_path / "lai")}, tmp_path)
    mgr = RuntimeManager(RuntimeConfig(port=free_port()), paths, binary=str(fake_binary),
                         base_env={"PATH": "/usr/bin:/bin"}, download_only=True)
    try:
        assert mgr.start(timeout=10).running
        client = mgr.client()
        assert client.download_only
        with pytest.raises(ModelIsolationError):
            client.generate(mgr.cfg.model, "veri")
    finally:
        mgr.stop()
    assert mgr.record_file.name != "ollama-process.json"


@pytest.mark.skipif(not shutil.which("bwrap"), reason="bubblewrap yok")
def test_setup_model_operation_runs_inside_verified_isolation(tmp_path, fake_binary):
    """Gerçek sınır: kurulumun doğrulama işlemi modeli yalnız loopback ad alanında çalıştırır."""
    from lai_setup.isolated import run_isolated

    paths = resolve_paths("linux", {"LAI_HOME": str(tmp_path / "lai")}, tmp_path)
    paths.config_dir.mkdir(parents=True)
    binary = paths.root / "bin" / "ollama"  # sınır yalnız uygulama kökünü bağlar
    binary.parent.mkdir()
    shutil.copy2(fake_binary, binary)
    paths.config_file.write_text(
        f'[ollama]\nmodel = "sentetik:latest"\nbinary = {json.dumps(str(binary))}\n',
        encoding="utf-8")
    (paths.models / "fake-models.json").parent.mkdir(parents=True)
    (paths.models / "fake-models.json").write_text(
        json.dumps({"sentetik:latest": {"digest": "sha256:" + "1" * 64}}))
    result = run_isolated("verify", paths, model="sentetik:latest")
    assert result["ok"], result
    assert not guard.isolated()  # dış süreç yalıtımsız; model yalnız içeride çalıştı


def test_learning_cli_refuses_model_outside_isolation(tmp_path, capsys, listener):
    from lai.learning.__main__ import main
    from lai.store.store import Store

    db = tmp_path / "lai.db"
    s = Store.open(db)
    s.inventory.create_inventory("Sentetik")
    s.close()
    rc = main(["discover", "--db", str(db), "--inventory", "Sentetik",
               "--port", str(listener.port)])
    assert rc == 2
    assert "yalıtım" in capsys.readouterr().err
    assert listener.connections == 0
