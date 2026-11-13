import hashlib
import json
from pathlib import Path

import pytest

from fakes import FakeRunner
from lai_setup.cli import main
from lai_setup.runner import SystemRunner
from conftest import free_port
from test_models import make_store

FIX = Path(__file__).resolve().parents[1] / "setup" / "fixtures" / "fsroot_arch"


def _no_http(url, timeout):
    raise OSError("ağ yok")


@pytest.fixture
def home(tmp_path, fake_binary):
    """Fake ikiliyi kullanan, rastgele portlu hazır LAI_HOME (M1 adımları önceden kurulu)."""
    h = tmp_path / "lai"
    req = tmp_path / "req.txt"
    req.write_text("")
    (tmp_path / "bos" / "wheels").mkdir(parents=True)
    rc = run(["install", "--skip-ai", "--offline", str(tmp_path / "bos"), "--requirements", str(req)], h)
    assert rc == 0
    cfg = h / "config" / "config.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        "port = 11435", f'port = {free_port()}\nbinary = "{fake_binary}"'), encoding="utf-8")
    return h, req


def run(argv, home, tty=False, answers=()):
    it = iter(answers)
    return main(argv, env={"LAI_HOME": str(home), "HOME": str(home.parent),
                           "PATH": "/usr/bin:/bin"},
                runner=SystemRunner(), isatty=tty, input_fn=lambda p="": next(it),
                fs_root=FIX, http_get=_no_http)


def _requests(home: Path) -> str:
    f = home / "runtime" / "models" / "requests.log"
    return f.read_text() if f.exists() else ""


def _state(home):
    return json.loads((home / "install-state.json").read_text(encoding="utf-8"))


def test_pull_without_permission_downloads_nothing(home, capsys):
    h, _ = home
    assert run(["model", "pull"], h) == 2
    assert "/api/pull" not in _requests(h)
    assert "--online" in capsys.readouterr().err


def test_pull_interactive_decline(home):
    h, _ = home
    assert run(["model", "pull"], h, tty=True, answers=["h"]) == 3
    assert "/api/pull" not in _requests(h)


def test_pull_with_online_flag(home, capsys):
    h, _ = home
    assert run(["model", "pull", "--online"], h) == 0
    assert "POST /api/pull" in _requests(h)
    pins = _state(h)["pins"]
    assert pins["model"] == "qwen3.5:4b" and pins["model_digest"].startswith("sha256:")
    assert not (h / "runtime" / "ollama-process.json").exists()  # geçici süreç kapatıldı


def test_import_from_other_store_read_only(home, tmp_path):
    h, _ = home
    src = tmp_path / "baska-ollama"
    make_store(src, "v2", name="sansursuz")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}
    assert run(["model", "import", "--from", str(src), "--name", "sansursuz",
                "--as", "qwen3.5:4b"], h) == 0
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob("*") if p.is_file()}
    assert before == after
    assert run(["model", "verify"], h) == 0


def test_verify_missing_model(home, capsys):
    h, _ = home
    assert run(["model", "verify"], h) == 1
    assert "bulunamadı" in capsys.readouterr().err


def test_doctor_does_not_start_runtime(home):
    h, _ = home
    run(["doctor"], h)
    assert not (h / "runtime" / "ollama-process.json").exists()
    assert _requests(h) == ""


def test_full_offline_install_with_model_bundle_is_idempotent(home, tmp_path):
    h, req = home
    bundle = tmp_path / "paket"
    (bundle / "wheels").mkdir(parents=True)
    make_store(bundle / "models", "v1", name="qwen3.5", tag="4b")
    assert run(["install", "--offline", str(bundle), "--requirements", str(req)], h) == 0
    st = _state(h)
    assert {k: v["status"] for k, v in st["steps"].items()}["model"] == "APPLIED"
    assert st["pins"]["ollama_version"] == "0.0.0-fake"
    assert st["pins"]["model_digest"].startswith("sha256:")
    assert run(["install", "--offline", str(bundle), "--requirements", str(req)], h) == 0
    st2 = _state(h)
    assert {v["status"] for v in st2["steps"].values()} == {"SKIPPED"}


def test_offline_bundle_without_model_fails_clearly(home, tmp_path, capsys):
    h, req = home
    bundle = tmp_path / "paket"
    (bundle / "wheels").mkdir(parents=True)
    assert run(["install", "--offline", str(bundle), "--requirements", str(req)], h) == 1
    assert "qwen3.5:4b" in capsys.readouterr().out


def test_runtime_status_and_stop_when_nothing_running(home, capsys):
    h, _ = home
    assert run(["runtime", "status"], h) == 0
    assert "çalışmıyor" in capsys.readouterr().out
    assert run(["runtime", "stop"], h) == 0


def test_pins_recorded_even_when_step_skipped(tmp_path):
    from lai_setup.detect import SystemInfo
    from lai_setup.paths import resolve_paths
    from lai_setup.state import InstallState
    from lai_setup.steps import Context, Step, run_steps

    class Done(Step):
        id, title = "x", "x"
        def check(self, ctx): return True
        def apply(self, ctx): raise AssertionError
        def verify(self, ctx): return True, ""
        def record_pins(self, ctx): ctx.state.pins["x"] = "1"

    p = resolve_paths("linux", {"LAI_HOME": str(tmp_path)}, tmp_path)
    ctx = Context(paths=p, info=SystemInfo.empty(os="linux"), runner=FakeRunner(),
                  state=InstallState.load(p.state_file))
    run_steps([Done()], ctx, "install")
    assert ctx.state.pins == {"x": "1"}
    ctx.state.pins.clear()
    run_steps([Done()], ctx, "doctor")
    assert ctx.state.pins == {}
