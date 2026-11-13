import socket

import pytest

from lai_runtime.config import RuntimeConfig, load_runtime_config
from lai_runtime.env import build_env
from lai_runtime.ports import port_in_use
from lai_setup.paths import resolve_paths


@pytest.fixture
def paths(tmp_path):
    return resolve_paths("linux", {"LAI_HOME": str(tmp_path / "lai")}, tmp_path)


def test_defaults():
    c = RuntimeConfig()
    assert (c.host, c.port, c.model, c.accel) == ("127.0.0.1", 11435, "qwen3.5:4b", "auto")


def test_load_from_config_file(paths):
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text('[ollama]\nport = 12000\nmodel = "x:1b"\naccel = "cpu"\n',
                                 encoding="utf-8")
    c = load_runtime_config(paths)
    assert (c.port, c.model, c.accel) == (12000, "x:1b", "cpu")


def test_missing_config_gives_defaults(paths):
    assert load_runtime_config(paths) == RuntimeConfig()


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::", "example.com"])
def test_non_loopback_host_rejected(paths, host):
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text(f'[ollama]\nhost = "{host}"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_runtime_config(paths)


def test_system_port_rejected(paths):
    paths.config_dir.mkdir(parents=True)
    paths.config_file.write_text('[ollama]\nport = 11434\n', encoding="utf-8")
    with pytest.raises(ValueError, match="11434"):
        load_runtime_config(paths)


def test_env_is_clean_and_isolated(paths):
    parent = {"PATH": "/usr/bin", "OLLAMA_HOST": "0.0.0.0:11434", "OLLAMA_MODELS": "/var/lib/ollama",
              "HTTPS_PROXY": "http://proxy:3128", "http_proxy": "x", "SECRET_TOKEN": "s",
              "LD_LIBRARY_PATH": "/opt/cuda/lib64", "LANG": "tr_TR.UTF-8"}
    env = build_env(RuntimeConfig(), paths, parent, "linux", owner="abc")
    assert env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert env["OLLAMA_MODELS"] == str(paths.models)
    assert env["OLLAMA_NO_CLOUD"] == "1"
    assert env["OLLAMA_ORIGINS"] == ""
    assert env["LAI_RUNTIME_OWNER"] == "abc"
    assert env["HOME"] == str(paths.runtime / "home")
    assert env["PATH"] == "/usr/bin" and env["LD_LIBRARY_PATH"] == "/opt/cuda/lib64"
    for bad in ("HTTPS_PROXY", "http_proxy", "SECRET_TOKEN"):
        assert bad not in env
    assert not any(k.startswith("OLLAMA_") and k not in {
        "OLLAMA_HOST", "OLLAMA_MODELS", "OLLAMA_NO_CLOUD", "OLLAMA_ORIGINS", "OLLAMA_KEEP_ALIVE"}
        for k in env)


def test_cpu_mode_hides_gpus(paths):
    env = build_env(RuntimeConfig(accel="cpu"), paths, {"CUDA_VISIBLE_DEVICES": "0"}, "linux", owner="o")
    for k in ("CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES",
              "GGML_VK_VISIBLE_DEVICES"):
        assert env[k] == ""


def test_windows_env_uses_userprofile(paths):
    env = build_env(RuntimeConfig(), paths, {"PATH": r"C:\x", "SystemRoot": r"C:\Windows"},
                    "windows", owner="o")
    assert env["USERPROFILE"] == str(paths.runtime / "home")
    assert env["SystemRoot"] == r"C:\Windows"


def test_port_in_use_detects_real_listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        assert port_in_use("127.0.0.1", port) is True
    finally:
        s.close()
    assert port_in_use("127.0.0.1", port) is False
