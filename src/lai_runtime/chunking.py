"""Uzun metni modele sığacak parçalara böler; hiçbir karakter kaybolmaz."""
from __future__ import annotations

from dataclasses import dataclass

_BOUNDARIES = ("\n\n", ".\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int
    text: str


def _cut(text: str, start: int, limit: int) -> int:
    """[start, limit) içinde en iyi bölme noktasını (bitiş indeksi) döndürür."""
    if limit >= len(text):
        return len(text)
    window = text[start:limit]
    floor = len(window) // 2  # çok küçük parçalar üretmemek için
    for sep in _BOUNDARIES:
        pos = window.rfind(sep)
        if pos >= floor:
            return start + pos + len(sep)
    return limit


def chunk_text(text: str, max_chars: int = 4000, overlap: int | None = None) -> list[Chunk]:
    if overlap is None:
        overlap = min(200, max_chars // 4)
    if max_chars <= 0 or overlap < 0 or overlap >= max_chars // 2:
        raise ValueError("max_chars > 0 ve 0 <= overlap < max_chars/2 olmalı")
    chunks: list[Chunk] = []
    start = 0
    while True:
        end = _cut(text, start, start + max_chars)
        chunks.append(Chunk(len(chunks), start, end, text[start:end]))
        if end >= len(text):
            return chunks
        nxt = max(end - overlap, start + 1)
        if overlap:
            # örtüşme başlangıcını kelime sınırına hizala
            sp = text.find(" ", nxt, end)
            nxt = sp + 1 if sp != -1 else nxt
        start = nxt
