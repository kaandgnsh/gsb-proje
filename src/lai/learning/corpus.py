"""Belge derlemi: ham birimlerden okunabilir metin, parçalama ve birebir alıntı bulma.

Alıntı eşleştirmede TEK gevşeklik: boşluk dizileri (satır sonu dahil) tek boşluk sayılır
(PDF/DOCX satır kaydırması). Harf, noktalama, büyük/küçük harf ya da Unicode biçimi farkı
eşleşmez. Bulunan alıntı, belgedeki metnin kendisiyle (LLM'in yazdığıyla değil) saklanır.
"""
from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass, field

from lai_runtime.chunking import Chunk, chunk_text

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Segment:
    start: int          # derlem metnindeki aralık
    end: int
    unit_idx: int
    page: int | None
    locator: dict
    plain: bool         # birim metni derlemde birebir (tablo satırı değil)


@dataclass(frozen=True)
class QuoteHit:
    corpus: Corpus
    start: int
    end: int
    occurrences: int

    @property
    def text(self) -> str:
        return self.corpus.text[self.start:self.end]

    @property
    def segments(self) -> list[Segment]:
        return self.corpus.segments_between(self.start, self.end)

    def evidence_fields(self) -> dict:
        """evidence_refs için: belge, sayfa, sayfa/paragraf içi aralık, konum, birebir alıntı."""
        segs = self.segments
        first, last = segs[0], segs[-1]
        loc = dict(first.locator)
        loc.pop("bbox", None)
        cs = ce = None
        same_page = first.page == last.page
        if first.plain and "char_start" in first.locator:
            cs = first.locator["char_start"] + (self.start - first.start)
            loc["char_start"] = cs
            if last.plain and same_page and "char_start" in last.locator and (
                    first.page is not None or first is last):
                ce = last.locator["char_start"] + (self.end - last.start)
                loc["char_end"] = ce
            else:
                loc.pop("char_end", None)
        if not same_page:
            loc["page_end"] = last.page
        if last is not first:
            loc["units"] = [first.unit_idx, last.unit_idx]
        return {"document_id": self.corpus.document_id, "page": first.page, "char_start": cs,
                "char_end": ce, "quote": self.text, "locator": loc}


@dataclass
class Corpus:
    document_id: int
    file_name: str
    text: str
    segments: list[Segment]
    _norm: str | None = field(default=None, repr=False)
    _map: list[int] | None = field(default=None, repr=False)

    @classmethod
    def for_document(cls, store, document_id: int) -> Corpus:
        name = store.conn.execute("SELECT file_name FROM source_documents WHERE id=?",
                                  (document_id,)).fetchone()
        if name is None:
            raise KeyError(f"belge yok: {document_id}")
        parts, segs, pos = [], [], 0
        for idx, page, text, loc_json in store.conn.execute(
                "SELECT idx, page, text, locator FROM document_raw_units WHERE document_id=? "
                "ORDER BY idx", (document_id,)):
            loc = json.loads(loc_json)
            plain = True
            if "row" in loc and text.startswith("["):
                try:
                    cells = json.loads(text)
                    text = " | ".join(c for c in cells if c.strip())
                    plain = False
                except (ValueError, AttributeError, TypeError):
                    pass
            if parts:
                pos += 1  # "\n"
            parts.append(text)
            segs.append(Segment(pos, pos + len(text), idx, page, loc, plain))
            pos += len(text)
        return cls(document_id, name[0], "\n".join(parts), segs)

    def segments_between(self, start: int, end: int) -> list[Segment]:
        """[start, end) ile kesişen birimler (alıntı her zaman bir birim içinde başlar)."""
        i = max(bisect.bisect_right([s.start for s in self.segments], start) - 1, 0)
        out = []
        for s in self.segments[i:]:
            if s.start >= end:
                break
            if s.end > start:
                out.append(s)
        return out

    def chunks(self, max_chars: int = 6000) -> list[Chunk]:
        return chunk_text(self.text, max_chars)

    def _normalized(self):
        if self._norm is None:
            out, mp, prev_ws = [], [], False
            for i, ch in enumerate(self.text):
                if ch.isspace():
                    if not prev_ws:
                        out.append(" ")
                        mp.append(i)
                    prev_ws = True
                else:
                    out.append(ch)
                    mp.append(i)
                    prev_ws = False
            self._norm, self._map = "".join(out), mp
        return self._norm, self._map

    def find(self, quote: str) -> QuoteHit | None:
        q = _WS.sub(" ", quote).strip()
        if not q:
            return None
        norm, mp = self._normalized()
        i = norm.find(q)
        if i < 0:
            return None
        n = norm.count(q)
        return QuoteHit(self, mp[i], mp[i + len(q) - 1] + 1, n)
