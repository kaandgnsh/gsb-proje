"""Mevcut CLI için salt okunur dışa aktarma komutları."""

import json
import sqlite3
import sys

from .service import export_assessment, export_job

COMMANDS = ("export", "export-job")


def add_commands(sub):
    for name in COMMANDS:
        p = sub.add_parser(name, help="Kayıtlı sonucu yerel dosyaya aktar")
        p.add_argument("--db", required=True)
        p.add_argument(
            "--id", type=int, required=True, help="Değerlendirme veya iş kimliği"
        )
        p.add_argument(
            "--format",
            choices=["csv", "xlsx", "pdf", "json"]
            if name == "export"
            else ["csv", "xlsx", "pdf"],
            required=True,
        )
        p.add_argument("--output", required=True, help="Toplu PDF için ZIP hedefi")
        p.add_argument("--json", action="store_true")


def run(a, store):
    try:
        fn = export_assessment if a.cmd == "export" else export_job
        result = fn(store, a.id, a.output, a.format)
    except KeyboardInterrupt:
        print("Dışa aktarma kesildi; hedef korunuyor.", file=sys.stderr)
        return 130
    except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error) as error:
        if a.json:
            print(json.dumps({"error": str(error)}, ensure_ascii=False))
        else:
            print(f"HATA: Rapor yazılamadı: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(result, ensure_ascii=False)
        if a.json
        else f"Rapor kaydedildi: {result['path']}"
    )
    return 0
