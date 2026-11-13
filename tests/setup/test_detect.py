from pathlib import Path

import pytest

from fakes import FakeRunner
from lai_setup.detect import detect_system
from lai_setup.paths import resolve_paths

FIX = Path(__file__).parent / "fixtures"
MUTATING = {"start", "stop", "enable", "disable", "restart", "pull", "rm", "create", "cp", "mask"}


def _detect(name, tmp_path, runner=None, tags=None):
    paths = resolve_paths("linux", {"LAI_HOME": str(tmp_path / "lai")}, tmp_path)
    runner = runner or FakeRunner(
        {("/usr/bin/ollama", "--version"): (0, "ollama version is 0.30.1\n"),
         ("systemctl", "is-enabled", "ollama.service"): (0, "enabled\n")},
        which={"ollama": "/usr/bin/ollama", "systemctl": "/usr/bin/systemctl"},
    )

    def http_get(url, timeout):
        assert url.startswith("http://127.0.0.1:11434/")
        if tags is None:
            raise OSError("bağlantı reddedildi")
        return tags

    info = detect_system(runner, fs_root=FIX / f"fsroot_{name}", paths=paths,
                         platform="linux", http_get=http_get)
    return info, runner


@pytest.mark.parametrize("name,distro,like", [
    ("arch", "cachyos", ["arch"]),
    ("ubuntu", "ubuntu", ["debian"]),
    ("fedora", "fedora", []),
])
def test_distro_detection(name, distro, like, tmp_path):
    info, _ = _detect(name, tmp_path)
    assert info.os == "linux"
    assert info.distro_id == distro
    assert info.distro_like == like


def test_memory_and_cpu(tmp_path):
    info, _ = _detect("arch", tmp_path)
    assert info.mem_total_mb == 16303524 // 1024
    assert info.cpu_model.startswith("AMD Ryzen 7")
    assert info.cpu_cores == 2
    assert info.cpu_flags == ["avx", "avx2", "avx512f", "f16c", "fma", "sse4_2"]


def test_gpu_vendors_without_assuming_nvidia(tmp_path):
    arch, _ = _detect("arch", tmp_path)
    assert sorted(g.vendor for g in arch.gpus) == ["amd", "nvidia"]
    ubuntu, _ = _detect("ubuntu", tmp_path)
    assert [g.vendor for g in ubuntu.gpus] == ["intel"]
    fedora, _ = _detect("fedora", tmp_path)
    assert fedora.gpus == []


def test_existing_ollama_detected_read_only(tmp_path):
    info, runner = _detect("arch", tmp_path, tags={"models": [{"name": "qwen3.5-9b:latest"}]})
    o = info.ollama
    assert o.binary == "/usr/bin/ollama"
    assert o.version == "0.30.1"
    assert o.system_service is True
    assert o.listening_11434 is True
    assert o.listening_11435 is False  # yalnız ESTABLISHED, dinleme değil
    assert o.models_11434 == ["qwen3.5-9b:latest"]
    for argv in runner.calls:
        assert not (MUTATING & set(argv)), argv


def test_wildcard_listener_on_11435_reported(tmp_path):
    info, _ = _detect("fedora", tmp_path)
    assert info.ollama.listening_11435 is True
    assert info.ollama.wildcard_11435 is True


def test_ollama_absent(tmp_path):
    info, _ = _detect("ubuntu", tmp_path, runner=FakeRunner())
    assert info.ollama.binary is None
    assert info.ollama.version is None
    assert info.ollama.models_11434 == []


def test_disk_free_measured_from_existing_ancestor(tmp_path):
    info, _ = _detect("arch", tmp_path)
    assert not (tmp_path / "lai").exists()
    assert info.disk_free_mb > 0


def test_existing_install_flag(tmp_path):
    info, _ = _detect("arch", tmp_path)
    assert info.existing_install is False
    (tmp_path / "lai").mkdir()
    (tmp_path / "lai" / "install-state.json").write_text("{}")
    info, _ = _detect("arch", tmp_path)
    assert info.existing_install is True


def test_to_dict_is_json_serialisable(tmp_path):
    import json
    info, _ = _detect("arch", tmp_path)
    json.dumps(info.to_dict())
