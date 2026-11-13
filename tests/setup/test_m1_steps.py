import hashlib
import os
import sys
import tomllib

import pytest

from fakes import FakeRunner
from lai_setup.detect import SystemInfo
from lai_setup.m1_steps import (CreateVenv, EnsureDirectories, InstallPythonDeps, WriteConfig,
                                m1_steps, venv_python)
from lai_setup.paths import resolve_paths
from lai_setup.runner import SystemRunner
from lai_setup.state import InstallState
from lai_setup.steps import Context, StepStatus, run_steps


def _ctx(tmp_path, runner=None, **kw):
    paths = resolve_paths("linux", {"LAI_HOME": str(tmp_path / "lai")}, tmp_path)
    req = tmp_path / "req.txt"
    if not req.exists():
        req.write_text("openpyxl==3.1.5\n")
    kw.setdefault("requirements_file", req)
    return Context(paths=paths, info=SystemInfo.empty(os="linux"), runner=runner or FakeRunner(),
                   state=InstallState.load(paths.state_file), python=sys.executable, **kw)


def test_directories_created_private(tmp_path):
    ctx = _ctx(tmp_path)
    assert run_steps([EnsureDirectories()], ctx, "install")[0].status == StepStatus.APPLIED
    for d in ctx.paths.all_dirs():
        assert d.is_dir()
    for d in ctx.paths.user_data_dirs():
        assert oct(d.stat().st_mode & 0o777) == "0o700"
    assert (ctx.paths.root / ".lai-root").is_file()


def test_default_config_contents(tmp_path):
    ctx = _ctx(tmp_path)
    run_steps([EnsureDirectories(), WriteConfig()], ctx, "install")
    cfg = tomllib.loads(ctx.paths.config_file.read_text(encoding="utf-8"))
    assert cfg["ollama"] == {"host": "127.0.0.1", "port": 11435, "model": "qwen3.5:4b",
                             "no_cloud": True}
    assert cfg["app"] == {"language": "tr", "network": "offline", "telemetry": False,
                          "update_check": False}


def test_user_edited_config_preserved(tmp_path):
    ctx = _ctx(tmp_path)
    run_steps([EnsureDirectories(), WriteConfig()], ctx, "install")
    edited = ctx.paths.config_file.read_text(encoding="utf-8").replace("qwen3.5:4b", "benim-modelim")
    ctx.paths.config_file.write_text(edited, encoding="utf-8")
    mtime = ctx.paths.config_file.stat().st_mtime_ns
    res = run_steps([EnsureDirectories(), WriteConfig()], _ctx(tmp_path), "install")
    assert res[1].status == StepStatus.SKIPPED
    assert ctx.paths.config_file.stat().st_mtime_ns == mtime
    assert "benim-modelim" in ctx.paths.config_file.read_text(encoding="utf-8")


def test_corrupt_config_backed_up_on_repair(tmp_path):
    ctx = _ctx(tmp_path)
    run_steps([EnsureDirectories(), WriteConfig()], ctx, "install")
    ctx.paths.config_file.write_text("[ollama\nport = ", encoding="utf-8")
    res = run_steps([EnsureDirectories(), WriteConfig()], _ctx(tmp_path), "repair")
    assert res[1].status == StepStatus.APPLIED
    backups = list(ctx.paths.config_dir.glob("config.toml.bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "[ollama\nport = "
    tomllib.loads(ctx.paths.config_file.read_text(encoding="utf-8"))


def _pip_runner(tmp_path):
    py = str(venv_python(tmp_path / "lai" / "venv", "linux"))
    return FakeRunner({(py, "-c"): (0, ""), (py, "-m", "pip", "install"): (0, ""),
                       (py, "-m", "pip", "check"): (0, "No broken requirements found.")}), py


def test_pydeps_online_argv(tmp_path):
    runner, py = _pip_runner(tmp_path)
    ctx = _ctx(tmp_path, runner, mode="online")
    res = run_steps([InstallPythonDeps()], ctx, "install")
    assert res[0].status == StepStatus.APPLIED
    install = [c for c in runner.calls if c[1:4] == ["-m", "pip", "install"]]
    assert install == [[py, "-m", "pip", "install", "--disable-pip-version-check",
                        "--no-input", "-r", str(ctx.requirements_file)]]
    sha = hashlib.sha256(ctx.requirements_file.read_bytes()).hexdigest()
    assert ctx.state.pins["requirements_sha256"] == sha


def test_pydeps_offline_argv_uses_no_index(tmp_path):
    runner, py = _pip_runner(tmp_path)
    off = tmp_path / "paket"
    ctx = _ctx(tmp_path, runner, mode="offline", offline_dir=off)
    run_steps([InstallPythonDeps()], ctx, "install")
    install = [c for c in runner.calls if c[1:4] == ["-m", "pip", "install"]][0]
    assert install[4:] == ["--disable-pip-version-check", "--no-input", "--no-index",
                           "--find-links", str(off / "wheels"), "-r", str(ctx.requirements_file)]


def test_pydeps_without_source_fails_and_never_calls_pip(tmp_path):
    runner, _ = _pip_runner(tmp_path)
    res = run_steps([InstallPythonDeps()], _ctx(tmp_path, runner, mode="none"), "install")
    assert res[0].status == StepStatus.FAILED
    assert "çevrimiçi" in res[0].message
    assert not any("pip" in c for c in runner.calls)


def test_pydeps_skipped_when_requirements_unchanged(tmp_path):
    runner, _ = _pip_runner(tmp_path)
    ctx = _ctx(tmp_path, runner, mode="online")
    run_steps([InstallPythonDeps()], ctx, "install")
    ctx2 = _ctx(tmp_path, runner, mode="online")
    ctx2.state = ctx.state
    assert run_steps([InstallPythonDeps()], ctx2, "install")[0].status == StepStatus.SKIPPED
    ctx2.requirements_file.write_text("openpyxl==3.1.5\nreportlab==5.1.0\n")
    assert run_steps([InstallPythonDeps()], ctx2, "install")[0].status == StepStatus.APPLIED


def test_offline_pip_ignores_user_pip_config(tmp_path):
    runner, _ = _pip_runner(tmp_path)
    seen = {}
    orig = runner.run

    def spy(argv, timeout=10, env=None, cwd=None):
        if "install" in argv:
            seen.update(env or {})
        return orig(argv, timeout, env, cwd)

    runner.run = spy
    run_steps([InstallPythonDeps()], _ctx(tmp_path, runner, mode="offline",
                                          offline_dir=tmp_path), "install")
    assert seen["PIP_CONFIG_FILE"] == os.devnull
    assert seen["PIP_NO_INDEX"] == "1"


@pytest.mark.slow
def test_real_venv_created_once(tmp_path):
    ctx = _ctx(tmp_path, SystemRunner())
    r1 = run_steps([EnsureDirectories(), CreateVenv()], ctx, "install")
    assert r1[1].status == StepStatus.APPLIED, r1[1].message
    assert venv_python(ctx.paths.venv, "linux").exists()
    r2 = run_steps([EnsureDirectories(), CreateVenv()], _ctx(tmp_path, SystemRunner()), "install")
    assert r2[1].status == StepStatus.SKIPPED


def test_step_order():
    assert [s.id for s in m1_steps()] == ["dirs", "config", "venv", "pydeps"]
