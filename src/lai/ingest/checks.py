"""Ayrıştırma sonrası denetimler. Hiçbir şeyi düzeltmez; yalnız işaretler.

error   → envanter sürümü oluşturulmaz (kullanıcı incelemesi gerekir)
warning → sürüm oluşur, raporda gösterilir
info    → bilgi
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .model import ERROR, INFO, WARNING, AnswerKeyEntry, Issue, ParsedItem
from .normalize import compare_key, jaccard, trigrams

SIMILAR_THRESHOLD = 0.85
SIMILARITY_MAX_ITEMS = 5000  # üstünde benzerlik (yaklaşık eşleşme) denetimi atlanır ve raporlanır
_CORRUPT = re.compile(r"[�\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(str(start) if start == prev else f"{start}–{prev}")
            start = prev = n
    if start is not None:
        out.append(str(start) if start == prev else f"{start}–{prev}")
    return ", ".join(out)


def _where(it: ParsedItem) -> str:
    return it.locator.describe()


def _similar_pairs(texts: list[tuple[int, frozenset]], t: float):
    """Önek süzgeci ile Jaccard ≥ t çiftleri (tüm çiftleri karşılaştırmadan)."""
    freq = Counter(g for _, s in texts for g in s)
    order = sorted(texts, key=lambda x: len(x[1]))
    index: dict[str, list[int]] = defaultdict(list)
    sets = dict(texts)
    for idx, s in order:
        toks = sorted(s, key=lambda g: (freq[g], g))
        plen = len(toks) - math.ceil(t * len(toks)) + 1
        cands = set()
        for g in toks[:plen]:
            for other in index[g]:
                if len(sets[other]) >= t * len(s):
                    cands.add(other)
            index[g].append(idx)
        for other in cands:
            j = jaccard(s, sets[other])
            if j >= t:
                yield (other, idx, j) if other < idx else (idx, other, j)


def run_checks(items: list[ParsedItem], keys: list[AnswerKeyEntry]) -> list[Issue]:
    issues: list[Issue] = []

    def add(sev, code, msg, it: ParsedItem | None = None, loc=None, number=None):
        issues.append(Issue(sev, code, msg, loc or (it.locator if it else None),
                            item_seq=it.seq if it else None,
                            item_number=number if number is not None else (it.number if it else None)))

    numbered = [it for it in items if it.number is not None]
    # --- numaralar
    by_num: dict[int, list[ParsedItem]] = defaultdict(list)
    for it in numbered:
        by_num[it.number].append(it)
    for n, its in sorted(by_num.items()):
        if len(its) > 1:
            add(ERROR, "duplicate_number",
                f"Madde numarası {n} {len(its)} kez geçiyor: "
                + "; ".join(_where(x) for x in its) + ". Hangisinin doğru olduğu tahmin edilmez.",
                its[1], number=n)
    if numbered:
        missing = sorted(set(range(1, max(by_num) + 1)) - set(by_num))
        if missing:
            add(ERROR, "number_gap", f"Eksik madde numaraları: {_ranges(missing)}. Bu maddeler "
                                     f"belgede bulunamadı ya da okunamadı.")
        seq_nums = [it.number for it in numbered]
        bad = [b for a, b in zip(seq_nums, seq_nums[1:], strict=False) if b <= a]
        if bad:
            first = next(it for it in numbered if it.number == bad[0])
            add(WARNING, "out_of_order",
                f"Madde numaraları belgede artan sırada değil ({len(bad)} yerde; ilk: madde "
                f"{bad[0]}). Belgedeki özgün sıra korundu.", first)
    # --- metin
    for it in items:
        if not it.text.strip():
            add(ERROR, "empty_text", f"Madde {it.number}: soru metni boş ({_where(it)}).", it)
        elif _CORRUPT.search(it.text):
            add(ERROR, "corrupt_text", f"Madde {it.number}: metinde bozuk/okunamayan karakter "
                                       f"var ({_where(it)}). Karakter tahmin edilmez.", it)
        elif len(compare_key(it.text)) < 3:
            add(WARNING, "short_text", f"Madde {it.number}: metin çok kısa: '{it.text}'.", it)
    # --- seçenekler
    with_opts = [it for it in items if it.options]
    if items and not with_opts:
        add(INFO, "no_options", "Hiçbir maddede seçenek bulunamadı. Envanter ortak bir yanıt "
                                "ölçeği kullanıyor olabilir; seçenekler sonra netleştirilecek.")
    mode = Counter(len(it.options) for it in with_opts).most_common(1)[0][0] if with_opts else 0
    for it in items:
        if not it.options:
            if with_opts and len(with_opts) * 2 >= len(items):
                add(ERROR, "missing_options",
                    f"Madde {it.number}: seçenek yok, oysa diğer maddelerin çoğunda var "
                    f"({_where(it)}).", it)
            continue
        codes = [o.code.casefold() for o in it.options]
        dups = sorted({c for c in codes if codes.count(c) > 1})
        if dups:
            add(ERROR, "duplicate_option_code",
                f"Madde {it.number}: seçenek kodu birden çok kez var: {', '.join(dups)}.", it)
        for o in it.options:
            if not o.text.strip():
                add(ERROR, "empty_option_text", f"Madde {it.number}: '{o.code}' seçeneğinin "
                                                f"metni boş ({o.locator.describe()}).", it, loc=o.locator)
            elif _CORRUPT.search(o.text):
                add(ERROR, "corrupt_text", f"Madde {it.number}: '{o.code}' seçeneğinde bozuk "
                                           f"karakter var.", it, loc=o.locator)
        if len(it.options) != mode:
            add(WARNING, "option_count_differs",
                f"Madde {it.number}: {len(it.options)} seçenek var; maddelerin çoğunda {mode}.", it)
        if not dups and all(len(c) == 1 for c in codes):
            first = "1" if codes[0].isdigit() else "a"
            expected = [chr(ord(first) + i) for i in range(len(codes))]
            if codes != expected:
                add(WARNING, "option_letter_gap",
                    f"Madde {it.number}: seçenek kodları ardışık değil "
                    f"({', '.join(o.code for o in it.options)}). Eksik seçenek olabilir.", it)
    # --- yinelenen / benzer metin
    seen: dict[str, ParsedItem] = {}
    exact_pairs = set()
    for it in items:
        k = compare_key(it.text)
        if not k:
            continue
        if k in seen:
            first = seen[k]
            exact_pairs.add((first.seq, it.seq))
            add(WARNING, "duplicate_text",
                f"Madde {it.number}, madde {first.number} ile aynı metne sahip "
                f"({_where(first)} / {_where(it)}). Kasıtlı (tutarlılık maddesi) olabilir.", it)
        else:
            seen[k] = it
    by_seq = {it.seq: it for it in items}
    grams = [(it.seq, trigrams(it.text)) for it in items if compare_key(it.text)]
    if len(grams) > SIMILARITY_MAX_ITEMS:
        add(INFO, "similarity_skipped",
            f"{len(grams)} madde var; yaklaşık benzerlik denetimi {SIMILARITY_MAX_ITEMS} maddenin "
            f"üstünde yapılmıyor. Birebir aynı metin denetimi yapıldı.")
        grams = []
    for a, b, j in sorted(_similar_pairs(grams, SIMILAR_THRESHOLD)):
        if (a, b) in exact_pairs or compare_key(by_seq[a].text) == compare_key(by_seq[b].text):
            continue
        add(WARNING, "similar_text",
            f"Madde {by_seq[a].number} ve madde {by_seq[b].number} çok benzer (benzerlik "
            f"{j:.2f}). Yinelenmiş ya da yazım farkı olabilir.", by_seq[b])
    # --- cevap anahtarı (değerler yorumlanmaz)
    if keys:
        by_key: dict[int, list[AnswerKeyEntry]] = defaultdict(list)
        for k in keys:
            by_key[k.item_number].append(k)
        for n, ks in sorted(by_key.items()):
            vals = {k.value.casefold() for k in ks}
            if n not in by_num:
                add(ERROR, "key_unknown_item", f"Cevap anahtarında madde {n} var ama belgede bu "
                                               f"madde yok ({ks[0].locator.describe()}).",
                    loc=ks[0].locator, number=n)
            if len(vals) > 1:
                add(ERROR, "key_conflict", f"Madde {n} için cevap anahtarında çelişen değerler: "
                                           + ", ".join(k.value for k in ks) + ".",
                    loc=ks[1].locator, number=n)
            elif len(ks) > 1:
                add(INFO, "key_repeated", f"Madde {n} cevap anahtarında {len(ks)} kez aynı "
                                          f"değerle geçiyor.", loc=ks[1].locator, number=n)
            if n in by_num and len(vals) == 1:
                it = by_num[n][0]
                if it.options and ks[0].value.casefold() not in {o.code.casefold() for o in it.options}:
                    add(WARNING, "key_value_not_option",
                        f"Madde {n}: anahtardaki '{ks[0].value}' değeri seçenek kodlarından biri "
                        f"değil. Değer olduğu gibi saklandı (yorumlanmadı).", it, loc=ks[0].locator)
        for n, its in sorted(by_num.items()):
            if n not in by_key:
                add(WARNING, "key_incomplete", f"Madde {n} için cevap anahtarında değer yok.", its[0])
    return issues
