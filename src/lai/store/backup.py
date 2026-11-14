"""Veritabanı yedekleme ve geri yükleme.

- Yedek, SQLite çevrimiçi yedekleme API'siyle alınır (WAL'deki işlenmiş veriler dahil, tutarlı).
- Her yedeğin yanında sha256, şema sürümü ve zaman içeren bir .json kaydı bulunur.
- Geri yüklemeden önce yedek doğrulanır (sha256 + PRAGMA integrity_check + şema sürümü) ve
  mevcut veritabanının güvenlik yedeği alınır. Yedekler kullanıcı verisidir; otomatik silinmez.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class BackupError(RuntimeError):
    pass


@dataclass
class BackupInfo:
    path: Path
    sha256: str
    schema_version: int
    created_at: str
    label: str


@dataclass
class RestoreResult:
    restored: Path
    safety_backup: Path | None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def _check(path: Path) -> int:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        res = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if res != "ok":
            raise BackupError(f"bütünlük denetimi başarısız ({path.name}): {res}")
        return conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as e:
        raise BackupError(f"geçerli bir SQLite veritabanı değil ({path.name}): {e}") from None
    finally:
        conn.close()


def backup_database(db_path: Path, dest_dir: Path, *, label: str = "manual") -> BackupInfo:
    db_path, dest_dir = Path(db_path), Path(dest_dir)
    if not db_path.exists():
        raise BackupError(f"veritabanı yok: {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(dest_dir, 0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
    target = dest_dir / f"lai-{stamp}-{safe_label}.db"
    n = 1
    while target.exists():
        n += 1
        target = dest_dir / f"lai-{stamp}-{safe_label}-{n}.db"
    tmp = target.with_suffix(".db.tmp")
    src = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        os.close(os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600))
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")  # tek dosya, -wal yok
        finally:
            dst.close()
    finally:
        src.close()
    version = _check(tmp)
    os.replace(tmp, target)
    info = BackupInfo(target, _sha256(target), version,
                      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), label)
    meta = target.with_suffix(".json")
    meta.write_text(json.dumps({"file": target.name, "sha256": info.sha256,
                                "schema_version": version, "created_at": info.created_at,
                                "label": label, "source": str(db_path)},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(meta, 0o600)
    return info


def verify_backup(path: Path) -> BackupInfo:
    path = Path(path)
    meta_file = path.with_suffix(".json")
    if not path.is_file() or not meta_file.is_file():
        raise BackupError(f"yedek ya da yanındaki .json kaydı yok: {path}")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    actual = _sha256(path)
    if actual != meta.get("sha256"):
        raise BackupError(f"yedek dosyası değişmiş/bozulmuş (sha256 uyuşmuyor): {path.name}")
    version = _check(path)
    if version != meta.get("schema_version"):
        raise BackupError("yedek kaydındaki şema sürümü dosyayla uyuşmuyor")
    return BackupInfo(path, actual, version, meta.get("created_at", ""), meta.get("label", ""))


def restore_database(backup_path: Path, db_path: Path, *, safety_dir: Path) -> RestoreResult:
    """Uygulama kapalıyken çağrılmalıdır (açık bağlantı varken dosya değiştirilmez)."""
    from .db import LATEST_VERSION
    info = verify_backup(backup_path)
    if info.schema_version > LATEST_VERSION:
        raise BackupError(f"yedek şeması v{info.schema_version}, uygulama en çok "
                          f"v{LATEST_VERSION} tanıyor")
    db_path = Path(db_path)
    safety = None
    if db_path.exists():
        safety = backup_database(db_path, safety_dir, label="pre-restore").path
    tmp = db_path.with_name(db_path.name + ".restore-tmp")
    tmp.write_bytes(Path(backup_path).read_bytes())
    os.chmod(tmp, 0o600)
    if _sha256(tmp) != info.sha256:
        tmp.unlink()
        raise BackupError("kopyalama sırasında yedek bozuldu")
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            side.unlink()  # eski veritabanına ait günlük; yedekle birleşmemeli
    os.replace(tmp, db_path)
    return RestoreResult(db_path, safety)
