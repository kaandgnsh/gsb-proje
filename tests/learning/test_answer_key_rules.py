"""Cevap anahtarı → otomatik kural önerisi. Tüm envanter, anahtar ve örnekler sentetiktir."""

import json

import pytest

from lai.ingest.answer_key import import_answer_key
from lai.learning.examples import import_examples
from lai.learning.interactive import analyze, answer
from lai.store.store import Store

KEY = {1: "P", 2: "Q", 3: "P", 4: "P", 5: "Q", 6: "P", 7: "Q", 8: "P"}


@pytest.fixture
def env(tmp_path):
    s = Store.open(tmp_path / "anahtar.db")
    i = s.inventory.create_inventory("Sentetik anahtar")
    v = s.inventory.add_version(i)
    s.inventory.add_items(
        v,
        [
            {"number": n, "text": f"Sentetik {n}", "options": [("A", "Bir"), ("B", "İki")]}
            for n in range(1, 9)
        ],
    )
    s.inventory.add_dimension(i, "X", poles=[("P", None), ("Q", None)])
    yield s, i, tmp_path
    s.close()


def load_key(s, i, tmp_path, values, name="anahtar.csv"):
    p = tmp_path / name
    p.write_text(
        "Soru No,Cevap Anahtarı\n" + "".join(f'{n},"{v}"\n' for n, v in values.items()),
        encoding="utf-8",
    )
    import_answer_key(s, i, p, documents_dir=tmp_path / "documents")


def load_examples(s, i, tmp_path, examples):
    p = tmp_path / "ornekler.json"
    p.write_text(
        json.dumps(
            {
                "format": "lai-examples",
                "version": 1,
                "examples": [
                    {
                        "id": f"Ö-{n}",
                        "answers": {str(k): v for k, v in answers.items()},
                        "result": {"X": {"scores": scores}},
                    }
                    for n, (answers, scores) in enumerate(examples, 1)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    import_examples(s, i, p, documents_dir=tmp_path / "documents")


def key_gaps(s, i):
    return [g for g in analyze(s, i) if g.category == "answer_key"]


def item_maps(s, i):
    return {
        e.item_number: (e.content["body"]["options"], e.confidence)
        for e in s.knowledge.current(i, kind="rule")
        if e.rule_type == "item_pole_map"
    }


ALL_A = {n: "A" for n in range(1, 9)}
MIXED = {n: ("A" if n % 2 else "B") for n in range(1, 9)}


def test_pole_letters_without_examples_offer_both_readings(env):
    s, i, tmp = env
    load_key(s, i, tmp, KEY)
    gs = key_gaps(s, i)
    assert sorted(g.payload["evidence"]["reading"] for g in gs) == [
        "pole_first",
        "pole_second",
    ]
    assert all(g.state == "WEAKLY_INFERRED" for g in gs)
    assert all("sınanamadı" in g.payload["question"] for g in gs)
    assert item_maps(s, i) == {}  # öneri onaysız yazılmaz

    first = next(g for g in gs if g.payload["evidence"]["reading"] == "pole_first")
    answer(s, first.id, "yes")
    maps = item_maps(s, i)
    assert maps[1] == ({"A": "P", "B": "Q"}, "CONFIRMED")
    assert maps[2] == ({"A": "Q", "B": "P"}, "CONFIRMED")
    assert len(maps) == 8
    assert key_gaps(s, i) == []


def test_examples_select_the_consistent_reading(env):
    s, i, tmp = env
    load_key(s, i, tmp, KEY)
    # ilk seçenek = anahtar kutbu: hepsi A → P5 Q3; karışık → 1,3,5,7 A (P,P,Q,Q) 2,4,6,8 B (P,Q,Q,Q)
    load_examples(s, i, tmp, [(ALL_A, {"P": 5, "Q": 3}), (MIXED, {"P": 3, "Q": 5})])
    gs = key_gaps(s, i)
    assert [g.payload["evidence"]["reading"] for g in gs] == ["pole_first"]
    assert gs[0].state == "STRONGLY_INFERRED"
    assert "örnek sonuçlarla tutarlı" in gs[0].payload["question"]
    assert gs[0].payload["evidence"]["fit_detail"]["matched"] == 2


def test_no_reading_fits_examples_keeps_open_question(env):
    s, i, tmp = env
    load_key(s, i, tmp, KEY)
    load_examples(s, i, tmp, [(ALL_A, {"P": 8, "Q": 0})])
    gs = key_gaps(s, i)
    assert len(gs) == 1 and gs[0].payload["proposal"] is None
    tested = {r["reading"]: r["fit"] for r in gs[0].payload["evidence"]["tested_readings"]}
    assert tested == {"pole_first": "inconsistent", "pole_second": "inconsistent"}


def test_explicit_option_pole_pairs(env):
    s, i, tmp = env
    load_key(s, i, tmp, {n: ("A:P B:Q" if n % 2 else "A=Q, B=P") for n in range(1, 9)})
    gs = key_gaps(s, i)
    assert [g.payload["evidence"]["reading"] for g in gs] == ["pairs"]
    answer(s, gs[0].id, "yes")
    maps = item_maps(s, i)
    assert maps[1][0] == {"A": "P", "B": "Q"} and maps[2][0] == {"A": "Q", "B": "P"}


def test_slash_pairs_follow_option_order(env):
    s, i, tmp = env
    load_key(s, i, tmp, {n: "Q/P" for n in range(1, 9)})
    gs = key_gaps(s, i)
    assert [g.payload["evidence"]["reading"] for g in gs] == ["pairs"]
    assert gs[0].payload["proposal"][0]["body"]["options"] == {"A": "Q", "B": "P"}


def test_option_codes_need_examples_and_membership(env):
    s, i, tmp = env
    key = {n: ("A" if n <= 4 else "B") for n in range(1, 9)}
    load_key(s, i, tmp, key)
    # Üyelik bilinmeden seçenek kodu yorumlanamaz: açık soru kalır.
    gs = key_gaps(s, i)
    assert len(gs) == 1 and gs[0].payload["proposal"] is None
    g = next(g for g in analyze(s, i) if g.category == "dimension_items")
    answer(
        s,
        g.id,
        "teach",
        "Tüm maddeler X boyutunda",
        structured={"rule_type": "dimension_items",
                    "body": {"dimension": "X", "items": list(range(1, 9))}},
    )
    gs = key_gaps(s, i)
    assert len(gs) == 1 and gs[0].payload["proposal"] is None  # örnek yok
    # anahtar seçeneği → P: hepsi A → 1-4 P, 5-8 Q
    load_examples(s, i, tmp, [(ALL_A, {"P": 4, "Q": 4}), (MIXED, {"P": 4, "Q": 4})])
    load_examples(s, i, tmp, [(key, {"P": 8, "Q": 0})])
    gs = key_gaps(s, i)
    assert [g.payload["evidence"]["reading"] for g in gs] == ["option_pole"]
    answer(s, gs[0].id, "yes")
    maps = item_maps(s, i)
    assert maps[1][0] == {"A": "P", "B": "Q"} and maps[5][0] == {"A": "Q", "B": "P"}
    assert key_gaps(s, i) == []


def test_conflicting_key_values_are_not_interpreted(env):
    s, i, tmp = env
    load_key(s, i, tmp, KEY)
    load_key(s, i, tmp, {**KEY, 1: "Q"}, name="anahtar2.csv")
    gs = key_gaps(s, i)
    assert len(gs) == 1 and gs[0].payload["proposal"] is None
