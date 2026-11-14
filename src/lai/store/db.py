"""SQLite bağlantısı ve sürümlü şema göçleri."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .migrations import Migration, load_migrations

MIGRATIONS: list[Migration] = load_migrations()
LATEST_VERSION: int = len(MIGRATIONS)


class SchemaTooNewError(RuntimeError):
    pass


class MigrationChecksumError(RuntimeError):
    pass


@dataclass
class MigrationResult:
    from_version: int
    to_version: int
    backup: Path | None


def open_db(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True,
                               isolation_level=None, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        if new:
            os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _db_path(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else None


def migrate(conn: sqlite3.Connection, *, backup_dir: Path | None) -> MigrationResult:
    import sys
    mod = sys.modules[__name__]  # testlerde MIGRATIONS/LATEST_VERSION değiştirilebilsin
    migrations, latest = mod.MIGRATIONS, mod.LATEST_VERSION
    start = current_version(conn)
    if start > latest:
        raise SchemaTooNewError(
            f"veritabanı şeması v{start}, bu uygulama en çok v{latest} tanıyor; "
            "eski uygulama sürümüyle açılmaya çalışılıyor olabilir. Hiçbir şey değiştirilmedi.")
    if start > 0:
        applied = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
        for m in migrations[:start]:
            if applied.get(m.version) != m.checksum:
                raise MigrationChecksumError(
                    f"uygulanmış göç v{m.version} ({m.name}) değiştirilmiş; şema güvenilmez")
    if start == latest:
        return MigrationResult(start, start, None)

    backup = None
    db_path = _db_path(conn)
    if start > 0 and backup_dir is not None and db_path is not None:
        from .backup import backup_database
        backup = backup_database(db_path, backup_dir, label=f"premigrate-v{start}").path

    for m in migrations[start:]:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        script = (f"BEGIN IMMEDIATE;\n{m.sql}\n;"
                  f"CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                  f"name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);"
                  f"INSERT INTO schema_migrations VALUES ({m.version}, '{m.name}', "
                  f"'{m.checksum}', '{ts}');"
                  f"PRAGMA user_version = {m.version};\nCOMMIT;")
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return MigrationResult(start, latest, backup)
