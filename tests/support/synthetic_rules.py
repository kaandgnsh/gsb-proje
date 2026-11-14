"""Sentetik puanlama kuralları ve örnek sonuçlar — testlerin bağımsız doğruluk kaynağı.

Bu modül `lai.core` kodunu KULLANMAZ: beklenen puanlar burada ayrı ve basit biçimde hesaplanır.
Kurallar (sentetik): madde i → boyut DIMS[(i-1) % 4]; A → ilk kutup, B → ikinci kutup;
ters maddelerde tersi; yüksek puan kazanır; eşitlikte ikinci kutup (I, N, F, P).
"""
from __future__ import annotations

import random

DIMS = {"E-I": ("E", "I"), "S-N": ("S", "N"), "T-F": ("T", "F"), "J-P": ("J", "P")}
ORDER = list(DIMS)


def dim_of(item: int) -> str:
    return ORDER[(item - 1) % 4]


def truth_rules(n_items: int, reverse: set[int]) -> list[tuple[str, dict]]:
    """Gerçek kural kümesi (rule_type, body) — keşfin bulması beklenen."""
    out = []
    for d, poles in DIMS.items():
        out.append(("dimension_def", {"dimension": d, "poles": list(poles)}))
        out.append(("dimension_items", {"dimension": d,
                                        "items": [i for i in range(1, n_items + 1) if dim_of(i) == d]}))
        out.append(("default_option_map", {"dimension": d, "options": {"A": poles[0], "B": poles[1]}}))
        out.append(("threshold", {"dimension": d, "method": "higher_wins"}))
        out.append(("tie_break", {"dimension": d, "pole": poles[1]}))
    for i in sorted(reverse):
        out.append(("reverse", {"item": i, "dimension": dim_of(i)}))
    return out


def true_outcome(answers: dict[int, str], reverse: set[int]) -> dict:
    pts = {d: {p: 0 for p in poles} for d, poles in DIMS.items()}
    for i, a in answers.items():
        d = dim_of(i)
        first, second = DIMS[d]
        pole = first if a == "A" else second
        if i in reverse:
            pole = second if pole == first else first
        pts[d][pole] += 1
    out = {}
    for d, (p1, p2) in DIMS.items():
        s = pts[d]
        letter = p1 if s[p1] > s[p2] else p2  # eşitlikte ikinci kutup
        out[d] = {"letter": letter, "scores": s}
    return out


def make_examples(n_items: int, n: int, reverse: set[int], *, seed: int = 11,
                  with_scores: bool = True, with_letters: bool = True,
                  force_tie: bool = False) -> dict:
    rnd = random.Random(seed)
    exs = []
    for k in range(n):
        ans = {i: rnd.choice("AB") for i in range(1, n_items + 1)}
        if force_tie and k == 0:  # ilk örnekte her boyutta eşitlik
            for d in DIMS:
                items = [i for i in range(1, n_items + 1) if dim_of(i) == d]
                for j, i in enumerate(items):
                    want_first = j < len(items) // 2
                    a = "A" if want_first else "B"
                    if i in reverse:
                        a = "B" if a == "A" else "A"
                    ans[i] = a
        res = true_outcome(ans, reverse)
        for r in res.values():
            if not with_scores:
                r.pop("scores")
            if not with_letters:
                r.pop("letter")
        exs.append({"id": f"Ö-{k + 1}", "answers": {str(i): a for i, a in ans.items()},
                    "result": res})
    return {"format": "lai-examples", "version": 1, "examples": exs}


# ------------------------------------------------------------------- sentetik yönerge
def guide_statements(n_items: int, reverse: set[int]) -> list[tuple[str, str, dict]]:
    """(cümle, kural türü, gövde): yönergede yazan cümle ve ondan çıkarılması beklenen kural."""
    out = []
    for d, (p1, p2) in DIMS.items():
        items = [i for i in range(1, n_items + 1) if dim_of(i) == d]
        out += [
            (f"{d} boyutu {p1} ve {p2} kutuplarından oluşur.", "dimension_def",
             {"dimension": d, "poles": [p1, p2]}),
            (f"{d} boyutunun maddeleri: {', '.join(map(str, items))}.", "dimension_items",
             {"dimension": d, "items": items}),
            (f"{d} boyutunda A seçeneği {p1}, B seçeneği {p2} kutbuna bir puan verir.",
             "default_option_map", {"dimension": d, "options": {"A": p1, "B": p2}}),
            (f"{d} boyutunda puanı yüksek olan kutbun harfi verilir.", "threshold",
             {"dimension": d, "method": "higher_wins"}),
            (f"{d} boyutunda puanlar eşitse {p2} harfi verilir.", "tie_break",
             {"dimension": d, "pole": p2}),
        ]
    for i in sorted(reverse):
        out.append((f"{i}. madde ters puanlanır.", "reverse", {"item": i, "dimension": dim_of(i)}))
    return out
