"""Dağıtıma göre sistem paketi stratejileri.

Bu modül komut **önerir**; kurulumu yalnız kullanıcı açıkça onaylarsa CLI çalıştırır.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .detect import SystemInfo
from .runner import CommandRunner

# python3 -c ile kontrol: venv + ensurepip (Debian'da python3-venv ayrı paket)
_VENV_PROBE = "import venv, ensurepip"


@dataclass(frozen=True)
class PackageStrategy:
    name: str
    manager: str | None
    install_cmd: list[str] = field(default_factory=list)
    packages: dict[str, str] = field(default_factory=dict)  # mantıksal ad -> paket


STRATEGIES = {
    "pacman": PackageStrategy("pacman", "pacman", ["pacman", "-S", "--needed"],
                              {"python": "python", "python-venv": "python"}),
    "apt": PackageStrategy("apt", "apt-get", ["apt-get", "install", "-y"],
                           {"python": "python3", "python-venv": "python3-venv"}),
    "dnf": PackageStrategy("dnf", "dnf", ["dnf", "install", "-y"],
                           {"python": "python3", "python-venv": "python3"}),
    "generic": PackageStrategy("generic", None),
}

_FAMILY = {"arch": "pacman", "debian": "apt", "ubuntu": "apt", "fedora": "dnf",
           "rhel": "dnf", "centos": "dnf"}


def strategy_for(info: SystemInfo) -> PackageStrategy:
    for ident in [info.distro_id, *info.distro_like]:
        if ident in _FAMILY:
            return STRATEGIES[_FAMILY[ident]]
    return STRATEGIES["generic"]


def missing_prereqs(python: str, runner: CommandRunner) -> list[str]:
    r = runner.run([python, "-c", _VENV_PROBE], timeout=20)
    return [] if r.returncode == 0 else ["python-venv"]


def suggest_command(strategy: PackageStrategy, logical: list[str],
                    runner: CommandRunner) -> list[str] | None:
    if strategy.manager is None or not logical:
        return None
    elevate = "pkexec" if runner.which("pkexec") else "sudo" if runner.which("sudo") else None
    if elevate is None:
        return None
    pkgs = list(dict.fromkeys(strategy.packages[n] for n in logical))
    return [elevate, *strategy.install_cmd, *pkgs]
