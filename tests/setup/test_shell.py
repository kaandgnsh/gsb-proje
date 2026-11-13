import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SH = ["install.sh", "doctor.sh", "repair.sh", "uninstall.sh"]
PS = ["install.ps1", "doctor.ps1", "repair.ps1", "uninstall.ps1"]
BASH = shutil.which("bash")


def _run(script, *args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([BASH, str(REPO / script), *args], capture_output=True, text=True,
                          env=e, timeout=120)


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck yok")
def test_shellcheck_clean():
    r = subprocess.run(["shellcheck", "-x", *[str(REPO / s) for s in SH + ["_common.sh"]]],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stdout + r.stderr


def test_scripts_executable():
    for s in SH:
        assert os.access(REPO / s, os.X_OK), s


def test_install_dry_run_with_turkish_spaced_home(tmp_path):
    home = tmp_path / "a b" / "İş"
    r = _run("install.sh", "--dry-run", "--lai-home", str(home))
    assert r.returncode == 0, r.stderr
    assert "PLANLANDI" in r.stdout
    assert not home.exists()


def test_doctor_reports_missing_install(tmp_path):
    r = _run("doctor.sh", "--lai-home", str(tmp_path / "yok"))
    assert r.returncode == 1
    assert "HATA" in r.stdout


def test_missing_python_gives_guidance(tmp_path):
    r = _run("install.sh", env={"PATH": str(tmp_path)})
    assert r.returncode == 127
    assert "Python 3.11" in r.stderr


def test_refuses_root():
    text = (REPO / "_common.sh").read_text(encoding="utf-8")
    assert 'EUID' in text and "root" in text


@pytest.mark.parametrize("ps", PS)
def test_powershell_wrappers_forward_to_engine(ps):
    text = (REPO / ps).read_text(encoding="utf-8-sig")
    cmd = ps.removesuffix(".ps1")
    assert ". \"$PSScriptRoot\\_common.ps1\"" in text
    assert re.search(rf"Invoke-LaiSetup\s+-Command\s+'{cmd}'\s+-Arguments\s+\$args", text)


def test_powershell_common_finds_python_and_sets_path():
    text = (REPO / "_common.ps1").read_text(encoding="utf-8-sig")
    assert "py" in text and "-3" in text
    assert "PYTHONPATH" in text
    assert "lai_setup" in text
    assert "exit $LASTEXITCODE" in text


@pytest.mark.parametrize("ps", PS + ["_common.ps1"])
def test_powershell_files_have_utf8_bom_for_windows_powershell_5(ps):
    assert (REPO / ps).read_bytes().startswith(b"\xef\xbb\xbf")
