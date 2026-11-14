"""Denetimler: eksik, bozuk, belirsiz, yinelenen kayıtlar. Her kod için en az bir test."""
import time

from lai.ingest.checks import run_checks
from lai.ingest.model import AnswerKeyEntry, Locator, ParsedItem, ParsedOption
from lai.ingest.textparse import parse_text_units
from ingest_helpers import U


def item(seq, number, text, opts="ABCDE"):
    return ParsedItem(seq, number, str(number), text, Locator(page=1, line=seq + 1),
                      [ParsedOption(c, f"seçenek {c}", Locator(page=1)) for c in opts])


def key(seq, n, v):
    return AnswerKeyEntry(seq, n, v, Locator(page=9))


def found(issues, code, severity=None):
    return [i for i in issues if i.code == code and (severity is None or i.severity == severity)]


def base(n=5):
    return [item(i, i + 1, f"Benzersiz ifade numarası {w}") for i, w in
            enumerate(["bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz"][:n])]


def test_clean_input_has_no_issues():
    assert run_checks(base(), []) == []


def test_number_gap_and_leading_gap():
    its = [item(0, 2, "İkinci farklı ifade"), item(1, 3, "Üçüncü ayrı cümle"),
           item(2, 7, "Yedinci başka söz")]
    (gap,) = found(run_checks(its, []), "number_gap", "error")
    assert "1" in gap.message and "4–6" in gap.message


def test_duplicate_number_is_error_with_both_locations():
    its = base(3) + [item(3, 2, "Tamamen başka bir ifade burada")]
    (d,) = found(run_checks(its, []), "duplicate_number", "error")
    assert d.item_number == 2 and "satır 2" in d.message and "satır 4" in d.message


def test_missing_number_items_do_not_create_gap_errors():
    its = base(3) + [item(3, None, "Numarasız ifade")]
    assert not found(run_checks(its, []), "number_gap")


def test_empty_and_corrupt_text():
    its = base(3) + [item(3, 4, "   "), item(4, 5, "Bozuk � karakter"),
                     item(5, 6, "Kontrol\x07 karakteri")]
    iss = run_checks(its, [])
    assert [i.item_number for i in found(iss, "empty_text", "error")] == [4]
    assert sorted(i.item_number for i in found(iss, "corrupt_text", "error")) == [5, 6]


def test_corrupt_option_text():
    its = base(2)
    its[1].options[0].text = "Ev�et"
    assert found(run_checks(its, []), "corrupt_text", "error")[0].item_number == 2


def test_duplicate_option_code_case_insensitive():
    its = base(2)
    its[0].options.append(ParsedOption("a", "tekrar", Locator(page=1)))
    assert found(run_checks(its, []), "duplicate_option_code", "error")[0].item_number == 1


def test_missing_options_when_others_have_them():
    its = base(4) + [item(4, 5, "Seçeneksiz ama diğerleri seçenekli", opts="")]
    assert found(run_checks(its, []), "missing_options", "error")[0].item_number == 5


def test_no_options_anywhere_is_info():
    its = [item(i, i + 1, f"Ölçek ortak olabilir {w}", opts="") for i, w in enumerate("xyz")]
    iss = run_checks(its, [])
    assert [i.severity for i in iss] == ["info"] and iss[0].code == "no_options"


def test_empty_option_text_is_error():
    its = base(2)
    its[0].options[2].text = ""
    assert found(run_checks(its, []), "empty_option_text", "error")[0].item_number == 1


def test_option_count_and_letter_gap_warnings():
    its = base(5)
    its[3].options = its[3].options[:2]
    its[4].options = [o for o in its[4].options if o.code != "C"]
    iss = run_checks(its, [])
    assert {i.item_number for i in found(iss, "option_count_differs", "warning")} == {4, 5}
    assert [i.item_number for i in found(iss, "option_letter_gap", "warning")] == [5]


def test_duplicate_text_turkish_case_insensitive():
    its = base(3) + [item(3, 4, "IŞIK açıkken uyurum."), item(4, 5, "ışık açıkken uyurum"),
                     item(5, 6, "İyi uyurum."), item(6, 7, "iyi UYURUM")]
    d = found(run_checks(its, []), "duplicate_text", "warning")
    assert sorted(i.item_number for i in d) == [5, 7]  # sonraki kopya işaretlenir
    assert "madde 4" in d[0].message and "madde 6" in d[1].message


def test_similar_text_warning():
    its = base(3) + [item(3, 4, "Kalabalık ortamlarda kendimi çok rahat hissederim."),
                     item(4, 5, "Kalabalık ortamlarda kendimi rahat hissederim.")]
    (s,) = found(run_checks(its, []), "similar_text", "warning")
    assert "4" in s.message and "5" in s.message


def test_out_of_order_is_warning_order_preserved():
    its = [item(0, 1, "Birinci benzersiz"), item(1, 3, "Üçüncü benzersiz söz"),
           item(2, 2, "İkinci apayrı cümle")]
    iss = run_checks(its, [])
    assert found(iss, "out_of_order", "warning") and not found(iss, "number_gap")


def test_answer_key_checks():
    its = base(4)
    keys = [key(0, 1, "A"), key(1, 2, "Z"), key(2, 2, "B"), key(3, 9, "A"), key(4, 1, "a"),
            key(5, 3, "Q")]
    iss = run_checks(its, keys)
    assert found(iss, "key_unknown_item", "error")[0].item_number == 9
    assert found(iss, "key_conflict", "error")[0].item_number == 2
    assert found(iss, "key_repeated", "info")[0].item_number == 1  # A ve a aynı değer
    assert {i.item_number for i in found(iss, "key_incomplete", "warning")} == {4}
    # çelişen madde (2) ayrıca "seçenek dışı" sayılmaz; tek değerli Q ise uyarı alır
    assert [i.item_number for i in found(iss, "key_value_not_option", "warning")] == [3]


def test_option_line_without_text_becomes_empty_option_not_glued_to_item():
    items, _, issues, _ = parse_text_units(U(["1. Soru metni", "A)", "B) Hayır"]))
    assert items[0].text == "Soru metni"
    assert [(o.code, o.text) for o in items[0].options] == [("A", ""), ("B", "Hayır")]
    assert found(run_checks(items, []), "empty_option_text", "error")


def test_option_text_on_next_line_is_attached():
    items, _, issues, _ = parse_text_units(U(["1. Soru", "A)", "Evet", "B) Hayır"]))
    assert [(o.code, o.text) for o in items[0].options] == [("A", "Evet"), ("B", "Hayır")]


def test_similarity_scales_to_large_inputs():
    import random

    import synthetic_docs as sd
    rnd = random.Random(5)
    words = sorted({w.strip(".,") for s in sd._WHEN + sd._WHO + sd._WHAT + [sd._TAIL]
                    for w in s.split()})
    big = [item(i, i + 1, " ".join(rnd.choice(words) for _ in range(10))) for i in range(1000)]
    t = time.perf_counter()
    iss = run_checks(big, [])
    assert time.perf_counter() - t < 8
    assert not found(iss, "similar_text")


def test_similarity_skipped_above_limit_is_reported(monkeypatch):
    import lai.ingest.checks as ch
    monkeypatch.setattr(ch, "SIMILARITY_MAX_ITEMS", 3)
    its = base(5) + [item(5, 6, base(1)[0].text)]
    iss = run_checks(its, [])
    assert found(iss, "similarity_skipped", "info")
    assert found(iss, "duplicate_text", "warning")  # birebir denetim yine yapılır
