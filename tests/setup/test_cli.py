import hashlib
import json
from pathlib import Path

import pytest

from fakes import FakeRunner
from lai_setup.cli import main
from lai_setup.runner import SystemRunner

FIX = Path(__file__).parent / "fixtures" / "fsroot_arch"


def _no_http(url, timeout):
    raise OSError("test: ağ yok")


def _tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "d")
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "ev dizini İ"
    return {"LAI_HOME": str(home), "HOME": str(tmp_path)}


@pytest.fixture
def offline(tmp_path):
    d = tmp_path / "paket"
    (d / "wheels").mkdir(parents=True)
    req = tmp_path / "bos-req.txt"
    req.write_text("# boş\n")
    return d, req


def run(argv, env, runner=None, tty=False, answers=()):
    it = iter(answers)
    return main(argv, env=env, runner=runner or SystemRunner(), isatty=tty,
                input_fn=lambda prompt="": next(it), fs_root=FIX, http_get=_no_http)


def test_dry_run_writes_nothing(env, tmp_path):
    before = _tree(tmp_path)
    assert run(["install", "--dry-run"], env) == 0
    assert _tree(tmp_path) == before


def test_no_source_without_tty_exits_2_and_downloads_nothing(env, capsys):
    r = FakeRunner(which={})
    assert run(["install"], env, runner=r) == 2
    assert not any("pip" in c for c in r.calls)
    assert "--online" in capsys.readouterr().err


def test_interactive_cancel_exits_3(env):
    assert run(["install"], env, runner=FakeRunner(), tty=True, answers=["3"]) == 3


@pytest.mark.slow
def test_install_twice_is_idempotent(env, offline):
    d, req = offline
    args = ["install", "--offline", str(d), "--requirements", str(req)]
    assert run(args, env) == 0
    home = Path(env["LAI_HOME"])
    cfg = home / "config" / "config.toml"
    mtime = cfg.stat().st_mtime_ns
    state1 = json.loads((home / "install-state.json").read_text(encoding="utf-8"))
    assert {k: v["status"] for k, v in state1["steps"].items()} == {
        "dirs": "APPLIED", "config": "APPLIED", "venv": "APPLIED", "pydeps": "APPLIED"}
    assert run(args, env) == 0
    state2 = json.loads((home / "install-state.json").read_text(encoding="utf-8"))
    assert {v["status"] for v in state2["steps"].values()} == {"SKIPPED"}
    assert cfg.stat().st_mtime_ns == mtime
    reports = sorted((home / "logs").glob("install-*.json"))
    assert len(reports) == 2
    assert json.loads(reports[-1].read_text(encoding="utf-8"))["ok"] is True


@pytest.mark.slow
def test_doctor_is_read_only_and_reports(env, offline, capsys):
    d, req = offline
    run(["install", "--offline", str(d), "--requirements", str(req)], env)
    home = Path(env["LAI_HOME"])
    before = _tree(home)
    capsys.readouterr()
    assert run(["doctor", "--requirements", str(req)], env) == 0
    assert _tree(home) == before
    out = capsys.readouterr().out
    assert "Sağlık denetimi" in out and "SAĞLAM" in out


def test_doctor_on_missing_install_exits_1(env):
    assert run(["doctor"], env) == 1


@pytest.mark.slow
def test_uninstall_keeps_user_data(env, offline):
    d, req = offline
    run(["install", "--offline", str(d), "--requirements", str(req)], env)
    home = Path(env["LAI_HOME"])
    (home / "data" / "x.db").write_bytes(b"veri")
    (home / "reports" / "r.pdf").write_bytes(b"rapor")
    assert run(["uninstall", "--yes"], env) == 0
    assert (home / "data" / "x.db").read_bytes() == b"veri"
    assert (home / "reports" / "r.pdf").read_bytes() == b"rapor"
    for gone in ("venv", "app", "runtime", "config", "logs", "install-state.json"):
        assert not (home / gone).exists(), gone


@pytest.mark.slow
def test_purge_requires_explicit_confirmation(env, offline):
    d, req = offline
    run(["install", "--offline", str(d), "--requirements", str(req)], env)
    home = Path(env["LAI_HOME"])
    (home / "data" / "x.db").write_bytes(b"veri")
    assert run(["uninstall", "--purge-data", "--yes"], env) == 2
    assert (home / "data" / "x.db").exists()
    assert run(["uninstall", "--purge-data", "--yes"], dict(env, LAI_CONFIRM_PURGE="SİL")) == 0
    assert not home.exists()


@pytest.mark.slow
def test_interactive_purge_needs_typed_word(env, offline):
    d, req = offline
    run(["install", "--offline", str(d), "--requirements", str(req)], env)
    home = Path(env["LAI_HOME"])
    (home / "data" / "x.db").write_bytes(b"veri")
    assert run(["uninstall", "--purge-data"], env, tty=True, answers=["e", "evet"]) == 3
    assert (home / "data" / "x.db").exists()


def test_uninstall_refuses_without_marker(tmp_path):
    victim = tmp_path / "kullanici"
    (victim / "app").mkdir(parents=True)
    (victim / "app" / "onemli.txt").write_text("dokunma")
    env = {"LAI_HOME": str(victim)}
    assert run(["uninstall", "--yes"], env) == 1
    assert (victim / "app" / "onemli.txt").read_text() == "dokunma"


def test_uninstall_without_yes_and_no_tty_exits_2(env):
    Path(env["LAI_HOME"]).mkdir(parents=True)
    (Path(env["LAI_HOME"]) / ".lai-root").write_text("")
    assert run(["uninstall"], env) == 2


def test_detect_json(env, capsys):
    assert run(["detect", "--json"], env) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["distro_id"] == "cachyos"
    assert data["ollama"]["listening_11434"] is True
