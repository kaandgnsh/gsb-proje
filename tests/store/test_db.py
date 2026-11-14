import hashlib
import sqlite3

import pytest

from lai.store import db as dbmod
from lai.store.db import (LATEST_VERSION, MigrationChecksumError, SchemaTooNewError,
                          current_version, migrate, open_db)

TABLES = {"schema_migrations", "app_meta", "inventories", "inventory_versions", "source_documents",
          "evidence_refs", "dimensions", "poles", "items", "options", "knowledge_entries",
          "knowledge_evidence", "user_corrections", "requirements", "clarification_questions",
          "question_responses", "learning_history", "scoring_profiles", "scoring_profile_versions",
          "profile_version_entries", "participants", "response_sets", "responses", "assessments",
          "assessment_feedback"}
VIEWS = {"current_knowledge", "confirmed_rules", "inferred_rules", "unresolved_knowledge",
         "pending_questions"}


def _objects(conn, kind):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_fresh_database_gets_latest_schema(tmp_path):
    p = tmp_path / "veri İ" / "lai.db"
    conn = open_db(p)
    res = migrate(conn, backup_dir=tmp_path / "yedek")
    assert (res.from_version, res.to_version) == (0, LATEST_VERSION)
    assert res.backup is None  # boş veritabanı yedeklenmez
    assert current_version(conn) == LATEST_VERSION
    assert TABLES <= _objects(conn, "table")
    assert VIEWS <= _objects(conn, "view")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert conn.execute("SELECT value FROM app_meta WHERE key='database_id'").fetchone()[0]


def test_second_migrate_is_noop(tmp_path):
    p = tmp_path / "lai.db"
    conn = open_db(p)
    migrate(conn, backup_dir=None)
    rows = conn.execute("SELECT * FROM schema_migrations").fetchall()
    res = migrate(conn, backup_dir=None)
    assert res.from_version == res.to_version == LATEST_VERSION
    assert conn.execute("SELECT * FROM schema_migrations").fetchall() == rows


def test_newer_schema_is_refused_and_untouched(tmp_path):
    p = tmp_path / "lai.db"
    conn = open_db(p)
    migrate(conn, backup_dir=None)
    conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 5}")
    conn.close()
    before = _sha(p)
    conn = open_db(p)
    with pytest.raises(SchemaTooNewError):
        migrate(conn, backup_dir=None)
    conn.close()
    assert _sha(p) == before


def test_edited_migration_detected(tmp_path):
    p = tmp_path / "lai.db"
    conn = open_db(p)
    migrate(conn, backup_dir=None)
    conn.execute("UPDATE schema_migrations SET checksum='degisti' WHERE version=1")
    with pytest.raises(MigrationChecksumError):
        migrate(conn, backup_dir=None)


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    p = tmp_path / "lai.db"
    conn = open_db(p)
    migrate(conn, backup_dir=None)
    bad = dbmod.Migration(LATEST_VERSION + 1, "bozuk",
                          "CREATE TABLE yeni_tablo(x INTEGER); SELECT * FROM olmayan_tablo;")
    monkeypatch.setattr(dbmod, "MIGRATIONS", [*dbmod.MIGRATIONS, bad])
    monkeypatch.setattr(dbmod, "LATEST_VERSION", LATEST_VERSION + 1)
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, backup_dir=tmp_path / "yedek")
    assert current_version(conn) == LATEST_VERSION
    assert "yeni_tablo" not in _objects(conn, "table")
    assert len(list((tmp_path / "yedek").glob("*.db"))) == 1  # göç öncesi otomatik yedek


def test_readonly_open_cannot_write(tmp_path):
    p = tmp_path / "lai.db"
    migrate(open_db(p), backup_dir=None)
    ro = open_db(p, readonly=True)
    assert current_version(ro) == LATEST_VERSION
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO app_meta VALUES ('x','y')")


def test_readonly_open_of_missing_db_does_not_create(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        open_db(tmp_path / "yok.db", readonly=True)
    assert not (tmp_path / "yok.db").exists()
