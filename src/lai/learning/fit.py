"""Kural adaylarını örnek sonuçlara karşı deterministik olarak sınar ve durum atar.

Sınamalar (her boyut için):
  a) karar: eşik + beraberlik kuralı, örnekte BİLDİRİLEN puanlara uygulanıp harfle karşılaştırılır;
  b) puan kümesi: üyelik + eşlemeler + ters + ağırlık + eksik cevap politikası ile hesaplanan puan
     bildirilen puanla (puan yoksa ve karar kuralı tutarsız bulunmadıysa harfle) karşılaştırılır;
  c) boyut tanımı: örnek harfleri ve puan anahtarları tanımlı kutuplarda mı.
Bir kural katıldığı sınamalardan birinde tutarsızsa `inconsistent`; değilse ve en az birinde
tutarlıysa `consistent`; hiç sınanamadıysa `not_testable`. Puan kümesi tutarsızsa kümedeki tüm
katılımcı kurallar tutarsız sayılır: hangi kuralın hatalı olduğu TAHMİN EDİLMEZ.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from lai.core.rules import RULE_TYPES
from lai.core.tally import RuleRef, build_model, decide, score
from lai.store.enums import Confidence, Source

from .examples import Example, load_examples

MAX_SHOWN = 5


@dataclass
class _Test:
    name: str
    matched: int = 0
    mismatched: int = 0
    skipped: int = 0
    mismatches: list = field(default_factory=list)
    skips: list = field(default_factory=list)

    def as_dict(self):
        return {"test": self.name, "matched": self.matched, "mismatched": self.mismatched,
                "skipped": self.skipped, "mismatches": self.mismatches[:MAX_SHOWN],
                "skips": self.skips[:MAX_SHOWN]}


@dataclass
class RuleFit:
    outcome: str
    detail: dict


def _letter(oc):
    return oc.get("letter")


def evaluate(rules: list[RuleRef], examples: list[Example]) -> dict[str, RuleFit]:
    part: dict[str, list[tuple[_Test, bool]]] = {r.key: [] for r in rules}
    extra: dict[str, dict] = {r.key: {} for r in rules}
    dims = sorted({r.body["dimension"] for r in rules if r.body.get("dimension") != "*"})
    for d in dims:
        m = build_model(d, rules)
        exd = [(e, e.outcome[d]) for e in examples if d in e.outcome]
        for k in m.keys:
            if m.missing:
                extra[k].setdefault("missing", m.missing[:20])
            if m.notes:
                extra[k].setdefault("notes", m.notes[:20])
        # c) boyut tanımı
        if m.def_key and exd:
            t = _Test("poles")
            for e, oc in exd:
                bad = [x for x in ([_letter(oc)] if _letter(oc) else []) + list(oc.get("scores", {}))
                       if x not in m.poles]
                if bad:
                    t.mismatched += 1
                    t.mismatches.append({"example": e.key, "unknown_poles": bad})
                else:
                    t.matched += 1
            part[m.def_key].append((t, t.mismatched == 0))
        # a) karar kuralları, bildirilen puanlarla
        decision_bad = False
        if m.decision is not None:
            per_key: dict[str, _Test] = {}
            for e, oc in exd:
                sc = oc.get("scores")
                if not (_letter(oc) and sc):
                    continue
                pred, used, why = decide(m, sc)
                if pred is None:
                    t = per_key.setdefault(m.decision.key, _Test("decision"))
                    t.skipped += 1
                    t.skips.append({"example": e.key, "reason": why})
                    continue
                for k in used:
                    t = per_key.setdefault(k, _Test("decision"))
                    if pred == oc["letter"]:
                        t.matched += 1
                    else:
                        t.mismatched += 1
                        t.mismatches.append({"example": e.key, "scores": sc,
                                             "expected": oc["letter"], "computed": pred})
            for k, t in per_key.items():
                if t.matched or t.mismatched:
                    part[k].append((t, t.mismatched == 0))
                    decision_bad |= t.mismatched > 0 and k == m.decision.key
        # b) puan kümesi
        if m.complete and m.items:
            t = _Test("scores")
            participants: set[str] = set()
            for e, oc in exd:
                ta = score(m, e.answers)
                if ta.status != "ok":
                    t.skipped += 1
                    t.skips.append({"example": e.key, "reason": ta.note})
                    continue
                if "scores" in oc:
                    rep = oc["scores"]
                    ok = all(abs(ta.points.get(p, 0) - v) < 1e-9 for p, v in rep.items())
                    detail = {"expected": rep, "computed": ta.points}
                    used = ta.used
                elif m.decision is not None and not decision_bad:
                    pred, dused, why = decide(m, ta.points)
                    if pred is None:
                        t.skipped += 1
                        t.skips.append({"example": e.key, "reason": why})
                        continue
                    ok = pred == oc["letter"]
                    detail = {"expected": oc["letter"], "computed": pred, "points": ta.points}
                    used = ta.used | dused
                    t.name = "scores+letters"
                else:
                    t.skipped += 1
                    t.skips.append({"example": e.key, "reason": "puan yok ve güvenilir karar "
                                                                "kuralı yok"})
                    continue
                participants |= used
                if ok:
                    t.matched += 1
                else:
                    t.mismatched += 1
                    t.mismatches.append({"example": e.key, **detail})
            if t.matched or t.mismatched:
                for k in participants:
                    part[k].append((t, t.mismatched == 0))
            elif t.skipped:
                for k in m.keys:
                    extra[k].setdefault("skips", t.skips[:MAX_SHOWN])
    out = {}
    for r in rules:
        tests = part[r.key]
        if any(not ok for _, ok in tests):
            oc = "inconsistent"
        elif tests:
            oc = "consistent"
        else:
            oc = "not_testable"
        detail = {"tests": [t.as_dict() for t, _ in tests], **extra[r.key]}
        if r.rule_type == "normalization":
            detail["note"] = "normalleştirme kuralı bu aşamada örneklerle sınanmaz"
        if not examples:
            detail["note"] = "örnek sonuç yok"
        out[r.key] = RuleFit(oc, detail)
    return out


# ------------------------------------------------------------------ veritabanı
_TARGET = {"consistent": (Confidence.STRONGLY_INFERRED, Source.EXAMPLE_FIT),
           "inconsistent": (Confidence.CONTRADICTORY, Source.EXAMPLE_FIT),
           "not_testable": (Confidence.WEAKLY_INFERRED, Source.AI_INFERENCE)}
_ADJUSTABLE = (Source.AI_INFERENCE, Source.EXAMPLE_FIT)


@dataclass
class FitReport:
    fit_run_id: int
    example_count: int
    outcomes: dict[str, str]
    changed: list[tuple[str, str, str]]


def _reason(fit: RuleFit) -> str:
    m = sum(t["matched"] for t in fit.detail["tests"])
    x = sum(t["mismatched"] for t in fit.detail["tests"])
    if fit.outcome == "consistent":
        return f"örnek uyumu: {m} örnekle tutarlı, çelişen yok"
    if fit.outcome == "inconsistent":
        ex = [mm.get("example") for t in fit.detail["tests"] for mm in t["mismatches"]][:3]
        return (f"örnek uyumu: {x} örnekte çelişki (ör. {', '.join(ex)}); kural kümesi örneklerle "
                f"tutarsız — hatalı kural bu küme içinde, hangisi olduğu belirlenmedi")
    why = fit.detail.get("note") or "; ".join(fit.detail.get("missing", [])[:2]) or \
        "örneklerle sınanabilecek hesap yok"
    return f"örneklerle sınanamadı: {why}"


def current_rules(store, inventory_id: int):
    """Uyum ve keşifte kullanılan güncel kurallar: reddedilmemiş, çelişki işaretsiz."""
    out = []
    for e in store.knowledge.current(inventory_id, kind="rule"):
        body = e.content.get("body")
        if e.validation == "rejected" or e.rule_type not in RULE_TYPES or not isinstance(body, dict):
            continue
        out.append((e, body))
    return out


def run_fit(store, inventory_id: int, *, discovery_run_id: int | None = None) -> FitReport:
    examples, evs = load_examples(store, inventory_id)
    entries = current_rules(store, inventory_id)
    usable = [(e, b) for e, b in entries if not e.content.get("conflict")]
    fits = evaluate([RuleRef(e.entry_key, e.rule_type, b) for e, b in usable], examples)
    changed, outcomes = [], {}
    with store.transaction() as c:
        fr = c.execute("INSERT INTO fit_runs(inventory_id, discovery_run_id, example_count) "
                       "VALUES (?,?,?)", (inventory_id, discovery_run_id, len(examples))).lastrowid
        for e, _ in entries:
            fit = fits.get(e.entry_key)
            oc = fit.outcome if fit else "conflict"
            outcomes[e.entry_key] = oc
            c.execute("INSERT INTO fit_results(fit_run_id, rule_key, entry_id, outcome, detail) "
                      "VALUES (?,?,?,?,?)", (fr, e.entry_key, e.id, oc,
                                             json.dumps(fit.detail if fit else {},
                                                        ensure_ascii=False)))
            if fit is None or e.source not in _ADJUSTABLE or e.confidence == Confidence.CONFIRMED:
                continue
            conf, src = _TARGET[oc]
            if (e.confidence, e.source) == (conf, src):
                continue
            store.knowledge.revise(e.id, confidence=conf, source=src, reason=_reason(fit),
                                   evidence_ids=evs if src == Source.EXAMPLE_FIT else (),
                                   created_by="örnek uyumu")
            changed.append((e.entry_key, str(e.confidence), str(conf)))
    return FitReport(fr, len(examples), outcomes, changed)
