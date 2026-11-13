"""Kurulum/doktor raporu: JSON + okunur Türkçe metin."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .detect import SystemInfo
from .steps import STATUS_TR, StepResult, StepStatus

ACTION_TR = {"install": "Kurulum", "repair": "Onarım", "doctor": "Sağlık denetimi",
             "uninstall": "Kaldırma", "detect": "Ortam tespiti"}


@dataclass
class Report:
    action: str
    info: SystemInfo
    results: list[StepResult]
    pins: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return all(r.status != StepStatus.FAILED for r in self.results)

    def to_dict(self) -> dict:
        return {
            "tool": "lai_setup", "tool_version": __version__, "action": self.action,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.started)),
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.finished)),
            "ok": self.ok, "system": self.info.to_dict(),
            "results": [r.to_dict() for r in self.results],
            "pins": self.pins, "notes": self.notes,
        }

    def to_text(self) -> str:
        i, o = self.info, self.info.ollama
        gpus = ", ".join(f"{g.vendor}{'/' + g.accel if g.accel else ''}" for g in i.gpus) or "yok (CPU)"
        lines = [
            f"Local Assessment AI — {ACTION_TR.get(self.action, self.action)} raporu",
            f"Tarih: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.finished))}",
            f"Sonuç: {'BAŞARILI' if self.ok else 'HATALI'}",
            "",
            "Sistem",
            f"  İşletim sistemi : {i.os} / {i.distro_id} {i.distro_version} ({i.arch})",
            f"  Bellek          : {i.mem_total_mb} MB",
            f"  Boş disk        : {i.disk_free_mb} MB",
            f"  İşlemci         : {i.cpu_model} — {i.cpu_cores} çekirdek — {' '.join(i.cpu_flags) or '-'}",
            f"  GPU             : {gpus}",
            f"  Python          : {i.python_version}",
            f"  Ollama (sistem) : {o.binary or 'yok'} {o.version or ''}"
            f"{' — servis kayıtlı' if o.system_service else ''}"
            f"{' — 11434 dinleniyor' if o.listening_11434 else ''}",
            f"  Sistem modelleri: {', '.join(o.models_11434) or '-'}",
            f"  Uygulama portu  : 11435 {'DOLU' if o.listening_11435 else 'boş'}"
            f"{' — UYARI: 0.0.0.0 üzerinde dinleniyor' if o.wildcard_11435 else ''}",
            "",
            "Adımlar",
        ]
        for r in self.results:
            lines.append(f"  [{STATUS_TR[r.status]:<9}] {r.title}: {r.message}")
        if self.pins:
            lines += ["", "Sabitlenen sürümler"] + [f"  {k}: {v}" for k, v in sorted(self.pins.items())]
        if self.notes:
            lines += ["", "Notlar"] + [f"  - {n}" for n in self.notes]
        return "\n".join(lines) + "\n"

    def write(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{self.action}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.finished))}"
        n, base = 1, stem
        while (directory / f"{stem}.json").exists():
            n += 1
            stem = f"{base}-{n}"
        j, t = directory / f"{stem}.json", directory / f"{stem}.txt"
        j.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        t.write_text(self.to_text(), encoding="utf-8")
        return j, t
