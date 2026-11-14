"""M3 kurulum adımı: SQLite veritabanı (sürümlü şema, göç öncesi yedek)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from lai.store.db import LATEST_VERSION, MIGRATIONS, current_version, migrate, open_db

from .steps import Context, Step


def db_path(paths) -> Path:
    return paths.data / "lai.db"


def backup_dir(paths) -> Path:
    return paths.data / "backups"


def inspect_db(path: Path) -> tuple[bool, str, int | None]:
    """Salt okunur denetim: (sağlam mı, açıklama, şema sürümü). Dosyaya yazmaz."""
    if not path.exists():
        return False, "veritabanı yok", None
    try:
        conn = open_db(path, readonly=True)
    except sqlite3.Error as e:
        return False, f"açılamadı: {e}", None
    try:
        ver = current_version(conn)
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            return False, f"bütünlük denetimi başarısız: {check}", ver
        if ver != LATEST_VERSION:
            return False, f"şema v{ver}, beklenen v{LATEST_VERSION} (repair ile güncellenir)", ver
        applied = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
        if any(applied.get(m.version) != m.checksum for m in MIGRATIONS):
            return False, "şema göç kaydı değiştirilmiş", ver
        return True, f"{path} (şema v{ver}, bütünlük: ok)", ver
    except sqlite3.Error as e:
        return False, f"geçerli bir veritabanı değil: {e}", None
    finally:
        conn.close()


class InitDatabase(Step):
    id = "database"
    title = "Veritabanı (SQLite)"

    def check(self, ctx: Context) -> bool:
        return self.verify(ctx)[0]

    def apply(self, ctx: Context) -> None:
        path = db_path(ctx.paths)
        ok, msg, ver = inspect_db(path)
        if path.exists() and ver is None:
            # bozuk dosya: üzerine yazılmaz (kullanıcı verisi olabilir), kullanıcı karar verir
            raise RuntimeError(f"{msg}. Dosya korunuyor; 'db restore <yedek>' ile geri yükleyin.")
        conn = open_db(path)
        try:
            migrate(conn, backup_dir=backup_dir(ctx.paths))
        finally:
            conn.close()

    def verify(self, ctx: Context) -> tuple[bool, str]:
        ok, msg, _ = inspect_db(db_path(ctx.paths))
        return ok, msg

    def record_pins(self, ctx: Context) -> None:
        ctx.state.pins["schema_version"] = str(LATEST_VERSION)


def m3_steps() -> list[Step]:
    return [InitDatabase()]
