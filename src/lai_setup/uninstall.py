"""Kaldırma: kullanıcı verisini varsayılan olarak korur."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .m1_steps import MARKER
from .paths import AppPaths

PURGE_WORD = "SİL"


def _size(p: Path) -> int:
    if p.is_file() or p.is_symlink():
        return p.lstat().st_size
    if not p.exists():
        return 0
    return sum(f.lstat().st_size for f in p.rglob("*") if f.is_file() or f.is_symlink())


@dataclass
class UninstallPlan:
    remove: list[Path] = field(default_factory=list)
    keep: list[Path] = field(default_factory=list)

    def total_bytes(self) -> int:
        return sum(_size(p) for p in self.remove)


def is_managed_root(paths: AppPaths) -> bool:
    return (paths.root / MARKER).is_file()


def plan_uninstall(paths: AppPaths, purge_data: bool) -> UninstallPlan:
    plan = UninstallPlan()
    targets = [paths.app, paths.venv, paths.runtime, paths.logs, paths.state_file,
               paths.config_dir]
    for t in targets:
        if t.exists() or t.is_symlink():
            plan.remove.append(t)
    for d in paths.user_data_dirs():
        if d.exists():
            (plan.remove if purge_data else plan.keep).append(d)
    return plan


def execute(plan: UninstallPlan, paths: AppPaths, purge_data: bool) -> list[str]:
    errors = []
    allowed = [paths.root, paths.config_dir]
    for p in plan.remove:
        if not any(p == a or p.is_relative_to(a) for a in allowed):
            errors.append(f"izin verilen dizinlerin dışında, atlandı: {p}")
            continue
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError as e:
            errors.append(f"{p}: {e}")
    leftovers = [c for c in paths.root.iterdir()] if paths.root.exists() else []
    if purge_data or all(c.name in (MARKER,) for c in leftovers):
        marker = paths.root / MARKER
        if marker.exists():
            marker.unlink()
        try:
            paths.root.rmdir()
        except OSError:
            pass  # içinde kullanıcı verisi ya da yabancı dosya var: dokunma
    return errors
