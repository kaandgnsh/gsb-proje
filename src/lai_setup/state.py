"""Kurulum durum dosyası (atomik JSON)."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

STATE_VERSION = 1


@dataclass
class InstallState:
    path: Path
    steps: dict[str, dict] = field(default_factory=dict)
    pins: dict[str, str] = field(default_factory=dict)
    corrupt: bool = False

    @classmethod
    def load(cls, path: Path) -> InstallState:
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("kök nesne sözlük değil")
            return cls(path, dict(data.get("steps", {})), dict(data.get("pins", {})))
        except (ValueError, OSError):
            return cls(path, corrupt=True)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.corrupt and self.path.exists():
            self.path.rename(self.path.with_name(
                f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"))
            self.corrupt = False
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {"version": STATE_VERSION, "steps": self.steps, "pins": self.pins}
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
