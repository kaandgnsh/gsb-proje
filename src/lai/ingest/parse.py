"""Dosya → `ParseResult` (veritabanı yok). Tür tespiti, okuma, ayrıştırma, denetimler."""
from __future__ import annotations

from pathlib import Path

from .checks import run_checks
from .detect import MAX_BYTES, detect_kind
from .jsonfmt import parse_json_document
from .model import ERROR, IngestError, Issue, ParseResult
from .readers import read
from .tabular import parse_table_units
from .textparse import parse_text_units


def _merge(parts):
    """Birden çok ayrıştırıcı çıktısını belgedeki sıraya göre birleştirir; `seq` ve sorunlardaki
    madde göndermeleri yeniden numaralanır."""
    items, keys, issues, meta = [], [], [], {}
    owner = {}
    for p_items, p_keys, p_issues, p_meta in parts:
        by_seq = {it.seq: it for it in p_items}
        for iss in p_issues:
            owner[id(iss)] = by_seq.get(iss.item_seq) if iss.item_seq is not None else None
        items += p_items
        keys += p_keys
        issues += p_issues
        meta.update(p_meta)
    items.sort(key=lambda it: (it.raw_first if it.raw_first is not None else -1))
    for i, it in enumerate(items):
        it.seq = i
    for iss in issues:
        it = owner.get(id(iss))
        if it is not None:
            iss.item_seq = it.seq
    for i, k in enumerate(keys):
        k.seq = i
    return items, keys, issues, meta


def parse_file(path: Path, *, kind: str | None = None, encoding: str | None = None,
               max_bytes: int = MAX_BYTES) -> ParseResult:
    path = Path(path)
    kind = detect_kind(path, declared=kind, max_bytes=max_bytes)
    out = read(path, kind, encoding=encoding)
    res = ParseResult(kind, raw_units=out.units, issues=list(out.issues), meta=dict(out.meta))
    if kind == "pdf":
        parts = [parse_text_units(out.units)]
    elif kind in ("xlsx", "csv"):
        parts = [parse_table_units(out.units)]
    elif kind == "json":
        parts = [parse_json_document(out.data)]
    else:  # docx: paragraflar serbest metin, tablolar tablo olarak
        paras = [u for u in out.units if u.cells is None]
        tables = [u for u in out.units if u.cells is not None]
        parts = [parse_text_units(paras)]
        if tables:
            try:
                parts.append(parse_table_units(tables))
            except IngestError:
                res.issues.append(Issue("info", "ignored_table",
                                        "Belgedeki tablolar soru tablosu olarak tanınmadı; "
                                        "ham içerikte duruyor."))
    items, keys, issues, meta = _merge(parts)
    res.items, res.answer_keys = items, keys
    res.issues += issues
    res.meta.update(meta)
    if not items:
        res.issues.append(Issue(ERROR, "no_items",
                                "Belgede soru bulunamadı. Sorular '1. …' biçiminde numaralı "
                                "olmalı (tablolarda 'Soru No' ve 'Soru' sütunları)."))
    res.issues += run_checks(items, keys)
    return res
