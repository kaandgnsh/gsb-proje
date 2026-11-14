"""`db status|backup|restore|verify` alt komutları."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from lai.store.backup import BackupError, backup_database, restore_database, verify_backup

from .m3_steps import backup_dir, db_path, inspect_db

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_CANCEL = 0, 1, 2, 3


def _in_use(path: Path) -> bool:
    """Başka bir bağlantı yazma işlemi tutuyorsa True (uygulama açık olabilir)."""
    conn = sqlite3.connect(path, timeout=0.2, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        conn.close()


def db_command(args, paths, interactive, ask, out, err) -> int:
    path = db_path(paths)
    if args.db_cmd == "status":
        ok, msg, ver = inspect_db(path)
        if not path.exists():
            out("Veritabanı henüz yok (install ile oluşturulur).")
            return EXIT_OK
        out(f"Veritabanı: {path}")
        out(f"  şema: v{ver}  boyut: {path.stat().st_size / 1024:.0f} KB  "
            f"bütünlük: {'ok' if ok else msg}")
        if ok:
            conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                for label, table in (("envanter", "inventories"), ("bilgi kaydı", "knowledge_entries"),
                                     ("bekleyen soru", "pending_questions"),
                                     ("katılımcı", "participants"), ("değerlendirme", "assessments")):
                    out(f"  {label}: {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
            finally:
                conn.close()
        backups = sorted(backup_dir(paths).glob("lai-*.db")) if backup_dir(paths).exists() else []
        out(f"  yedek sayısı: {len(backups)}" + (f" (son: {backups[-1].name})" if backups else ""))
        return EXIT_OK if ok else EXIT_FAIL

    try:
        if args.db_cmd == "backup":
            dest = Path(args.to).expanduser() if args.to else backup_dir(paths)
            info = backup_database(path, dest, label="manual")
            out(f"Yedek alındı: {info.path} (şema v{info.schema_version}, sha256 {info.sha256[:12]}…)")
            return EXIT_OK

        if args.db_cmd == "verify":
            info = verify_backup(Path(args.file))
            out(f"Yedek sağlam: {info.path.name} (şema v{info.schema_version}, {info.created_at})")
            return EXIT_OK

        if args.db_cmd == "restore":
            src = Path(args.file).expanduser()
            info = verify_backup(src)
            out(f"Geri yüklenecek: {src.name} (şema v{info.schema_version}, {info.created_at}).\n"
                f"Mevcut veritabanının güvenlik yedeği alınacak: {backup_dir(paths)}")
            if not args.yes:
                if not interactive:
                    err("Etkileşimsiz geri yükleme için --yes gerekir.")
                    return EXIT_USAGE
                if ask("Devam edilsin mi? [e/H]: ").lower() not in ("e", "evet", "y", "yes"):
                    out("İptal edildi.")
                    return EXIT_CANCEL
            if path.exists() and _in_use(path):
                err("Veritabanı şu anda kullanılıyor (uygulama açık olabilir). Kapatıp yeniden deneyin.")
                return EXIT_FAIL
            res = restore_database(src, path, safety_dir=backup_dir(paths))
            out(f"Geri yüklendi. Önceki hâlin yedeği: {res.safety_backup}")
            return EXIT_OK
    except (BackupError, sqlite3.Error) as e:
        err(f"Veritabanı hatası: {e}")
        return EXIT_FAIL
    return EXIT_USAGE
