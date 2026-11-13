import pytest

from fakes import FakeRunner
from lai_setup.detect import SystemInfo
from lai_setup.distro import missing_prereqs, strategy_for, suggest_command


def _info(distro_id, like=()):
    return SystemInfo.empty(os="linux", distro_id=distro_id, distro_like=list(like))


@pytest.mark.parametrize("did,like,name", [
    ("arch", [], "pacman"),
    ("cachyos", ["arch"], "pacman"),
    ("endeavouros", ["arch"], "pacman"),
    ("ubuntu", ["debian"], "apt"),
    ("linuxmint", ["ubuntu", "debian"], "apt"),
    ("debian", [], "apt"),
    ("fedora", [], "dnf"),
    ("nobara", ["fedora"], "dnf"),
    ("gentoo", [], "generic"),
])
def test_strategy_selection(did, like, name):
    assert strategy_for(_info(did, like)).name == name


def test_suggest_prefers_pkexec():
    r = FakeRunner(which={"pkexec": "/usr/bin/pkexec", "sudo": "/usr/bin/sudo"})
    assert suggest_command(strategy_for(_info("arch")), ["python-venv"], r) == \
        ["pkexec", "pacman", "-S", "--needed", "python"]
    assert suggest_command(strategy_for(_info("ubuntu", ["debian"])), ["python-venv"], r) == \
        ["pkexec", "apt-get", "install", "-y", "python3-venv"]
    assert suggest_command(strategy_for(_info("fedora")), ["python-venv"], r) == \
        ["pkexec", "dnf", "install", "-y", "python3"]


def test_suggest_falls_back_to_sudo_and_generic_has_none():
    r = FakeRunner(which={"sudo": "/usr/bin/sudo"})
    assert suggest_command(strategy_for(_info("debian")), ["python-venv"], r)[0] == "sudo"
    assert suggest_command(strategy_for(_info("gentoo")), ["python-venv"], r) is None


def test_missing_venv_detected():
    ok = FakeRunner({("python3", "-c"): (0, "")})
    bad = FakeRunner({("python3", "-c"): (1, "No module named 'ensurepip'")})
    assert missing_prereqs("python3", ok) == []
    assert missing_prereqs("python3", bad) == ["python-venv"]
