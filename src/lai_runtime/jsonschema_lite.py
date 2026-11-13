"""Küçük JSON Schema alt kümesi doğrulayıcı (LLM çıktısını denetlemek için).

Desteklenen: type (tek ya da liste), properties, required, additionalProperties(false),
items, enum, minimum, maximum, minLength, maxLength, minItems, maxItems.
"""
from __future__ import annotations

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(instance, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](instance) for x in types):
            return [f"{path}: tür {'/'.join(types)} bekleniyordu, {type(instance).__name__} geldi"]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} izinli değerlerden biri değil {schema['enum']}")
    if _TYPES["number"](instance):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < en az {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > en çok {schema['maximum']}")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: metin çok kısa")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: metin çok uzun")
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}.{req}: zorunlu alan eksik")
        for k, v in instance.items():
            if k in props:
                errors += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{k}: şemada olmayan alan")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: en az {schema['minItems']} öğe gerekli")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: en çok {schema['maxItems']} öğe olabilir")
        if "items" in schema:
            for i, v in enumerate(instance):
                errors += validate(v, schema["items"], f"{path}[{i}]")
    return errors
