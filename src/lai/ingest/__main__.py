"""Komut satırı: `python -m lai.ingest parse DOSYA` (veritabanısız deneme) ve
`python -m lai.ingest import --db YOL --inventory AD DOSYA…`.

Çıkış kodları: 0 tümü içe aktarıldı/zaten var · 1 inceleme gerekiyor ya da başarısız · 2 kullanım.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import ERROR, WARNING, IngestError
from .parse import parse_file
from .report import format_report, report_dict


def _parse_cmd(a) -> int:
    try:
        res = parse_file(Path(a.file), kind=a.kind, encoding=a.encoding)
    except IngestError as e:
        print(f"BAŞARISIZ [{e.code}] {e.message}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps({"kind": res.kind, "items": len(res.items),
                          "options": sum(len(i.options) for i in res.items),
                          "answer_keys": len(res.answer_keys),
                          "issues": [{"severity": i.severity, "code": i.code, "message": i.message,
                                      "location": i.locator.describe() if i.locator else None}
                                     for i in res.issues]}, ensure_ascii=False, indent=1))
    else:
        print(f"{Path(a.file).name}: {len(res.items)} madde, "
              f"{sum(len(i.options) for i in res.items)} seçenek, {len(res.answer_keys)} anahtar; "
              f"{res.count(ERROR)} hata, {res.count(WARNING)} uyarı")
        for i in res.issues:
            where = f" — {i.locator.describe()}" if i.locator else ""
            print(f"  [{i.severity}] [{i.code}] {i.message}{where}")
    return 1 if res.has_errors else 0


def _import_cmd(a) -> int:
    from lai.store.store import Store

    from .service import import_document
    db = Path(a.db)
    store = Store.open(db, backup_dir=db.parent / "backups")
    try:
        row = store.conn.execute("SELECT id FROM inventories WHERE name=?", (a.inventory,)).fetchone()
        inv = row[0] if row else store.inventory.create_inventory(a.inventory)
        docs = Path(a.documents_dir) if a.documents_dir else db.parent / "documents"
        rc, out = 0, []
        for f in a.files:
            try:
                rep = import_document(store, inv, Path(f), documents_dir=docs, kind=a.kind,
                                      encoding=a.encoding)
            except IngestError as e:
                print(f"BAŞARISIZ [{e.code}] {e.message}", file=sys.stderr)
                rc = 1
                continue
            if rep.status in ("needs_review", "failed"):
                rc = 1
            if a.json:
                out.append(report_dict(rep))
            else:
                print(format_report(rep))
                print()
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        return rc
    finally:
        store.close()


def _key_cmd(a):
    from lai.store.store import Store

    from .answer_key import import_answer_key

    db = Path(a.db)
    store = Store.open(db, backup_dir=db.parent / "backups")
    try:
        row = store.conn.execute(
            "SELECT id FROM inventories WHERE name=?", (a.inventory,)
        ).fetchone()
        if row is None:
            raise IngestError("no_inventory", "Önce envanteri içe aktarın.")
        r = import_answer_key(
            store, row[0], Path(a.file), documents_dir=db.parent / "documents"
        )
        print(
            json.dumps(r, ensure_ascii=False)
            if a.json
            else f"{r['answer_key_count']} anahtar kaydedildi; puanlama kuralı üretilmedi."
        )
        return 0
    except IngestError as e:
        print(f"HATA: {e.message}", file=sys.stderr)
        return 1
    finally:
        store.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m lai.ingest",
                                description="Envanter belgelerini içe aktarır (PDF/DOCX/XLSX/CSV/JSON).")
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("parse", help="yalnız ayrıştır ve raporla (veritabanına yazmaz)")
    pp.add_argument("file")
    ip = sub.add_parser("import", help="veritabanına içe aktar")
    ip.add_argument("--db", required=True)
    ip.add_argument("--inventory", required=True, help="envanter adı (yoksa oluşturulur)")
    ip.add_argument("--documents-dir", help="özgün dosya kopyaları (varsayılan: <db dizini>/documents)")
    ip.add_argument("files", nargs="+")
    for sp in (pp, ip):
        sp.add_argument("--kind", choices=["pdf", "docx", "xlsx", "csv", "json"])
        sp.add_argument("--encoding", help="CSV kodlaması (ör. cp1254); varsayılan UTF-8")
        sp.add_argument("--json", action="store_true")
    kp=sub.add_parser("key",help="ayrı cevap anahtarını ham değer olarak kaydet")
    kp.add_argument("--db",required=True);kp.add_argument("--inventory",required=True)
    kp.add_argument("--json",action="store_true");kp.add_argument("file")
    a = p.parse_args(argv)
    if a.cmd=="key":return _key_cmd(a)
    return _parse_cmd(a) if a.cmd == "parse" else _import_cmd(a)


if __name__ == "__main__":
    sys.exit(main())
