"""Deterministik, sürümlü ve izlenebilir puanlama. AI/Qt/depo/ağ bağımlılığı yok.

Decimal bağlamı: 50 anlamlı basamak, HALF_EVEN; dış bağlamdan bağımsız.
Sıra: madde katkısı → toplam/ortalama → ağırlık → normalizasyon → karar → gösterim.
Sınıflandırma gösterimde yuvarlanmış puanı kullanmaz. Serbest formül yürütülmez.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from decimal import (
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    localcontext,
)

from .rules import rule_key, validate_rule
from .tally import RuleRef, build_model, decide

ENGINE_VERSION = "m7.1"
_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN, Emin=-999, Emax=999)
_BLOCKED = {"UNKNOWN", "CONTRADICTORY", "REJECTED", "DEFERRED"}
_INFERRED = {"STRONGLY_INFERRED", "WEAKLY_INFERRED"}


class ScoringInputError(ValueError):
    """Bozuk sınav/profil/cevap zarfı; boyut eksikliğinden ayrı bir girdi hatası."""


@dataclass(frozen=True)
class ExamVersion:
    id: int
    inventory_id: int
    items: Mapping[int, Sequence[str]]
    dimension_order: tuple[str, ...]


@dataclass(frozen=True)
class ScoringProfile:
    id: int
    inventory_id: int
    inventory_version_id: int
    snapshot: dict
    provisional: bool = False


@dataclass
class DimensionResult:
    dimension: str
    status: str = "NOT_EVALUABLE"
    raw: dict[str, Decimal] | None = None
    final: dict[str, Decimal] | None = None
    displayed: dict[str, str] | None = None
    selected_pole: str | None = None
    contributions: list[dict] = field(default_factory=list)
    transformations: list[dict] = field(default_factory=list)
    decision: dict | None = None
    rules: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class ScoringResult:
    inventory_version_id: int
    profile_version_id: int
    dimensions: list[DimensionResult]
    status: str
    full_profile: str | None
    engine_version: str = ENGINE_VERSION

    def to_dict(self):
        """JSON güvenli, kayıpsız ondalık metinler; zaman/rastgele kimlik yok."""

        def convert(v):
            if isinstance(v, Decimal):
                return format(v, "f")
            if isinstance(v, dict):
                return {k: convert(x) for k, x in v.items()}
            if isinstance(v, list):
                return [convert(x) for x in v]
            return v

        return convert(asdict(self))


def _number(v):
    if isinstance(v, bool) or not isinstance(v, (int, float, str, Decimal)):
        raise ValueError("Sonlu sayısal değer gerekli")  # noqa: TRY004 — tek kural hatası sözleşmesi
    try:
        d = Decimal(str(v))
    except (ValueError, DecimalException):
        raise ValueError("Geçersiz ondalık değer") from None
    if (
        not d.is_finite()
        or len(d.as_tuple().digits) > 24
        or not -12 <= d.as_tuple().exponent <= 12
    ):
        raise ValueError("Sayı sonlu, en çok 24 basamaklı ve üssü -12..12 olmalı")
    return d


def _responses(exam, responses):
    if isinstance(responses, Mapping):
        pairs = list(responses.items())
    elif isinstance(responses, (list, tuple)):
        pairs = responses
    else:
        raise ScoringInputError(
            "Cevaplar madde/seçenek eşlemesi veya çiftler listesi olmalı"
        )
    out = {}
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ScoringInputError("Her cevap madde numarası ve seçenek çiftidir")
        n, a = pair
        if type(n) is not int or n not in exam.items:
            raise ScoringInputError(f"Geçersiz veya sınavda bulunmayan madde: {n!r}")
        if n in out:
            raise ScoringInputError(f"{n}. madde için yinelenen cevap")
        if a is not None and (not isinstance(a, str) or a not in exam.items[n]):
            raise ScoringInputError(
                f"{n}. madde için bilinmeyen veya geçersiz seçenek: {a!r}"
            )
        out[n] = a
    return out


def _runtime_errors(t, b):
    errors = validate_rule(t, b)
    if errors:
        return errors
    if t == "normalization":
        method, p = b["method"], b["parameters"]
        required = {
            "none": set(),
            "percentage": {"denominator"},
            "scaled": {"factor", "offset"},
        }
        if method not in required:
            return [
                "Normalizasyon yöntemi yürütülemiyor; serbest formül desteklenmiyor"
            ]
        if set(p) != required[method]:
            return [
                f"Normalizasyon {method} parametreleri açıkça {sorted(required[method])} olmalı"
            ]
        try:
            for key, value in p.items():
                number = _number(value)
                if key == "denominator" and number <= 0:
                    return ["Normalizasyon paydası sıfırdan büyük olmalı"]
        except ValueError as e:
            return [str(e)]
    try:
        if t == "option_weight":
            for weights in b["weights"].values():
                for value in weights.values():
                    _number(value)
        if t == "dimension_weight" or (t == "threshold" and "value" in b):
            _number(b["value"])
    except ValueError as e:
        return [str(e)]
    return []


def _scope(requirement, dimensions):
    scope = requirement.get("dimensions")
    if not scope:
        scope = [requirement.get("dimension_code") or "*"]
    return dimensions if "*" in scope else scope


def _dimension(exam, profile, dim, entries, requirements, answers):
    result = DimensionResult(dim)
    local = [
        e
        for e in entries
        if e.get("content", {}).get("body", {}).get("dimension") in (dim, "*")
    ]
    if any(
        e.get("rule_type") == "missing_answer_policy"
        and e["content"]["body"]["dimension"] == dim
        for e in local
    ):
        local = [e for e in local if e["content"]["body"]["dimension"] != "*"]
    refs = []
    provisional = False
    keys = set()
    for e in local:
        t = e.get("rule_type")
        b = e.get("content", {}).get("body")
        result.rules.append(
            {
                k: deepcopy(e.get(k))
                for k in (
                    "rule_type",
                    "content",
                    "entry_id",
                    "entry_key",
                    "version",
                    "confidence",
                    "source",
                    "validation",
                    "confirmation_ref",
                    "evidence_ids",
                )
            }
        )
        errors = _runtime_errors(t, b)
        if errors:
            result.limitations.extend(errors)
            continue
        key = rule_key(t, b)
        if key in keys:
            result.limitations.append(
                f"{dim}: aynı anahtarda birden fazla kural: {key}"
            )
        keys.add(key)
        if e.get("source") not in (
            "user",
            "document",
            "ai_inference",
            "example_fit",
            "assessment_feedback",
        ):
            result.limitations.append(f"{dim}: geçersiz bilgi kaynağı")
        if e.get("validation") not in (None, "pending", "validated", "rejected"):
            result.limitations.append(f"{dim}: geçersiz doğrulama durumu")
        state = e.get("confidence", "UNKNOWN")
        if (
            state in _BLOCKED
            or e.get("validation") == "rejected"
            or e["content"].get("conflict")
        ):
            result.limitations.append(
                f"{dim}: {key} bilinmiyor, reddedilmiş veya çelişkili ({state})"
            )
        elif state in _INFERRED:
            provisional = True
        elif state != "CONFIRMED":
            result.limitations.append(f"{dim}: geçersiz bilgi durumu: {state}")
        elif e.get("source") not in ("user", "document"):
            result.limitations.append(
                f"{dim}: açık kullanıcı/belge kaynağı bulunmayan onay"
            )
        # Legacy snapshots have no validation/reference fields. They remain provisional.
        if "validation" not in e or "confirmation_ref" not in e:
            provisional = True
        elif state == "CONFIRMED" and (
            e["validation"] != "validated"
            or not isinstance(e["confirmation_ref"], str)
            or not e["confirmation_ref"]
        ):
            result.limitations.append(f"{dim}: kaynak doğrulaması eksik")
        refs.append(RuleRef(key, t, b))
    for req in requirements:
        if (
            dim not in _scope(req, exam.dimension_order)
            or req.get("status") == "CONFIRMED"
        ):
            continue
        state = req.get("status", "UNKNOWN")
        if req.get("critical", True) and state not in _INFERRED:
            result.limitations.append(
                f"{dim}: kritik bilgi eksik/çelişkili: {req.get('req_key')} ({state})"
            )
        else:
            provisional = True
    by = {r.rule_type: r for r in refs if r.body["dimension"] == dim}
    for fact in profile.snapshot.get("learning_facts", []):
        key = fact.get("entry_key", "").split(":")
        if len(key) == 3 and key[0] == "fact" and key[2] in (dim, "*"):
            needed = {
                "normalization": "normalization",
                "dimension_weight": "dimension_weight",
                "zero_policy": "zero_score",
            }.get(key[1])
            if needed and needed not in by:
                result.limitations.append(
                    f"{dim}: {needed} yalnız serbest metin olarak biliniyor; yapılandırılmış kural gerekli"
                )
    for t in ("dimension_def", "dimension_items", "threshold"):
        if t not in by:
            result.limitations.append(f"{dim}: kritik {t} kuralı eksik")
    if result.limitations:
        return result
    members = by["dimension_items"].body["items"]
    poles = by["dimension_def"].body["poles"]
    for r in refs:
        b = r.body
        ns = [b["item"]] if "item" in b else b.get("items", [])
        if any(n not in exam.items for n in ns):
            result.limitations.append(f"{dim}: kural sınavda bulunmayan madde içeriyor")
        if "item" in b and b["item"] not in members:
            result.limitations.append(f"{dim}: {b['item']}. madde boyut üyeliğinde yok")
        used_poles = set()
        if r.rule_type in ("item_pole_map", "default_option_map"):
            used_poles = set(b["options"].values())
        if r.rule_type == "option_weight":
            used_poles = {p for ps in b["weights"].values() for p in ps}
        if "pole" in b:
            used_poles.add(b["pole"])
        if used_poles - set(poles):
            result.limitations.append(f"{dim}: tanımsız kutup kullanılmış")
        options = b.get("options", b.get("weights"))
        affected = [b["item"]] if "item" in b else members
        if options is not None:
            for n in affected:
                if n in exam.items and set(options) - set(exam.items[n]):
                    result.limitations.append(
                        f"{dim}: {n}. madde kuralında bilinmeyen seçenek"
                    )
    model = build_model(dim, refs)
    result.limitations.extend(model.missing)
    for n in members:
        if n in exam.items and set(exam.items[n]) - set(model.items.get(n, {})):
            result.limitations.append(f"{dim}: {n}. maddenin seçenek katkıları eksik")
    if result.limitations:
        return result
    points = {p: Decimal(0) for p in poles}
    count = 0
    for n in sorted(members):
        a = answers.get(n)
        contribution = {p: Decimal(0) for p in poles}
        used = sorted(model.item_keys.get(n, set()))
        transforms = []
        if a is None:
            policy = model.missing_policy
            if policy is None or policy.body["policy"] == "invalidate":
                result.limitations.append(
                    f"{dim}: {n}. madde cevapsız; eksik cevap politikası yok veya geçersiz sayıyor"
                )
                return result
            used = [by["dimension_items"].key, policy.key]
            transforms.append(policy.body["policy"])
            if policy.body["policy"] == "count_as_pole":
                contribution[policy.body["pole"]] = Decimal(1)
                count += 1
        else:
            for p, v in model.items[n][a].items():
                contribution[p] = _number(v)
            count += 1
            if any(
                r.rule_type == "reverse" and r.body["item"] == n and r.key in used
                for r in refs
            ):
                transforms.append("reverse: kutuplar yer değiştirdi")
            elif any(r.rule_type == "reverse" and r.body["item"] == n for r in refs):
                transforms.append(
                    "reverse uygulanmadı: açık madde eşlemesi/ağırlığı öncelikli"
                )
        for p in poles:
            points[p] += contribution[p]
        result.contributions.append(
            {
                "item": n,
                "answer": a,
                "points": contribution,
                "rules": used,
                "transformations": transforms,
            }
        )
    result.raw = dict(points)
    method = by["aggregation"].body["method"] if "aggregation" in by else "sum"
    if method == "mean":
        if count == 0:
            result.raw = None
            result.limitations.append(f"{dim}: ortalama için katkı veren cevap yok")
            return result
        points = {p: v / Decimal(count) for p, v in points.items()}
    result.transformations.append(
        {
            "type": "aggregation",
            "method": method,
            "count": count,
            "points": dict(points),
        }
    )
    weight = (
        _number(by["dimension_weight"].body["value"])
        if "dimension_weight" in by
        else Decimal(1)
    )
    points = {p: v * weight for p, v in points.items()}
    result.transformations.append(
        {"type": "dimension_weight", "value": weight, "points": dict(points)}
    )
    norm = by.get("normalization")
    if norm:
        b = norm.body
        params = b["parameters"]
        if b["method"] == "percentage":
            points = {
                p: v / _number(params["denominator"]) * Decimal(100)
                for p, v in points.items()
            }
        elif b["method"] == "scaled":
            points = {
                p: v * _number(params["factor"]) + _number(params["offset"])
                for p, v in points.items()
            }
        result.transformations.append(
            {"type": "normalization", "body": deepcopy(b), "points": dict(points)}
        )
    # Reuse M5 decisions, replacing its numeric threshold with Decimal before comparison.
    if model.decision.body["method"] == "min_points":
        model.decision = RuleRef(
            model.decision.key,
            "threshold",
            dict(model.decision.body, value=_number(model.decision.body["value"])),
        )
    zero = by.get("zero_score")
    if (
        all(v == 0 for v in points.values())
        and zero
        and zero.body["policy"] != "use_threshold"
    ):
        selected = zero.body.get("pole")
        used = {zero.key}
        why = (
            "Sıfır puan politikası boyutu geçersiz sayıyor"
            if selected is None
            else None
        )
    else:
        selected, used, why = decide(model, points)
    if why:
        result.raw = None
        result.limitations.append(f"{dim}: {why}")
        return result
    result.final = points
    result.selected_pole = selected
    result.decision = {
        "rules": sorted(used),
        "threshold": deepcopy(by["threshold"].body),
        "selected_pole": selected,
        "profile_version_id": profile.id,
        "unrounded": True,
    }
    precision = by.get("precision")
    places = precision.body["places"] if precision else 2
    rounding = precision.body["rounding"] if precision else "half_even"
    quantum = Decimal(1).scaleb(-places)
    result.displayed = {
        p: format(
            v.quantize(
                quantum,
                rounding=ROUND_HALF_UP if rounding == "half_up" else ROUND_HALF_EVEN,
            ),
            "f",
        )
        for p, v in points.items()
    }
    result.transformations.append(
        {
            "type": "display_rounding",
            "places": places,
            "rounding": rounding,
            "affects_decision": False,
        }
    )
    result.status = "PROVISIONAL" if provisional else "VERIFIED"
    return result


def score_assessment(
    exam: ExamVersion, profile: ScoringProfile, responses
) -> ScoringResult:
    """M8 ortak saf giriş noktası. Bozuk cevap zarfı exception; boyut eksikleri sonuçta."""
    if not isinstance(exam, ExamVersion) or not isinstance(profile, ScoringProfile):
        raise ScoringInputError("Sınav ve puanlama profili sürümleri gerekli")
    if any(
        type(n) is not int or n < 1
        for n in (
            exam.id,
            exam.inventory_id,
            profile.id,
            profile.inventory_id,
            profile.inventory_version_id,
        )
    ):
        raise ScoringInputError("Sürüm ve envanter kimlikleri pozitif tamsayı olmalı")
    if (
        exam.inventory_id != profile.inventory_id
        or exam.id != profile.inventory_version_id
    ):
        raise ScoringInputError("Sınav sürümü ve puanlama profili sürümü uyuşmuyor")
    if (
        not isinstance(exam.items, Mapping)
        or not isinstance(exam.dimension_order, (list, tuple))
        or any(not isinstance(d, str) or not d for d in exam.dimension_order)
    ):
        raise ScoringInputError("Sınav maddeleri ve boyut sırası geçersiz")
    if (
        not exam.items
        or not exam.dimension_order
        or len(set(exam.dimension_order)) != len(exam.dimension_order)
    ):
        raise ScoringInputError("Sınav maddeleri ve tekil boyut sırası gerekli")
    for n, options in exam.items.items():
        if (
            type(n) is not int
            or n < 1
            or not isinstance(options, (list, tuple))
            or not options
            or any(not isinstance(a, str) or not a for a in options)
            or len(set(options)) != len(options)
        ):
            raise ScoringInputError("Sınavda geçersiz madde veya seçenek listesi")
    answers = _responses(exam, responses)
    snapshot = profile.snapshot
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("rules"), list):
        raise ScoringInputError("Puanlama profili kural listesi geçersiz")
    if (
        snapshot.get("inventory_id", profile.inventory_id) != profile.inventory_id
        or snapshot.get("inventory_version_id", profile.inventory_version_id)
        != profile.inventory_version_id
    ):
        raise ScoringInputError("Profil snapshot sürüm bağı uyuşmuyor")
    if "dimension_order" in snapshot and not isinstance(
        snapshot["dimension_order"], (list, tuple)
    ):
        raise ScoringInputError("Profil boyut sırası liste olmalı")
    if "dimension_order" in snapshot and tuple(snapshot["dimension_order"]) != tuple(
        exam.dimension_order
    ):
        raise ScoringInputError("Profil ve sınav boyut sırası uyuşmuyor")
    catalog = snapshot.get("catalog_version", 2)
    if type(catalog) is not int or catalog not in (1, 2):
        raise ScoringInputError("Puanlama kural kataloğu sürümü desteklenmiyor")
    entries = snapshot["rules"]
    for e in entries:
        if (
            not isinstance(e, dict)
            or not isinstance(e.get("content"), dict)
            or not isinstance(e["content"].get("body"), dict)
        ):
            raise ScoringInputError("Puanlama profilinde bozuk kural gövdesi")
        if not isinstance(e.get("confidence", "UNKNOWN"), str):
            raise ScoringInputError("Kuralın bilgi durumu metin olmalı")
        dim = e["content"]["body"].get("dimension")
        if dim not in (*exam.dimension_order, "*"):
            raise ScoringInputError(
                f"Profil kuralında sınavda bulunmayan boyut: {dim!r}"
            )
        if dim == "*" and e.get("rule_type") != "missing_answer_policy":
            raise ScoringInputError(
                "Yalnız eksik cevap politikası bütün boyutlara uygulanabilir"
            )
    for name in (
        "requirements",
        "unconfirmed_critical",
        "dimension_reviews",
        "learning_facts",
    ):
        if name in snapshot and not isinstance(snapshot[name], list):
            raise ScoringInputError(f"Profil {name} alanı liste olmalı")
    requirements = list(snapshot.get("requirements", []))
    for req in requirements:
        if (
            not isinstance(req, dict)
            or not isinstance(req.get("req_key"), str)
            or not isinstance(req.get("status"), str)
            or type(req.get("critical", True)) is not bool
        ):
            raise ScoringInputError("Profil gereksinimi geçersiz")
        scope = req.get("dimensions", [req.get("dimension_code") or "*"])
        if (
            not isinstance(scope, list)
            or not scope
            or any(d not in (*exam.dimension_order, "*") for d in scope)
        ):
            raise ScoringInputError("Gereksinimin boyut kapsamı geçersiz")
    for fact in snapshot.get("learning_facts", []):
        if not isinstance(fact, dict) or not isinstance(fact.get("entry_key"), str):
            raise ScoringInputError("Profil bilgi kaydı geçersiz")
    for review in snapshot.get("dimension_reviews", []):
        if not isinstance(review, dict) or review.get("dimension_code") not in (
            *exam.dimension_order,
            "*",
        ):
            raise ScoringInputError("Boyut incelemesi geçersiz")
    known_requirements = {r.get("req_key") for r in requirements}
    if snapshot.get("unconfirmed_critical"):
        for key in snapshot.get("unconfirmed_critical", []):
            if not isinstance(key, str):
                raise ScoringInputError("Kritik gereksinim anahtarı geçersiz")
            if key in known_requirements:
                continue
            pieces = key.split(":")
            dim = pieces[2] if key.startswith("m6:") and len(pieces) > 2 else "*"
            if dim not in exam.dimension_order:
                dim = "*"
            requirements.append(
                {
                    "req_key": key,
                    "dimensions": [dim],
                    "status": "UNKNOWN",
                    "critical": True,
                }
            )
    assigned = {
        n
        for e in entries
        if e.get("rule_type") == "dimension_items"
        for n in (
            e["content"]["body"].get("items")
            if isinstance(e["content"]["body"].get("items"), list)
            else []
        )
        if type(n) is int
    }
    if set(exam.items) - assigned:
        requirements.append(
            {
                "req_key": "Boyutu bilinmeyen sınav maddeleri",
                "dimensions": ["*"],
                "status": "UNKNOWN",
                "critical": True,
            }
        )
    for review in snapshot.get("dimension_reviews", []):
        requirements.append(
            {
                "req_key": "örnek çelişkisi",
                "dimensions": [review["dimension_code"]],
                "status": "CONTRADICTORY",
                "critical": True,
            }
        )
    with localcontext(_CONTEXT):
        out = []
        for dim in exam.dimension_order:
            try:
                out.append(
                    _dimension(exam, profile, dim, entries, requirements, answers)
                )
            except (ValueError, DecimalException) as e:
                out.append(
                    DimensionResult(
                        dim, limitations=[f"{dim}: sayısal kural geçersiz: {e}"]
                    )
                )
    status = (
        "VERIFIED"
        if not profile.provisional and all(r.status == "VERIFIED" for r in out)
        else "PROVISIONAL"
    )
    letters = [r.selected_pole for r in out]
    full = (
        "".join(letters)
        if len(out) == 4 and all(isinstance(x, str) and len(x) == 1 for x in letters)
        else None
    )
    return ScoringResult(exam.id, profile.id, out, status, full)


def select_signed_profile(
    values: Sequence, *, axes: Sequence[tuple[str, str, str]]
) -> str:
    """Yalnız harf eşleme: (pozitif, negatif, sıfır) açıkça sağlanır; formül keşfetmez."""
    if (
        not isinstance(values, (list, tuple))
        or not isinstance(axes, (list, tuple))
        or len(values) != 4
        or len(axes) != 4
    ):
        raise ScoringInputError("Dört puan ve dört açık kutup/sıfır eşlemesi gerekli")
    out = []
    for value, axis in zip(values, axes, strict=True):
        if (
            not isinstance(axis, (list, tuple))
            or len(axis) != 3
            or any(not isinstance(p, str) or len(p) != 1 for p in axis)
            or axis[2] not in axis[:2]
            or axis[0] == axis[1]
        ):
            raise ScoringInputError("Geçersiz pozitif/negatif/sıfır kutup eşlemesi")
        try:
            d = _number(value)
        except ValueError as e:
            raise ScoringInputError(str(e)) from None
        out.append(axis[0] if d > 0 else axis[1] if d < 0 else axis[2])
    return "".join(out)
