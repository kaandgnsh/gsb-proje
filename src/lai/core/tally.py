"""Bir boyut için kurallardan puan modeli kurma, puan sayma ve harf kararı. Saf Python.

Tahmin yok: eksik eşleme, cevapsız madde (politika yoksa), tanımsız seçenek ya da karar kuralı
yokluğu sonucu "hesaplanamadı" yapar ve nedeni açıkça yazılır.

Puan önceliği: option_weight > item_pole_map > default_option_map (reverse yalnız varsayılan
eşlemeye uygulanır: iki kutup yer değiştirir).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuleRef:
    key: str
    rule_type: str
    body: dict


@dataclass
class DimensionModel:
    dimension: str
    poles: tuple[str, ...] | None
    items: dict[int, dict[str, dict[str, float]]] = field(default_factory=dict)
    item_keys: dict[int, set[str]] = field(default_factory=dict)
    def_key: str | None = None
    decision: RuleRef | None = None
    tie_break: RuleRef | None = None
    missing_policy: RuleRef | None = None
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)   # bu boyuta ait tüm kural anahtarları

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass
class Tally:
    points: dict[str, float]
    status: str            # ok | not_evaluable
    note: str | None
    used: set[str]


def _for_dim(dimension: str, rules: list[RuleRef]) -> list[RuleRef]:
    own = [r for r in rules if r.body.get("dimension") == dimension]
    if not any(r.rule_type == "missing_answer_policy" for r in own):
        own += [r for r in rules if r.rule_type == "missing_answer_policy"
                and r.body.get("dimension") == "*"]
    return own


def build_model(dimension: str, rules: list[RuleRef]) -> DimensionModel:
    rs = _for_dim(dimension, rules)
    by = {}
    for r in rs:
        by.setdefault(r.rule_type, []).append(r)
    one = {t: v[0] for t, v in by.items()}
    m = DimensionModel(dimension, None, keys={r.key for r in rs})
    if "dimension_def" in one:
        m.poles = tuple(one["dimension_def"].body["poles"])
        m.def_key = one["dimension_def"].key
    m.decision = one.get("threshold")
    m.tie_break = one.get("tie_break")
    m.missing_policy = one.get("missing_answer_policy")
    dmap = one.get("default_option_map")
    members = one.get("dimension_items")
    weights = {r.body["item"]: r for r in by.get("option_weight", [])}
    pmaps = {r.body["item"]: r for r in by.get("item_pole_map", [])}
    revs = {r.body["item"]: r for r in by.get("reverse", [])}
    member_set = set(members.body["items"]) if members else set()
    items = sorted(member_set | set(weights) | set(pmaps) | set(revs))
    if not items:
        m.missing.append(f"{dimension} boyutuna ait madde bilinmiyor")
    flip = None
    if dmap is not None:
        vals = sorted(set(dmap.body["options"].values()))
        if len(vals) == 2:
            flip = {vals[0]: vals[1], vals[1]: vals[0]}
    for i in items:
        keys = {members.key} if i in member_set else set()
        if i in weights:
            m.items[i] = {c: dict(p) for c, p in weights[i].body["weights"].items()}
            keys.add(weights[i].key)
        elif i in pmaps:
            m.items[i] = {c: {p: 1} for c, p in pmaps[i].body["options"].items()}
            keys.add(pmaps[i].key)
        elif dmap is not None:
            opts = dmap.body["options"]
            if i in revs:
                if flip is None:
                    m.missing.append(f"madde {i}: ters kuralı uygulanamıyor (varsayılan eşleme iki "
                                     f"kutuplu değil)")
                    continue
                opts = {c: flip[p] for c, p in opts.items()}
                keys.add(revs[i].key)
            m.items[i] = {c: {p: 1} for c, p in opts.items()}
            keys.add(dmap.key)
        else:
            m.missing.append(f"madde {i}: seçenek → kutup eşlemesi yok")
            continue
        if i in revs and revs[i].key not in keys:
            m.notes.append(f"madde {i}: açık eşleme olduğu için ters kuralı kullanılmadı")
        m.item_keys[i] = keys
    found = {p for opts in m.items.values() for pts in opts.values() for p in pts}
    if m.poles is None:
        if len(found) == 2:
            m.poles = tuple(sorted(found))
    else:
        extra = found - set(m.poles)
        if extra:
            m.missing.append(f"eşlemedeki kutup(lar) {sorted(extra)} boyut tanımında yok "
                             f"{list(m.poles)}")
    return m


def score(m: DimensionModel, answers: dict[int, str | None]) -> Tally:
    poles = m.poles or tuple(sorted({p for o in m.items.values() for w in o.values() for p in w}))
    pts = {p: 0 for p in poles}
    used: set[str] = set()
    pol = m.missing_policy
    for i in sorted(m.items):
        a = answers.get(i)
        if a is None:
            if pol is None:
                return Tally(pts, "not_evaluable", f"madde {i} cevapsız ve eksik cevap politikası yok",
                             used)
            used.add(pol.key)
            kind = pol.body["policy"]
            if kind == "skip":
                continue
            if kind == "count_as_pole":
                pts[pol.body["pole"]] = pts.get(pol.body["pole"], 0) + 1
                continue
            return Tally(pts, "not_evaluable", f"madde {i} cevapsız; politika: geçersiz say", used)
        opt = m.items[i].get(a)
        if opt is None:
            return Tally(pts, "not_evaluable", f"madde {i}: '{a}' seçeneği eşlemede yok", used)
        for p, v in opt.items():
            pts[p] = pts.get(p, 0) + v
        used |= m.item_keys[i]
    return Tally(pts, "ok", None, used)


def decide(m: DimensionModel, points: dict[str, float]) -> tuple[str | None, set[str], str | None]:
    """Harf kararı: (harf ya da None, kullanılan kural anahtarları, açıklama)."""
    d = m.decision
    if d is None:
        return None, set(), "karar (eşik) kuralı yok"
    poles = m.poles
    if not poles or len(poles) != 2 or any(p not in points for p in poles):
        return None, set(), "iki kutbun puanı yok"
    p1, p2 = poles
    if d.body["method"] == "min_points":
        pole = d.body["pole"]
        other = p2 if pole == p1 else p1
        return (pole if points[pole] >= d.body["value"] else other), {d.key}, None
    if points[p1] == points[p2]:
        if m.tie_break is None:
            return None, set(), "puanlar eşit ve beraberlik kuralı yok"
        return m.tie_break.body["pole"], {m.tie_break.key}, None
    return (p1 if points[p1] > points[p2] else p2), {d.key}, None
