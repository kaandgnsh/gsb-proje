import hashlib
import json
import sqlite3

import pytest

from lai.store.backup import BackupError, backup_database, restore_database, verify_backup
from lai.store.db import LATEST_VERSION
from lai.store.store import Store


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _count(path, table="inventories"):
    c = sqlite3.connect(path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "data" / "lai.db"
    s = Store.open(p)
    s.inventory.create_inventory("Birinci")
    yield p, s
    s.close()


def test_backup_contains_committed_wal_data(db, tmp_path):
    p, s = db
    assert p.with_name("lai.db-wal").exists()  # veri henüz WAL'de
    info = backup_database(p, tmp_path / "yedek", label="elle")
    assert info.schema_version == LATEST_VERSION
    assert info.sha256 == _sha(info.path)
    assert _count(info.path) == 1
    assert oct(info.path.stat().st_mode & 0o777) == "0o600"
    meta = json.loads(info.path.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["sha256"] == info.sha256 and meta["label"] == "elle"
    assert not info.path.with_name(info.path.name + "-wal").exists()
    assert verify_backup(info.path).sha256 == info.sha256


def test_restore_reverts_and_keeps_safety_copy(db, tmp_path):
    p, s = db
    info = backup_database(p, tmp_path / "yedek")
    s.inventory.create_inventory("İkinci (yedekten sonra)")
    s.close()
    res = restore_database(info.path, p, safety_dir=tmp_path / "guvenlik")
    assert _count(p) == 1
    assert res.safety_backup and _count(res.safety_backup) == 2
    s2 = Store.open(p)
    assert s2.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    s2.close()


def test_corrupt_backup_rejected_and_target_untouched(db, tmp_path):
    p, s = db
    info = backup_database(p, tmp_path / "yedek")
    s.close()
    data = bytearray(info.path.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 256] = bytes(x ^ 0xFF for x in data[mid:mid + 256])
    info.path.write_bytes(bytes(data))
    assert _sha(info.path) != info.sha256  # dosya gerçekten bozuldu
    before = _sha(p)
    with pytest.raises(BackupError, match="sha256"):
        verify_backup(info.path)
    with pytest.raises(BackupError):
        restore_database(info.path, p, safety_dir=tmp_path / "guvenlik")
    assert _sha(p) == before
    assert not (tmp_path / "guvenlik").exists()


def test_backup_with_forged_metadata_still_checked(db, tmp_path):
    p, s = db
    info = backup_database(p, tmp_path / "yedek")
    info.path.write_bytes(b"SQLite olmayan icerik" * 100)
    meta = info.path.with_suffix(".json")
    m = json.loads(meta.read_text(encoding="utf-8"))
    m["sha256"] = _sha(info.path)
    meta.write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(BackupError, match="SQLite"):
        verify_backup(info.path)


def test_newer_schema_backup_refused(db, tmp_path):
    p, s = db
    info = backup_database(p, tmp_path / "yedek")
    c = sqlite3.connect(info.path)
    c.execute(f"PRAGMA user_version={LATEST_VERSION + 1}")
    c.close()
    m = json.loads(info.path.with_suffix(".json").read_text(encoding="utf-8"))
    m.update(sha256=_sha(info.path), schema_version=LATEST_VERSION + 1)
    info.path.with_suffix(".json").write_text(json.dumps(m), encoding="utf-8")
    s.close()
    with pytest.raises(BackupError, match="v"):
        restore_database(info.path, p, safety_dir=tmp_path / "g")


def test_missing_metadata_rejected(db, tmp_path):
    p, _ = db
    info = backup_database(p, tmp_path / "yedek")
    info.path.with_suffix(".json").unlink()
    with pytest.raises(BackupError, match="json"):
        verify_backup(info.path)


def test_restore_into_missing_database(db, tmp_path):
    p, s = db
    info = backup_database(p, tmp_path / "yedek")
    s.close()
    target = tmp_path / "yeni" / "lai.db"
    target.parent.mkdir()
    res = restore_database(info.path, target, safety_dir=tmp_path / "g")
    assert res.safety_backup is None and _count(target) == 1
