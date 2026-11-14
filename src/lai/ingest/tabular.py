"""Tablo satırları (XLSX/CSV/DOCX tablosu) → maddeler. Sütunlar başlıktan eşlenir."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from itertools import groupby

from .model import ERROR, INFO, WARNING, AnswerKeyEntry, IngestError, Issue, ParsedItem, ParsedOption, RawUnit
from .normalize import compare_key

_NUMBER = {"no", "numara", "sıra", "sıra no", "soru no", "soru numarası", "madde no",
           "madde numarası", "item", "item no", "number", "n", "nr"}
_TEXT = {"soru", "soru metni", "madde", "madde metni", "metin", "ifade", "ifadeler", "sorular",
         "maddeler", "question", "text", "item text", "statement"}
_KEY = {"cevap", "yanıt", "anahtar", "cevap anahtarı", "yanıt anahtarı", "doğru cevap",
        "doğru yanıt", "answer", "key", "answer key"}
_OPTION = re.compile(r"^(?:seçenek|secenek|şık|sik|option|opt|cevap seçeneği)?\s*([a-j]|[1-9])$"
                     r"|^([a-j]) şıkkı$")
HEADER_SEARCH_ROWS = 10


@dataclass
class ColumnMap:
    number: int
    text: int
    options: dict[str, int] = field(default_factory=dict)
    key: int | None = None
    ignored: list[str] = field(default_factory=list)


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def map_columns(header) -> ColumnMap | None:
    number = text = key = None
    options: dict[str, int] = {}
    ignored = []
    def any_in(variants, vocab):
        return any(v in vocab for v in variants)

    for i, raw in enumerate(header):
        h = compare_key(str(raw))
        if not h:
            continue
        # Türkçe (I→ı) ve İngilizce (I→i) küçük harf biçimlerinin ikisi de denenir
        variants = (h, compare_key(str(raw).replace("I", "i")))
        if any_in(variants, _NUMBER) and number is None:
            number = i
        elif any_in(variants, _TEXT) and text is None:
            text = i
        elif any_in(variants, _KEY) and key is None:
            key = i
        elif (m := _OPTION.match(variants[1])) and number is not None:
            letter = m.group(1) or m.group(2)
            # başlıkta yazıldığı biçim korunur ("Seçenek A" → "A", "a" → "a")
            original = re.findall(r"\b([A-Ja-j1-9])\b", str(raw))
            code = original[-1] if original else letter
            if code in options:
                return None
            options[code] = i
        else:
            ignored.append(str(raw))
    if number is None or text is None:
        return None
    return ColumnMap(number, text, options, key, ignored)


def _group_key(u: RawUnit):
    return u.locator.sheet


def parse_table_units(units: list[RawUnit]):
    """Dönüş: (items, answer_keys, issues, meta). Hiçbir grupta başlık yoksa IngestError."""
    items: list[ParsedItem] = []
    keys: list[AnswerKeyEntry] = []
    issues: list[Issue] = []
    meta = {"header_rows": [], "ignored_rows": 0}
    found = False
    for sheet, grp in groupby(units, key=_group_key):
        rows = list(grp)
        hdr_i, cmap = None, None
        for i, u in enumerate(rows[:HEADER_SEARCH_ROWS]):
            cmap = map_columns(u.cells or ())
            if cmap:
                hdr_i = i
                break
        label = sheet or "tablo"
        if cmap is None:
            if len(rows) > 1:
                issues.append(Issue(WARNING, "unrecognized_sheet",
                                    f"'{label}' içinde soru tablosu başlığı bulunamadı; bu bölümün "
                                    f"{len(rows)} satırı içe aktarılmadı (ham içerikte duruyor).",
                                    rows[0].locator))
            else:
                issues.append(Issue(INFO, "ignored_sheet",
                                    f"'{label}' tek satırlık açıklama olarak yok sayıldı.",
                                    rows[0].locator))
            continue
        found = True
        meta["header_rows"].append(rows[hdr_i].locator.describe())
        if cmap.ignored:
            issues.append(Issue(INFO, "ignored_column",
                                f"'{label}': tanınmayan sütun(lar) alınmadı: "
                                + ", ".join(f"'{c}'" for c in cmap.ignored), rows[hdr_i].locator))
        meta["ignored_rows"] += hdr_i
        for u in rows[hdr_i + 1:]:
            cells = list(u.cells or ())
            get = lambda i, cells=cells: cells[i].strip() if i is not None and i < len(cells) else ""  # noqa: E731
            num_s, text = get(cmap.number), get(cmap.text)
            opts = [(code, get(ci), ci) for code, ci in cmap.options.items()]
            if not num_s and not text and not any(t for _, t, _ in opts):
                meta["ignored_rows"] += 1
                issues.append(Issue(WARNING, "unrecognized_row",
                                    "Satırda numara, soru ya da seçenek yok; alınmadı.", u.locator))
                continue
            seq = len(items)
            number = None
            if not num_s:
                issues.append(Issue(ERROR, "missing_number",
                                    "Soru numarası boş; numara tahmin edilmez.", u.locator, item_seq=seq))
            elif re.fullmatch(r"\d+\.?", num_s) and int(num_s.rstrip(".")) >= 1:
                number = int(num_s.rstrip("."))
            else:
                issues.append(Issue(ERROR, "invalid_number",
                                    f"Soru numarası sayı değil: '{num_s}'.", u.locator, item_seq=seq))
            it = ParsedItem(seq=seq, number=number, label=num_s or None,
                            text=cells[cmap.text] if cmap.text < len(cells) else "",
                            locator=u.locator, raw_first=u.index, raw_last=u.index)
            it.text = it.text.strip()
            for code, t, ci in opts:
                if t:
                    it.options.append(ParsedOption(code, cells[ci].strip(),
                                                   replace(u.locator, column=_col_letter(ci))))
            items.append(it)
            kv = get(cmap.key)
            if kv and number is not None:
                keys.append(AnswerKeyEntry(len(keys), number, kv,
                                           replace(u.locator, column=_col_letter(cmap.key))))
    if not found:
        raise IngestError("no_header",
                          "Soru tablosu başlığı bulunamadı. İlk satırlardan birinde en az bir "
                          "numara sütunu ('Soru No', 'No', 'Sıra') ve bir metin sütunu ('Soru', "
                          "'Soru Metni', 'Madde', 'İfade') olmalı.")
    return items, keys, issues, meta
