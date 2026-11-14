"""Örnek sonuçlar (`lai-examples` v1): cevaplar + kaynakta bildirilen sonuç.

    {"format": "lai-examples", "version": 1,
     "examples": [{"id": "Ö-1", "answers": {"1": "A", "2": null, ...},
                   "result": {"E-I": {"letter": "E", "scores": {"E": 23, "I": 17}}, ...}}]}

Her boyut sonucunda `letter` ve/veya `scores` bulunmalı. Hiçbir değer tahminle doldurulmaz;
hatalar JSON yoluyla raporlanır ve küme hiç yazılmaz.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lai.ingest.detect import sha256_file
from lai.ingest.model import IngestError
from lai.ingest.readers import read_json
from lai.ingest.service import _store_copy
from lai_runtime.jsonschema_lite import validate

FORMAT, VERSION = "lai-examples", 1
_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["format", "version", "examples"],
    "properties": {
        "format": {"type": "string", "enum": [FORMAT]},
        "version": {"type": "integer", "enum": [VERSION]},
        "description": {"type": "string"},
        "examples": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "answers", "result"],
            "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 100},
                           "answers": {"type": "object"}, "result": {"type": "object"},
                           "type": {"type": "string"}, "note": {"type": "string"}}}},
    },
}
_OUTCOME = {"type": "object", "additionalProperties": False,
            "properties": {"letter": {"type": "string", "minLength": 1, "maxLength": 10},
                           "scores": {"type": "object"}}}


@dataclass(frozen=True)
class Example:
    key: str
    answers: dict[int, str | None]
    outcome: dict[str, dict]
    json_path: str


def parse_examples(data) -> list[Example]:
    errs = validate(data, _SCHEMA)
    out: list[Example] = []
    if not errs:
        seen = set()
        for i, e in enumerate(data["examples"]):
            p = f"$.examples[{i}]"
            if e["id"] in seen:
                errs.append(f"{p}.id: yinelenen örnek kimliği '{e['id']}'")
            seen.add(e["id"])
            ans = {}
            for k, v in e["answers"].items():
                if not (k.isdigit() and int(k) >= 1):
                    errs.append(f"{p}.answers.{k}: madde numarası pozitif tamsayı olmalı")
                elif not (v is None or (isinstance(v, str) and 1 <= len(v) <= 5)):
                    errs.append(f"{p}.answers.{k}: seçenek kodu (metin) ya da null olmalı")
                else:
                    ans[int(k)] = v
            if not e["answers"]:
                errs.append(f"{p}.answers: boş")
            if not e["result"]:
                errs.append(f"{p}.result: en az bir boyut sonucu gerekli")
            for dim, oc in e["result"].items():
                q = f"{p}.result.{dim}"
                sub = validate(oc, _OUTCOME, q)
                if not sub and not ({"letter", "scores"} & set(oc)):
                    sub.append(f"{q}: 'letter' ya da 'scores' gerekli")
                for pole, v in (oc.get("scores") or {}).items() if not sub else ():
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        sub.append(f"{q}.scores.{pole}: sayı olmalı")
                errs += sub
            out.append(Example(e["id"], ans, e["result"], p))
    if errs:
        shown = "; ".join(errs[:10]) + (f" (+{len(errs) - 10} hata daha)" if len(errs) > 10 else "")
        raise IngestError("invalid_examples", f"Örnek sonuç dosyası geçersiz: {shown}")
    return out


def _check_inventory(store, inventory_id: int, examples: list[Example]) -> None:
    row = store.conn.execute("SELECT id FROM inventory_versions WHERE inventory_id=? "
                             "ORDER BY version DESC LIMIT 1", (inventory_id,)).fetchone()
    if row is None:
        return  # envanter henüz içe aktarılmamış: madde denetimi yapılamaz
    items = {it["number"]: {c for c, _ in it["options"]} for it in store.inventory.items(row[0])}
    errs = []
    for e in examples:
        for n, a in e.answers.items():
            if n not in items:
                errs.append(f"{e.json_path}.answers.{n}: envanterde {n}. madde yok")
            elif a is not None and items[n] and a not in items[n]:
                errs.append(f"{e.json_path}.answers.{n}: '{a}' bu maddenin seçeneklerinden biri değil")
    if errs:
        raise IngestError("invalid_examples", "Örnekler envanterle uyuşmuyor: " + "; ".join(errs[:10]))


@dataclass
class ExampleImport:
    set_id: int
    document_id: int
    evidence_id: int
    count: int
    duplicate: bool


def import_examples(store, inventory_id: int, path: Path, *, documents_dir: Path) -> ExampleImport:
    path = Path(path)
    if not path.is_file():
        raise IngestError("not_found", f"{path}: dosya bulunamadı.")
    sha, size = sha256_file(path)
    row = store.conn.execute(
        "SELECT s.id, s.document_id, s.evidence_id, s.example_count FROM example_sets s "
        "JOIN source_documents d ON d.id = s.document_id WHERE s.inventory_id=? AND d.sha256=?",
        (inventory_id, sha)).fetchone()
    if row is not None:
        return ExampleImport(row[0], row[1], row[2], row[3], True)
    examples = parse_examples(read_json(path).data)
    _check_inventory(store, inventory_id, examples)
    stored = _store_copy(path, sha, "json", documents_dir)
    with store.transaction() as c:
        doc_id, _ = store.inventory.add_document(inventory_id, file_name=path.name, sha256=sha,
                                                 kind="json", size_bytes=size,
                                                 stored_path=str(stored))
        ev = c.execute("INSERT INTO evidence_refs(kind, document_id, note, locator) "
                       "VALUES ('example_result', ?, ?, ?)",
                       (doc_id, f"{path.name}: {len(examples)} örnek sonuç",
                        json.dumps({"json_path": "$.examples"}))).lastrowid
        sid = c.execute("INSERT INTO example_sets(inventory_id, document_id, format_version, "
                        "example_count, evidence_id) VALUES (?,?,?,?,?)",
                        (inventory_id, doc_id, VERSION, len(examples), ev)).lastrowid
        c.executemany("INSERT INTO example_results(example_set_id, seq, example_key, answers, "
                      "outcome, json_path) VALUES (?,?,?,?,?,?)",
                      [(sid, i, e.key, json.dumps({str(k): v for k, v in e.answers.items()},
                                                  ensure_ascii=False),
                        json.dumps(e.outcome, ensure_ascii=False), e.json_path)
                       for i, e in enumerate(examples)])
    return ExampleImport(sid, doc_id, ev, len(examples), False)


def load_examples(store, inventory_id: int) -> tuple[list[Example], list[int]]:
    """Envanterin tüm örnekleri ve kümelerin kanıt kimlikleri."""
    exs, evs = [], []
    for sid, ev in store.conn.execute("SELECT id, evidence_id FROM example_sets "
                                      "WHERE inventory_id=? ORDER BY id", (inventory_id,)):
        evs.append(ev)
        for key, ans, oc, jp in store.conn.execute(
                "SELECT example_key, answers, outcome, json_path FROM example_results "
                "WHERE example_set_id=? ORDER BY seq", (sid,)):
            exs.append(Example(f"{sid}:{key}", {int(k): v for k, v in json.loads(ans).items()},
                               json.loads(oc), jp))
    return exs, evs
