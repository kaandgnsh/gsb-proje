"""Sentetik (gerçek olmayan) envanter ve katılımcı verisi üretici. Gerçek sınav verisi
kullanılmaz; içerik rastgele ama tohumla (seed) tekrarlanabilir."""
from __future__ import annotations

import random

DIMENSIONS = [
    ("E-I", "Dışadönüklük / İçedönüklük", [("E", "Dışadönük"), ("I", "İçedönük")]),
    ("S-N", "Duyumsama / Sezgi", [("S", "Duyumsama"), ("N", "Sezgi")]),
    ("T-F", "Düşünme / Hissetme", [("T", "Düşünme"), ("F", "Hissetme")]),
    ("J-P", "Yargılama / Algılama", [("J", "Yargılama"), ("P", "Algılama")]),
]
OPTIONS = [("A", "Kesinlikle katılıyorum"), ("B", "Katılıyorum"), ("C", "Kararsızım"),
           ("D", "Katılmıyorum"), ("E", "Kesinlikle katılmıyorum")]
_TOPICS = ["kalabalık ortamlar", "yeni fikirler", "planlı çalışma", "duygusal kararlar",
           "ayrıntılar", "yalnız kalmak", "son dakika değişiklikleri", "mantıksal analiz",
           "somut veriler", "olasılıklar", "kurallar", "esneklik"]


def make_inventory(n_items: int = 400, seed: int = 1) -> dict:
    rnd = random.Random(seed)
    items = []
    for n in range(1, n_items + 1):
        code, _, poles = DIMENSIONS[(n - 1) % len(DIMENSIONS)]
        pole = rnd.choice(poles)[0]
        items.append({
            "number": n,
            "text": f"Madde {n}: {rnd.choice(_TOPICS).capitalize()} konusunda kendimi rahat hissederim.",
            "options": list(OPTIONS),
            "dimension": code, "pole": pole, "reverse": rnd.random() < 0.15,
        })
    return {"name": f"Sentetik Envanter ({n_items})", "dimensions": DIMENSIONS, "items": items}


def make_responses(n_items: int, n_participants: int, seed: int = 2,
                   missing_rate: float = 0.01) -> list[dict[int, str | None]]:
    rnd = random.Random(seed)
    codes = [c for c, _ in OPTIONS]
    return [{i: (None if rnd.random() < missing_rate else rnd.choice(codes))
             for i in range(1, n_items + 1)} for _ in range(n_participants)]


def load_into(store, inventory: dict) -> tuple[int, int]:
    iid = store.inventory.create_inventory(inventory["name"], "sentetik test verisi")
    ver = store.inventory.add_version(iid, label="sentetik")
    for code, name, poles in inventory["dimensions"]:
        store.inventory.add_dimension(iid, code, name, poles)
    store.inventory.add_items(ver, inventory["items"])
    return iid, ver
