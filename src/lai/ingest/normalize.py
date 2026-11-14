"""Yalnız KARŞILAŞTIRMA için Türkçe duyarlı normalleştirme. Saklanan metin değiştirilmez."""
from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def tr_lower(s: str) -> str:
    """Türkçe küçük harf: I → ı, İ → i (Python'un varsayılanı I → i yapar)."""
    return unicodedata.normalize("NFC", s).replace("I", "ı").replace("İ", "i").lower()


def tr_upper_ok(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def compare_key(s: str) -> str:
    """Büyük/küçük harf, noktalama ve boşluk farkları yok sayılır."""
    return _WS.sub(" ", _PUNCT.sub(" ", tr_lower(s))).strip()


def trigrams(s: str) -> frozenset[str]:
    k = f"  {compare_key(s)} "
    return frozenset(k[i:i + 3] for i in range(len(k) - 2))


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)
