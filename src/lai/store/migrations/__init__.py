"""Şema göçleri: NNNN_ad.sql dosyaları sırayla uygulanır. Uygulanmış bir dosya DEĞİŞTİRİLMEZ;
değişiklik yeni bir göç dosyasıyla yapılır (sağlama toplamı denetlenir)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def load_migrations() -> list[Migration]:
    out = []
    for f in sorted(resources.files(__package__).iterdir(), key=lambda p: p.name):
        if f.name.endswith(".sql") and f.name[:4].isdigit():
            out.append(Migration(int(f.name[:4]), f.name[5:-4], f.read_text(encoding="utf-8")))
    versions = [m.version for m in out]
    if versions != list(range(1, len(out) + 1)):
        raise RuntimeError(f"göç sürümleri ardışık değil: {versions}")
    return out
