"""JSON envanter biçimi `lai-inventory` sürüm 1.

    {"format": "lai-inventory", "version": 1, "name": "…",
     "items": [{"number": 1, "text": "…", "options": [{"code": "A", "text": "…"}]}],
     "answer_key": [{"item": 1, "value": "A"}]}

Yapı (format, sürüm, items dizisi) hatalıysa belge reddedilir. Tek tek hatalı maddeler
yol bilgisiyle raporlanır; geçerli alanları korunur, geçersiz alanlar tahminle doldurulmaz.
"""
from __future__ import annotations

from lai_runtime.jsonschema_lite import validate

from .model import ERROR, AnswerKeyEntry, IngestError, Issue, Locator, ParsedItem, ParsedOption

TOP_SCHEMA = {
    "type": "object",
    "required": ["format", "version", "items"],
    "additionalProperties": False,
    "properties": {
        "format": {"enum": ["lai-inventory"]},
        "version": {"enum": [1]},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "items": {"type": "array", "minItems": 1},
        "answer_key": {"type": "array"},
    },
}
OPTION_SCHEMA = {"type": "object", "required": ["code", "text"], "additionalProperties": False,
                 "properties": {"code": {"type": "string", "minLength": 1},
                                "text": {"type": "string"}}}
ITEM_FIELDS = {"number": {"type": "integer", "minimum": 1}, "text": {"type": "string"}}
KEY_SCHEMA = {"type": "object", "required": ["item", "value"], "additionalProperties": False,
              "properties": {"item": {"type": "integer", "minimum": 1},
                             "value": {"type": "string", "minLength": 1}}}


def _err(issues, path, msg, seq=None):
    issues.append(Issue(ERROR, "schema", f"{path}: {msg}", Locator(json_path=path), item_seq=seq))


def parse_json_document(data):
    errs = validate(data, TOP_SCHEMA)
    if errs:
        raise IngestError("schema", "JSON envanter biçimi geçersiz: " + "; ".join(errs))
    items: list[ParsedItem] = []
    keys: list[AnswerKeyEntry] = []
    issues: list[Issue] = []
    for i, raw in enumerate(data["items"]):
        path = f"$.items[{i}]"
        seq = len(items)
        if not isinstance(raw, dict):
            _err(issues, path, "madde bir nesne olmalı; alınmadı")
            continue
        extra = set(raw) - {"number", "text", "options"}
        if extra:
            _err(issues, path, f"tanınmayan alan(lar): {', '.join(sorted(extra))}", seq)
        num = raw.get("number")
        if validate(num, ITEM_FIELDS["number"]) or "number" not in raw:
            _err(issues, f"{path}.number", f"1 veya daha büyük tam sayı olmalı (gelen: {num!r})", seq)
            num = None
        text = raw.get("text")
        if not isinstance(text, str):
            _err(issues, f"{path}.text", f"metin olmalı (gelen: {text!r})", seq)
            text = ""
        it = ParsedItem(seq=seq, number=num, label=None if num is None else str(num), text=text,
                        locator=Locator(json_path=path))
        opts = raw.get("options", [])
        if not isinstance(opts, list):
            _err(issues, f"{path}.options", "dizi olmalı", seq)
            opts = []
        for j, o in enumerate(opts):
            opath = f"{path}.options[{j}]"
            e = validate(o, OPTION_SCHEMA, opath)
            if e:
                _err(issues, opath, "; ".join(e), seq)
                continue
            it.options.append(ParsedOption(o["code"], o["text"], Locator(json_path=opath)))
        items.append(it)
    for j, k in enumerate(data.get("answer_key", [])):
        kpath = f"$.answer_key[{j}]"
        e = validate(k, KEY_SCHEMA, kpath)
        if e:
            _err(issues, kpath, "; ".join(e))
            continue
        keys.append(AnswerKeyEntry(len(keys), k["item"], k["value"], Locator(json_path=kpath)))
    return items, keys, issues, {"name": data.get("name")}
