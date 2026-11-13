"""Dış komut çalıştırma soyutlaması (testlerde sahtesi kullanılır)."""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol


class CommandRunner(Protocol):
    def run(self, argv: list[str], timeout: float = 10,
            env: Mapping[str, str] | None = None,
            cwd: Path | None = None) -> subprocess.CompletedProcess: ...

    def which(self, name: str) -> str | None: ...


class SystemRunner:
    """Gerçek komut çalıştırıcı. Kabuk kullanmaz; argv liste olarak geçer."""

    def run(self, argv, timeout: float = 10, env=None, cwd=None):
        try:
            return subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                                  timeout=timeout, env=dict(env) if env is not None else None,
                                  cwd=cwd, check=False, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: bulunamadı")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, "", f"{argv[0]}: zaman aşımı")

    def which(self, name: str) -> str | None:
        return shutil.which(name)
