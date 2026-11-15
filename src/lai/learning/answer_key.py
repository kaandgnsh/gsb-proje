"""Ayrı cevap anahtarı → kural önerileri. Deterministik; hiçbir öneri onaysız yazılmaz.

Anahtar değerlerinin biçimi otomatik tanınır ve her olası okuma için madde düzeyinde
kurallar üretilir:
  pairs        "A:E B:I", "A=E", "E/I" → seçenek → kutup eşlemesi doğrudan yazılı
  pole_first   "E" (iki seçenekli madde) → ilk seçenek bu kutup, diğeri karşı kutup
  pole_second  "E" (iki seçenekli madde) → ikinci seçenek bu kutup, diğeri karşı kutup
  option_pole  "A" (seçenek kodu) → anahtar seçeneği boyutun bir kutbuna, diğerleri karşı
               kutba; hangi kutup olduğu her boyut için yalnız örnek sonuçlarla belirlenir
Örnek sonuç varsa her okuma mevcut kurallarla birlikte örneklere karşı sınanır; yalnız
tutarlı okumalar önerilir. Örnek yoksa uygulanabilir okumalar seçenek olarak sorulur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lai.core.rules import rule_key
from lai.core.tally import RuleRef

from .discovery import _known_poles
from .examples import load_examples
from .fit import current_rules, evaluate

LABELS = {
    "pairs": "Anahtar her seçeneğin hangi kutba puan verdiğini doğrudan yazıyor.",
    "pole_first": "Anahtardaki harf maddenin ilk seçeneğinin kutbu; diğer seçenek karşı kutba puan verir.",
    "pole_second": "Anahtardaki harf maddenin ikinci seçeneğinin kutbu; diğer seçenek karşı kutba puan verir.",
    "option_pole": "Anahtardaki seçenek, maddenin boyutunda örneklerle belirlenen kutba puan verir; diğer seçenekler karşı kutba.",
}
_PAIR = re.compile(r"([^\s:=,;/>→]+)\s*(?:->|=>|[:=→>])\s*([^\s,;/]+)")
_MAPPING_TYPES = ("item_pole_map", "option_weight")


@dataclass
class Reading:
    code: str
    label: str
    rules: list[dict]
    outcome: str = "not_testable"
    detail: dict = field(default_factory=dict)


def key_rows(store, inventory_id):
    """Madde → anahtar değeri. Aynı maddeye çelişen değerler varsa None (yorum yapılmaz)."""
    values = {}
    for n, v in store.conn.execute(
        "SELECT k.item_number,k.value FROM parsed_answer_keys k JOIN import_runs r "
        "ON r.id=k.import_run_id WHERE r.inventory_id=?",
        (inventory_id,),
    ):
        if v is not None and str(v).strip():
            values.setdefault(n, set()).add(str(v).strip())
    if any(len(v) > 1 for v in values.values()):
        return None
    return {n: next(iter(v)) for n, v in sorted(values.items())}


def _options(store, inventory_id):
    row = store.conn.execute(
        "SELECT id FROM inventory_versions WHERE inventory_id=? ORDER BY version DESC LIMIT 1",
        (inventory_id,),
    ).fetchone()
    if row is None:
        return {}
    return {it["number"]: [c for c, _ in it["options"]] for it in store.inventory.items(row[0])}


def _match(value, choices):
    hits = [c for c in choices if c.upper() == value.upper()]
    return hits[0] if len(hits) == 1 else None


def _pole_dim(value, poles):
    hits = [(d, p) for d, ps in poles.items() for p in ps if p.upper() == value.upper()]
    return hits[0] if len(hits) == 1 else (None, None)


def _other(poles, dim, pole):
    return next(p for p in poles[dim] if p != pole)


def _map(n, dim, options):
    return {"rule_type": "item_pole_map", "body": {"item": n, "dimension": dim, "options": options}}


def _pairs(n, value, opts, poles):
    found = _PAIR.findall(value)
    if found and _PAIR.sub("", value).strip(" ,;/") == "":
        mapping, dims = {}, set()
        for x, y in found:
            code = _match(x, opts)
            dim, pole = _pole_dim(y, poles)
            if code is None or dim is None or code in mapping:
                return None
            mapping[code] = pole
            dims.add(dim)
    else:
        parts = [p.strip() for p in value.split("/")]
        if len(parts) < 2 or len(parts) != len(opts):
            return None
        hits = [_pole_dim(p, poles) for p in parts]
        dims = {d for d, _ in hits}
        if None in dims:
            return None
        mapping = {c: p for c, (_, p) in zip(opts, hits)}
    # Eksik eşleme sessizce kabul edilmez: her seçeneğin kutbu yazılı olmalı.
    if len(dims) != 1 or set(mapping) != set(opts):
        return None
    return _map(n, dims.pop(), mapping)


def _candidates(rows, opts, poles, members):
    out = {}
    pairs = [_pairs(n, v, opts.get(n, []), poles) for n, v in rows.items()]
    if all(pairs):
        out["pairs"] = pairs
    for code, first in (("pole_first", True), ("pole_second", False)):
        rules = []
        for n, v in rows.items():
            dim, pole = _pole_dim(v, poles)
            if dim is None or len(opts.get(n, [])) != 2:
                break
            a, b = opts[n] if first else list(reversed(opts[n]))
            rules.append(_map(n, dim, {a: pole, b: _other(poles, dim, pole)}))
        else:
            out[code] = rules
    dims = {}
    for n, v in rows.items():
        code = _match(v, opts.get(n, []))
        owner = [d for d, items in members.items() if n in items and d in poles]
        if code is None or len(owner) != 1 or len(opts[n]) < 2:
            break
        dims.setdefault(owner[0], []).append((n, code))
    else:
        if dims:
            out["option_pole"] = dims
    return out


def _option_pole_variants(dim, rows, opts, poles):
    for pole in poles[dim]:
        other = _other(poles, dim, pole)
        yield [
            _map(n, dim, {c: pole if c == code else other for c in opts[n]})
            for n, code in rows
        ]


def _fit(context, rules, examples):
    refs = context + [RuleRef(rule_key(r["rule_type"], r["body"]), r["rule_type"], r["body"])
                      for r in rules]
    own = {rule_key(r["rule_type"], r["body"]) for r in rules}
    fits = {k: f for k, f in evaluate(refs, examples).items() if k in own}
    outcomes = {f.outcome for f in fits.values()}
    tests = [t for f in fits.values() for t in f.detail.get("tests", [])]
    detail = {
        "matched": max((t["matched"] for t in tests), default=0),
        "mismatched": max((t["mismatched"] for t in tests), default=0),
    }
    if "inconsistent" in outcomes:
        return "inconsistent", detail
    if "consistent" in outcomes:
        return "consistent", detail
    return "not_testable", detail


def readings(store, inventory_id):
    """Uygulanabilir okumalar; örnek varsa her biri sınanmış olarak döner."""
    rows = key_rows(store, inventory_id)
    poles = _known_poles(store, inventory_id)
    opts = _options(store, inventory_id)
    if not rows or not poles or not opts or not set(rows) <= set(opts):
        return []
    entries = current_rules(store, inventory_id)
    members = {}
    for e, b in entries:
        if e.rule_type == "dimension_items" and not e.content.get("conflict"):
            members.setdefault(b["dimension"], set()).update(b["items"])
    context = [
        RuleRef(e.entry_key, e.rule_type, b)
        for e, b in entries
        if not e.content.get("conflict")
        and not (e.rule_type in _MAPPING_TYPES and b.get("item") in rows)
    ]
    examples, _ = load_examples(store, inventory_id)
    out = []
    for code, rules in _candidates(rows, opts, poles, members).items():
        if code != "option_pole":
            reading = Reading(code, LABELS[code], rules)
            if examples:
                reading.outcome, reading.detail = _fit(context, rules, examples)
            out.append(reading)
            continue
        if not examples:
            continue
        chosen, detail = [], {"dimensions": {}}
        for dim, dim_rows in sorted(rules.items()):
            ok = []
            for variant in _option_pole_variants(dim, dim_rows, opts, poles):
                outcome, d = _fit(context, variant, examples)
                if outcome == "consistent":
                    ok.append((variant, d))
            if len(ok) != 1:
                break
            chosen += ok[0][0]
            detail["dimensions"][dim] = ok[0][1]
        else:
            out.append(Reading(code, LABELS[code], chosen, "consistent", detail))
    return out


def covered(store, inventory_id):
    """Anahtardaki her maddenin onaylı madde düzeyi eşlemesi var mı?"""
    rows = key_rows(store, inventory_id) or {}
    confirmed = {
        b.get("item")
        for e, b in current_rules(store, inventory_id)
        if e.rule_type in _MAPPING_TYPES and e.confidence == "CONFIRMED"
    }
    return bool(rows) and set(rows) <= confirmed
