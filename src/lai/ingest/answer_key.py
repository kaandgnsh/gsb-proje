"""Ayrı cevap anahtarı: ham değerler ve konum; puanlama kuralı çıkarılmaz."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .detect import detect_kind, sha256_file
from .model import AnswerKeyEntry, IngestError, Issue, Locator, ParseResult
from .normalize import compare_key
from .readers import read
from .service import _insert_parsed, _insert_run, import_reference_document
from .tabular import _KEY, _NUMBER
from .textparse import parse_text_units


def parse_answer_key(path: Path, *, known_items=None):
    kind = detect_kind(Path(path))
    output = read(Path(path), kind)
    res = ParseResult(kind, raw_units=output.units, issues=list(output.issues))
    if kind == "json":
        data = output.data
        if (
            not isinstance(data, dict)
            or data.get("format") != "lai-answer-key"
            or data.get("version") != 1
            or not isinstance(data.get("answer_key"), list)
        ):
            raise IngestError(
                "key_format",
                "JSON cevap anahtarı lai-answer-key sürüm 1 ve answer_key listesi içermeli.",
            )
        for n, row in enumerate(data["answer_key"]):
            loc = Locator(json_path=f"$.answer_key[{n}]")
            if (
                not isinstance(row, dict)
                or set(row) != {"item", "value"}
                or type(row.get("item")) is not int
                or row["item"] < 1
                or not isinstance(row.get("value"), str)
                or not row["value"].strip()
            ):
                res.issues.append(
                    Issue(
                        "error",
                        "invalid_key",
                        "Anahtarda pozitif madde ve dolu metin değer gerekli.",
                        loc,
                    )
                )
                continue
            res.answer_keys.append(AnswerKeyEntry(n, row["item"], row["value"], loc))
    elif kind in ("csv", "xlsx"):
        maps = {}
        for unit in output.units:
            cells = list(unit.cells or ())
            scope = unit.locator.sheet
            if scope not in maps:
                num = [j for j, c in enumerate(cells) if compare_key(c) in _NUMBER]
                key = [j for j, c in enumerate(cells) if compare_key(c) in _KEY]
                if len(num) == len(key) == 1:
                    maps[scope] = (num[0], key[0])
                    continue
                if cells:
                    res.issues.append(
                        Issue(
                            "error",
                            "key_header",
                            "Soru No ve Cevap Anahtarı başlıkları tekil olmalı.",
                            unit.locator,
                        )
                    )
                continue
            ni, ki = maps[scope]
            if not any(c.strip() for c in cells):
                continue
            if (
                max(ni, ki) >= len(cells)
                or not re.fullmatch(r"[1-9]\d*", cells[ni].strip())
                or not cells[ki].strip()
            ):
                res.issues.append(
                    Issue(
                        "error",
                        "invalid_key",
                        "Madde numarası veya anahtar değeri geçersiz.",
                        unit.locator,
                    )
                )
                continue
            res.answer_keys.append(
                AnswerKeyEntry(
                    len(res.answer_keys), int(cells[ni]), cells[ki], unit.locator
                )
            )
    else:
        _, res.answer_keys, issues, _ = parse_text_units(output.units)
        res.issues.extend(issues)
    seen = set()
    for key in res.answer_keys:
        if key.item_number in seen:
            res.issues.append(
                Issue(
                    "error",
                    "duplicate_key",
                    f"{key.item_number}. madde anahtarda yineleniyor.",
                    key.locator,
                    item_number=key.item_number,
                )
            )
        if known_items is not None and key.item_number not in known_items:
            res.issues.append(
                Issue(
                    "error",
                    "unknown_item",
                    f"{key.item_number}. madde envanterde yok.",
                    key.locator,
                    item_number=key.item_number,
                )
            )
        seen.add(key.item_number)
    if not res.answer_keys:
        res.issues.append(
            Issue(
                "error",
                "no_keys",
                "Cevap anahtarı bulunamadı. PDF/DOCX içinde Cevap Anahtarı başlığı gerekli.",
            )
        )
    return res


def import_answer_key(store, inventory_id, path, *, documents_dir):
    path = Path(path)
    version = store.conn.execute(
        "SELECT id FROM inventory_versions WHERE inventory_id=? ORDER BY version DESC LIMIT 1",
        (inventory_id,),
    ).fetchone()
    if version is None:
        raise IngestError("no_inventory", "Önce envanter sorularını kaydedin.")
    parsed = parse_answer_key(
        path, known_items={r["number"] for r in store.inventory.items(version[0])}
    )
    if parsed.has_errors:
        raise IngestError(
            "invalid_key",
            "\n".join(i.message for i in parsed.issues if i.severity == "error"),
        )
    sha, _ = sha256_file(path)
    old = store.conn.execute(
        "SELECT id FROM import_runs WHERE inventory_id=? AND sha256=? AND parser_version='answer-key-1'",
        (inventory_id, sha),
    ).fetchone()
    if old:
        return {
            "run_id": old[0],
            "duplicate": True,
            "answer_key_count": len(parsed.answer_keys),
        }
    with store.transaction() as c:
        ref = import_reference_document(
            store, inventory_id, path, documents_dir=documents_dir
        )
        rid = _insert_run(
            c,
            inventory_id=inventory_id,
            document_id=ref.document_id,
            file_name=path.name,
            sha256=sha,
            kind=parsed.kind,
            parser_version="answer-key-1",
            status="imported",
            inventory_version_id=version[0],
            answer_key_count=len(parsed.answer_keys),
            meta=json.dumps({"role": "answer_key"}),
        )
        _insert_parsed(c, rid, parsed)
    return {
        "run_id": rid,
        "duplicate": False,
        "answer_key_count": len(parsed.answer_keys),
    }
