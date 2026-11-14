"""Serbest metin (PDF/DOCX paragrafları) → maddeler, seçenekler, cevap anahtarı.

Deterministik satır makinesi. İçerik değiştirilmez: yalnız satırlar tek boşlukla birleştirilir.
Belirsiz durumlar sessizce çözülmez; `Issue` olarak raporlanır.
"""
from __future__ import annotations

import math
import re
from dataclasses import replace

from .model import ERROR, INFO, WARNING, AnswerKeyEntry, Issue, Locator, ParsedItem, ParsedOption, RawUnit
from .normalize import tr_lower, tr_upper_ok

_ITEM = re.compile(r"^\s*(?P<label>(?:(?P<prefix>soru|madde)\s*)?(?P<num>\d{1,4})\s*"
                   r"(?P<punct>[.):\-–])?)(?:\s+(?P<rest>.*))?$", re.IGNORECASE)
_OPT = re.compile(r"^\s*\(?(?P<code>[A-Ja-j])\s*[).\-](?:\s+(?P<rest>.*))?$")
_INLINE = re.compile(r"(?:(?<=\s)|^)\(?([A-Ja-j])\)\s+")
_KEYTOK = re.compile(r"(\d{1,4})\s*[-.:=)]\s*([A-Za-zÇĞİÖŞÜçğıöşü0-9]{1,3})(?=[\s,;]|$)")
_KEY_HEADERS = {"cevap anahtarı", "yanıt anahtarı", "cevap anahtari", "answer key", "anahtar",
                "puanlama anahtarı"}
_SECTION = re.compile(r"^(bölüm|kısım|section|part)\b", re.IGNORECASE)


def _is_item(text: str) -> re.Match | None:
    m = _ITEM.match(text)
    if not m:
        return None
    # Önek yoksa noktalama zorunlu ("12.5" gibi ondalık sayılar madde değildir)
    if not m.group("prefix") and not m.group("punct"):
        return None
    return m


def _key_tokens(text: str) -> list[re.Match] | None:
    toks = list(_KEYTOK.finditer(text))
    if not toks:
        return None
    residue = _KEYTOK.sub("", text)
    return toks if not residue.strip(" \t,;") else None


def _is_key_header(text: str) -> bool:
    return tr_lower(text).strip(" :.") in _KEY_HEADERS


def _is_heading(text: str) -> bool:
    t = text.strip()
    if len(t) > 80:
        return False
    return bool(_SECTION.match(t)) or (tr_upper_ok(t) and not t.endswith((".", "?", ":")))


def _next_letter(code: str) -> str:
    return chr(ord(code) + 1)


def _split_inline(text: str, first: str | None) -> tuple[str, list[tuple[str, str, int]]]:
    """`text` içinde ardışık harfli satır içi seçenekleri bulur. `first`: beklenen ilk harf
    (None → 'a' ya da 'A'). Dönüş: (öndeki metin, [(kod, metin, başlangıç ofseti)])."""
    marks = []
    expect = first
    for m in _INLINE.finditer(text):
        c = m.group(1)
        if expect is None:
            if c not in "aA":
                continue
        elif c != expect:
            continue
        marks.append(m)
        expect = _next_letter(c)
    if not marks or (first is None and len(marks) < 2):
        return text, []
    head = text[:marks[0].start()].rstrip()
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(1), text[m.end():end].strip(), m.start()))
    return head, out


def _sub_locator(loc: Locator, offset: int, length: int) -> Locator:
    if loc.char_start is None:
        return loc
    return replace(loc, char_start=loc.char_start + offset,
                   char_end=loc.char_start + offset + length, bbox=None)


def _furniture(units: list[RawUnit]) -> set[int]:
    """Sayfaların çoğunda tekrar eden başlık/altlık satırları (rakamlar yok sayılarak)."""
    pages = {u.locator.page for u in units if u.locator.page is not None}
    if len(pages) < 3:
        return set()
    seen: dict[str, set[int]] = {}
    for u in units:
        t = u.text.strip()
        if u.locator.page is None or _is_item(t) or _OPT.match(t) or _key_tokens(t):
            continue
        seen.setdefault(re.sub(r"\d+", "#", tr_lower(t)), set()).add(u.locator.page)
    limit = max(3, math.ceil(len(pages) * 0.5))
    keys = {k for k, ps in seen.items() if len(ps) >= limit}
    return {u.index for u in units
            if u.locator.page is not None and re.sub(r"\d+", "#", tr_lower(u.text.strip())) in keys}


def parse_text_units(units: list[RawUnit]):
    """Dönüş: (items, answer_keys, issues, meta)."""
    items: list[ParsedItem] = []
    keys: list[AnswerKeyEntry] = []
    issues: list[Issue] = []
    furniture = _furniture(units)
    meta = {"furniture_lines": len(furniture), "preamble_lines": 0, "headings": [],
            "preamble_options": []}
    cur: ParsedItem | None = None
    cur_opt: ParsedOption | None = None
    in_key = False
    multiline_flagged: set[int] = set()
    before_first = True

    def add_option(code, text, loc, unit_idx):
        nonlocal cur_opt
        cur_opt = ParsedOption(code, text, loc)
        cur.options.append(cur_opt)
        cur.raw_last = unit_idx

    for u in units:
        if u.index in furniture:
            continue
        t = u.text.strip()
        lead = len(u.text) - len(u.text.lstrip())
        if _is_key_header(t):
            in_key, cur, cur_opt = True, None, None
            continue
        if in_key:
            toks = _key_tokens(t)
            if toks:
                for m in _KEYTOK.finditer(u.text):
                    keys.append(AnswerKeyEntry(len(keys), int(m.group(1)), m.group(2),
                                               _sub_locator(u.locator, m.start(), m.end() - m.start())))
                continue
            m = _is_item(t)
            if not (m and m.group("rest")):
                issues.append(Issue(WARNING, "unparsed_key_line",
                                    f"Cevap anahtarı bölümündeki satır çözümlenemedi: '{t[:80]}'",
                                    u.locator))
                continue
            in_key = False
        m = _is_item(t)
        if m:
            before_first = False
            rest = m.group("rest") or ""
            head, inline = _split_inline(rest, None)
            cur = ParsedItem(seq=len(items), number=int(m.group("num")), label=m.group("label").strip(),
                             text=head, locator=u.locator, raw_first=u.index, raw_last=u.index)
            items.append(cur)
            cur_opt = None
            rest_off = lead + (m.start("rest") - lead if m.group("rest") else 0)
            for code, text, off in inline:
                add_option(code, text, _sub_locator(u.locator, rest_off + off, len(t) - off), u.index)
            continue
        om = _OPT.match(t)
        if om and om.group("rest"):
            code = om.group("code")
            if before_first:
                _, inline = _split_inline(t, code)
                meta["preamble_options"].extend(
                    [(c, x) for c, x, _ in inline] or [(code, om.group("rest").strip())])
                continue
            if cur is None:
                issues.append(Issue(ERROR, "orphan_option",
                                    f"Hiçbir maddeye bağlanamayan seçenek satırı: '{t[:80]}'",
                                    u.locator))
                continue
            _, inline = _split_inline(t, code)
            for c, text, off in inline or [(code, om.group("rest").strip(), 0)]:
                add_option(c, text, _sub_locator(u.locator, lead + off, len(t) - off), u.index)
            continue
        if _is_heading(t):
            meta["headings"].append(t)
            issues.append(Issue(INFO, "heading", f"Başlık olarak ayrıldı: '{t}'", u.locator))
            cur, cur_opt = None, None
            continue
        if cur_opt is not None:
            cur_opt.text = f"{cur_opt.text} {t}".strip()
            cur_opt_loc = cur_opt.locator
            cur_opt.locator = cur_opt_loc.span_to(u.locator)
            cur.raw_last = u.index
            if cur.seq not in multiline_flagged:
                multiline_flagged.add(cur.seq)
                issues.append(Issue(WARNING, "multiline_option",
                                    f"Madde {cur.number}: bir seçenek birden çok satıra yayılıyor; "
                                    f"satırlar seçeneğe eklendi, kontrol edin.", u.locator,
                                    item_seq=cur.seq, item_number=cur.number))
        elif cur is not None:
            cur.text = f"{cur.text} {t}".strip()
            cur.locator = cur.locator.span_to(u.locator)
            cur.raw_last = u.index
        else:
            meta["preamble_lines"] += 1
    if furniture:
        issues.append(Issue(INFO, "furniture_removed",
                            f"{len(furniture)} satır sayfa başlığı/altlığı olarak ayrıldı (ham "
                            f"içerikte duruyor)."))
    if meta["preamble_lines"]:
        issues.append(Issue(INFO, "preamble",
                            f"{meta['preamble_lines']} satır yönerge/açıklama olarak ayrıldı (ham "
                            f"içerikte duruyor)."))
    if meta["preamble_options"]:
        issues.append(Issue(INFO, "preamble_options",
                            f"Maddelerden önce {len(meta['preamble_options'])} seçenek satırı "
                            f"bulundu (ortak yanıt ölçeği olabilir). Maddelere otomatik "
                            f"BAĞLANMADI."))
    return items, keys, issues, meta
