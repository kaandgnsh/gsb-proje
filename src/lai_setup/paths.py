"""Platforma göre uygulama dizinleri."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

APP_ID = "local-assessment-ai"
WIN_APP_ID = "LocalAssessmentAI"


@dataclass(frozen=True)
class AppPaths:
    root: Path
    app: Path
    venv: Path
    data: Path
    runtime: Path
    models: Path
    config_dir: Path
    config_file: Path
    reports: Path
    logs: Path
    state_file: Path

    def all_dirs(self) -> list[Path]:
        return [self.root, self.app, self.data, self.runtime, self.models,
                self.config_dir, self.reports, self.logs]

    def user_data_dirs(self) -> list[Path]:
        """Kullanıcı verisi: kaldırmada izinsiz silinmez."""
        return [self.data, self.reports]


def _build(root: Path, config_dir: Path) -> AppPaths:
    runtime = root / "runtime"
    return AppPaths(
        root=root, app=root / "app", venv=root / "venv", data=root / "data",
        runtime=runtime, models=runtime / "models",
        config_dir=config_dir, config_file=config_dir / "config.toml",
        reports=root / "reports", logs=root / "logs",
        state_file=root / "install-state.json",
    )


def resolve_paths(platform: str, env: Mapping[str, str], home: Path) -> AppPaths:
    if env.get("LAI_HOME"):
        root = Path(env["LAI_HOME"])
        return _build(root, root / "config")
    if platform == "windows":
        local = Path(env.get("LOCALAPPDATA") or PureWindowsPath(home, "AppData", "Local"))
        roaming = Path(env.get("APPDATA") or PureWindowsPath(home, "AppData", "Roaming"))
        return _build(local / WIN_APP_ID, roaming / WIN_APP_ID)
    data_home = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    config_home = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return _build(data_home / APP_ID, config_home / APP_ID)
