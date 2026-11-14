"""Komut satırı:

  python -m lai.learning reference --db YOL --inventory AD DOSYA…   # başvuru belgesi (yönerge)
  python -m lai.learning examples  --db YOL --inventory AD DOSYA    # örnek sonuçlar (lai-examples)
  python -m lai.learning discover  --db YOL --inventory AD [--port 11435] [--model qwen3.5:4b]
  python -m lai.learning fit       --db YOL --inventory AD          # yalnız örnek uyumu
  python -m lai.learning rules     --db YOL --inventory AD          # güncel kurallar

Yerel AI yalnız 127.0.0.1 üzerinden ve uygulamanın kendi portundan kullanılır; 11434 (sistem
Ollama servisi) reddedilir. Çıkış kodları: 0 tamam · 1 kısmen/başarısız · 2 kullanım.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lai.ingest.model import IngestError


def _store(a):
    from lai.store.store import Store
    db = Path(a.db)
    return Store.open(db, backup_dir=db.parent / "backups")


def _inventory(store, name: str, *, create: bool) -> int | None:
    row = store.conn.execute("SELECT id FROM inventories WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    return store.inventory.create_inventory(name) if create else None


def _docs_dir(a) -> Path:
    return Path(a.documents_dir) if a.documents_dir else Path(a.db).parent / "documents"


def _out(a, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1) if a.json else text)


def _reference(a, store, inv) -> int:
    from lai.ingest.service import import_reference_document
    rc, out = 0, []
    for f in a.files:
        try:
            r = import_reference_document(store, inv, Path(f), documents_dir=_docs_dir(a))
        except IngestError as e:
            print(f"BAŞARISIZ [{e.code}] {e.message}", file=sys.stderr)
            rc = 1
            continue
        out.append({"file": r.file_name, "document_id": r.document_id, "units": r.unit_count,
                    "duplicate": r.duplicate})
        if not a.json:
            print(f"{r.file_name}: {'ZATEN KAYITLI' if r.duplicate else 'KAYDEDİLDİ'} "
                  f"(belge #{r.document_id}, {r.unit_count} metin birimi)")
            for i in r.issues:
                print(f"  [{i.severity}] [{i.code}] {i.message}")
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return rc


def _examples(a, store, inv) -> int:
    from lai.learning.examples import import_examples
    try:
        r = import_examples(store, inv, Path(a.file), documents_dir=_docs_dir(a))
    except IngestError as e:
        print(f"BAŞARISIZ [{e.code}] {e.message}", file=sys.stderr)
        return 1
    _out(a, {"set_id": r.set_id, "count": r.count, "duplicate": r.duplicate},
         f"{Path(a.file).name}: {'ZATEN KAYITLI' if r.duplicate else 'KAYDEDİLDİ'} "
         f"({r.count} örnek, küme #{r.set_id})")
    return 0


def _discover(a, store, inv) -> int:
    from lai.ai.llm import OllamaLLM
    from lai.learning.discovery import discover_rules
    from lai.learning.report import discovery_dict, format_discovery
    from lai_runtime.config import RuntimeConfig
    try:
        cfg = RuntimeConfig(port=a.port, model=a.model)
    except ValueError as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 2
    llm = OllamaLLM(cfg.base_url, cfg.model, think=not a.no_think)

    def progress(n, name, i, total):
        if not a.json:
            print(f"  … {name} parça {i}/{total}", file=sys.stderr, flush=True)
    rep = discover_rules(store, inv, llm, document_ids=a.document or None,
                         max_chars=a.max_chars, progress=progress)
    _out(a, discovery_dict(rep), format_discovery(rep))
    return 0 if rep.status == "completed" else 1


def _fit(a, store, inv) -> int:
    from lai.learning.fit import run_fit
    r = run_fit(store, inv)
    _out(a, {"fit_run_id": r.fit_run_id, "examples": r.example_count, "outcomes": r.outcomes,
             "changed": r.changed},
         f"Örnek uyumu #{r.fit_run_id}: {r.example_count} örnek, {len(r.outcomes)} kural, "
         f"{len(r.changed)} durum değişti" + "".join(f"\n  {k}: {f} → {t}" for k, f, t in r.changed))
    return 0


def _rules(a, store, inv) -> int:
    from lai.learning.report import format_rules, rules_rows
    rows = rules_rows(store, inv)
    _out(a, rows, format_rules(rows))
    return 0


def _missing(a, store, inv):
    from dataclasses import asdict

    from lai.learning.interactive import analyze, gaps
    analyze(store, inv)
    rows = gaps(store, inv, include_closed=a.all)
    _out(a, [asdict(g) for g in rows], "\n\n".join(
        f"#{g.id} [{g.severity}] [{g.state}] {g.payload['question']}\n"
        f"Neden: {g.payload['why']}\nEtkilenen: {g.payload['dimensions']}, "
        f"{len(g.payload['items'])} madde\nKanıt: {g.payload['evidence']}\n"
        f"Seçenekler: {', '.join(g.payload['choices'])}; serbest metin kabul edilir."
        for g in rows) or 'Açık soru yok.')
    return 0


def _answer(a, store, inv):
    from lai.ai.llm import LLMError, LLMUnavailable
    from lai.learning.interactive import answer, interpret_answer
    row = store.conn.execute('SELECT inventory_id FROM learning_gaps WHERE id=?',(a.question,)).fetchone()
    if row is None or row[0] != inv:
        print('HATA: soru bu envantere ait değil', file=sys.stderr)
        return 2
    try:
        structured = json.loads(a.structured) if a.structured else None
        if a.interpret_with_model:
            if a.action != 'text' or structured is not None:
                raise ValueError('Model yorumu --action text ile kullanılır')
            from lai.ai.llm import OllamaLLM
            from lai_runtime.config import RuntimeConfig
            cfg = RuntimeConfig(port=a.port,model=a.model)
            result = interpret_answer(store,a.question,a.text,OllamaLLM(cfg.base_url,cfg.model))
        else:
            result = answer(store,a.question,a.action,a.text,structured=structured)
    except (ValueError, KeyError, LLMError, LLMUnavailable) as e:
        print(f'HATA: {e}',file=sys.stderr)
        return 2
    _out(a,result,f"Yanıt kaydedildi: {result['state']}; profil #{result['profile_version_id']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m lai.learning",
                                description="Puanlama kuralı keşfi (yerel AI önerir, kod doğrular).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = {n: sub.add_parser(n, help=h) for n, h in (
        ("reference", "madde içermeyen başvuru belgesi (puanlama yönergesi) kaydet"),
        ("examples", "örnek sonuç dosyası (lai-examples JSON) içe aktar"),
        ("discover", "yerel AI ile kural adaylarını çıkar ve doğrula"),
        ("fit", "kuralları örnek sonuçlarla yeniden sına"),
        ("rules", "güncel kuralları ve durumlarını listele"),
        ("missing", "eksik bilgileri analiz et ve Türkçe soruları listele"),
        ("answer", "bir eksik bilgi sorusunu yanıtla"))}
    for s in sp.values():
        s.add_argument("--db", required=True)
        s.add_argument("--inventory", required=True, help="envanter adı")
        s.add_argument("--documents-dir")
        s.add_argument("--json", action="store_true")
    sp["reference"].add_argument("files", nargs="+")
    sp["examples"].add_argument("file")
    d = sp["discover"]
    d.add_argument("--port", type=int, default=11435, help="uygulamanın Ollama portu (11434 yasak)")
    d.add_argument("--model", default="qwen3.5:4b")
    d.add_argument("--no-think", action="store_true", help="düşünme modunu kapat")
    d.add_argument("--document", type=int, action="append", help="yalnız bu belge(ler)")
    d.add_argument("--max-chars", type=int, default=6000)
    sp["missing"].add_argument("--all", action="store_true", help="ertelenmiş/kapalı sorular dahil")
    sp["answer"].add_argument("--question", type=int, required=True)
    sp["answer"].add_argument("--action", required=True, choices=["yes","no","text","unknown","later","reject","correct","teach","interpret","resume"])
    sp["answer"].add_argument("--text", default="")
    sp["answer"].add_argument("--structured", help="Açık kullanıcı kuralı: rule_type ve body JSON")
    sp["answer"].add_argument("--interpret-with-model", action="store_true", help="Serbest metni yerel model ile doğrulanmamış öneriye dönüştür")
    sp["answer"].add_argument("--port", type=int, default=11435)
    sp["answer"].add_argument("--model", default="qwen3.5:4b")
    from lai.assessment.cli import add_commands
    add_commands(sub)
    from lai.reports.cli import add_commands as add_reports
    add_reports(sub)
    a = p.parse_args(argv)
    store = _store(a)
    try:
        from lai.reports.cli import COMMANDS as REPORT_COMMANDS, run as run_report
        if a.cmd in REPORT_COMMANDS:
            return run_report(a, store)
        from lai.assessment.cli import COMMANDS, run
        if a.cmd in COMMANDS:
            return run(a,store)
        inv = _inventory(store, a.inventory, create=a.cmd in ("reference", "examples"))
        if inv is None:
            print(f"HATA: '{a.inventory}' adlı envanter yok; önce belgeleri içe aktarın "
                  f"(python -m lai.ingest import).", file=sys.stderr)
            return 2
        return {"reference": _reference, "examples": _examples, "discover": _discover,
                "fit": _fit, "rules": _rules, "missing": _missing, "answer": _answer}[a.cmd](a, store, inv)
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
