"""Puanlama kuralı kataloğu (v2): sabit tür kümesi, her tür için katı gövde şeması.

Kurallar veri olarak saklanır ve yalnız bu katalogdaki türler için yazılmış Python kodu
tarafından yorumlanır (DSL ya da kod çalıştırma yok). Katalog dışı tür reddedilir.
"""
from __future__ import annotations

import json

from lai_runtime.jsonschema_lite import validate

CATALOG_VERSION = 2

_DIM = {"type": "string", "minLength": 1, "maxLength": 20}
_POLE = {"type": "string", "minLength": 1, "maxLength": 10}
_ITEM = {"type": "integer", "minimum": 1, "maximum": 100000}
_CODE_MAP = {"type": "object"}  # kod → kutup; anahtarlar kodda denetlenir


def _obj(required, **props):
    return {"type": "object", "additionalProperties": False, "required": list(required),
            "properties": props}


SCHEMAS: dict[str, dict] = {
    "aggregation": _obj(("dimension", "method"), dimension=_DIM,
                        method={"type":"string", "enum":["sum","mean"]}),
    "dimension_weight": _obj(("dimension", "value"), dimension=_DIM,
                             value={"type":"number", "minimum":0, "maximum":1000}),
    "zero_score": _obj(("dimension", "policy"), dimension=_DIM,
                       policy={"type":"string", "enum":["select_pole","invalidate","use_threshold"]},
                       pole=_POLE),
    "precision": _obj(("dimension", "places", "rounding"), dimension=_DIM,
                      places={"type":"integer", "minimum":0,"maximum":12},
                      rounding={"type":"string","enum":["half_even","half_up"]}),
    "dimension_def": _obj(("dimension", "poles"), dimension=_DIM,
                          poles={"type": "array", "items": _POLE, "minItems": 2, "maxItems": 2}),
    "dimension_items": _obj(("dimension", "items"), dimension=_DIM,
                            items={"type": "array", "items": _ITEM, "minItems": 1,
                                   "maxItems": 100000}),
    "default_option_map": _obj(("dimension", "options"), dimension=_DIM, options=_CODE_MAP),
    "item_pole_map": _obj(("item", "dimension", "options"), item=_ITEM, dimension=_DIM,
                          options=_CODE_MAP),
    "option_weight": _obj(("item", "dimension", "weights"), item=_ITEM, dimension=_DIM,
                          weights={"type": "object"}),
    "reverse": _obj(("item", "dimension"), item=_ITEM, dimension=_DIM),
    "threshold": _obj(("dimension", "method"), dimension=_DIM,
                      method={"type": "string", "enum": ["higher_wins", "min_points"]},
                      pole=_POLE, value={"type": "number", "minimum": 0}),
    "tie_break": _obj(("dimension", "pole"), dimension=_DIM, pole=_POLE),
    "missing_answer_policy": _obj(("dimension", "policy"), dimension=_DIM,
                                  policy={"type": "string",
                                          "enum": ["skip", "count_as_pole", "invalidate"]},
                                  pole=_POLE),
    "normalization": _obj(("dimension", "method", "parameters"), dimension=_DIM,
                          method={"type": "string", "enum": ["none", "percentage", "scaled", "other"]},
                          parameters={"type": "object"}),
}
RULE_TYPES = tuple(SCHEMAS)

# Madde düzeyinde olan türler (anahtar madde numarası içerir)
ITEM_TYPES = ("item_pole_map", "option_weight", "reverse")


def _code_ok(code: str) -> bool:
    return isinstance(code, str) and 1 <= len(code) <= 5 and code.strip() == code


def _extra(rule_type: str, body: dict) -> list[str]:
    errs: list[str] = []
    if rule_type == "zero_score":
        if body["policy"] == "select_pole" and "pole" not in body:
            errs.append("$.pole: 'select_pole' için zorunlu")
        if body["policy"] != "select_pole" and "pole" in body:
            errs.append("$.pole: yalnız 'select_pole' için kullanılır")
    if rule_type == "dimension_def" and len(set(body["poles"])) != 2:
        errs.append("$.poles: iki kutup birbirinden farklı olmalı")
    if rule_type == "dimension_items" and len(set(body["items"])) != len(body["items"]):
        errs.append("$.items: yinelenen madde numarası")
    if rule_type in ("default_option_map", "item_pole_map"):
        opts = body["options"]
        if not opts:
            errs.append("$.options: boş olamaz")
        for k, v in opts.items():
            if not _code_ok(k):
                errs.append(f"$.options.{k}: geçersiz seçenek kodu")
            errs += validate(v, _POLE, f"$.options.{k}")
    if rule_type == "option_weight":
        w = body["weights"]
        if not w:
            errs.append("$.weights: boş olamaz")
        for k, poles in w.items():
            if not _code_ok(k):
                errs.append(f"$.weights.{k}: geçersiz seçenek kodu")
            if not isinstance(poles, dict) or not poles:
                errs.append(f"$.weights.{k}: kutup → puan nesnesi olmalı")
                continue
            for p, pts in poles.items():
                errs += validate(p, _POLE, f"$.weights.{k}.<kutup>")
                errs += validate(pts, {"type": "number", "minimum": -1000, "maximum": 1000},
                                 f"$.weights.{k}.{p}")
    if rule_type == "threshold":
        if body["method"] == "min_points":
            for f in ("pole", "value"):
                if f not in body:
                    errs.append(f"$.{f}: 'min_points' yönteminde zorunlu")
        else:
            for f in ("pole", "value"):
                if f in body:
                    errs.append(f"$.{f}: 'higher_wins' yönteminde kullanılmaz")
    if rule_type == "missing_answer_policy":
        if body["policy"] == "count_as_pole" and "pole" not in body:
            errs.append("$.pole: 'count_as_pole' politikasında zorunlu")
        if body["policy"] != "count_as_pole" and "pole" in body:
            errs.append("$.pole: yalnız 'count_as_pole' politikasında kullanılır")
    return errs


def validate_rule(rule_type: str, body) -> list[str]:
    """Hata listesi (boş = geçerli). Mesajlar Türkçe ve JSON yolu içerir."""
    if not isinstance(rule_type, str) or rule_type not in SCHEMAS:
        return [f"'{rule_type}' kural kataloğunda yok (izinli türler: {', '.join(RULE_TYPES)})"]
    try:
        json.dumps(body, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        return ["$: sonlu JSON verisi gerekli"]
    errs = validate(body, SCHEMAS[rule_type])
    return errs or _extra(rule_type, body)


def rule_dimension(body: dict) -> str:
    return body["dimension"]


def rule_key(rule_type: str, body: dict) -> str:
    if rule_type in ITEM_TYPES:
        return f"rule:{rule_type}:item:{body['item']}:dim:{body['dimension']}"
    return f"rule:{rule_type}:dim:{body['dimension']}"


def canonical(rule_type: str, body: dict) -> str:
    """Anlamca aynı iki gövde için aynı metin (anahtar sırası ve madde listesi sırası önemsiz)."""
    b = dict(body)
    if rule_type == "dimension_items":
        b["items"] = sorted(b["items"])
    return json.dumps(b, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_POLICY_TR = {"skip": "hesaba katılmaz", "count_as_pole": "kutba sayılır", "invalidate": "boyut "
              "geçersiz sayılır"}


def describe_rule(rule_type: str, body: dict) -> str:
    """Kullanıcıya gösterilecek kısa Türkçe özet."""
    d = body.get("dimension")
    if rule_type in ("aggregation", "dimension_weight", "zero_score", "precision"):
        return f"{d}: {rule_type} {canonical(rule_type, body)}"
    if rule_type == "dimension_def":
        return f"{d} boyutunun kutupları: {', '.join(body['poles'])}"
    if rule_type == "dimension_items":
        it = sorted(body["items"])
        s = ", ".join(map(str, it[:10])) + (f" … ({len(it)} madde)" if len(it) > 10 else "")
        return f"{d} boyutunun maddeleri: {s}"
    if rule_type == "default_option_map":
        return f"{d}: varsayılan eşleme " + ", ".join(f"{c}→{p}" for c, p in body["options"].items())
    if rule_type == "item_pole_map":
        return f"{d}: {body['item']}. madde " + ", ".join(f"{c}→{p}"
                                                         for c, p in body["options"].items())
    if rule_type == "option_weight":
        w = "; ".join(f"{c}: " + ", ".join(f"{p} {v:g}" for p, v in pts.items())
                      for c, pts in body["weights"].items())
        return f"{d}: {body['item']}. madde ağırlıkları ({w})"
    if rule_type == "reverse":
        return f"{d}: {body['item']}. madde ters puanlanır"
    if rule_type == "threshold":
        if body["method"] == "higher_wins":
            return f"{d}: puanı yüksek olan kutbun harfi verilir"
        return f"{d}: {body['pole']} için en az {body['value']:g} puan gerekir"
    if rule_type == "tie_break":
        return f"{d}: puanlar eşitse {body['pole']}"
    if rule_type == "missing_answer_policy":
        scope = "tüm boyutlar" if d == "*" else d
        extra = f" ({body['pole']})" if "pole" in body else ""
        return f"{scope}: cevapsız madde {_POLICY_TR[body['policy']]}{extra}"
    if rule_type == "normalization":
        return f"{d}: normalleştirme ({body['method']})"
    return f"{rule_type}: {canonical(rule_type, body)}"
