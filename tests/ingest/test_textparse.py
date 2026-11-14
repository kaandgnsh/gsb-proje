"""Serbest metin ayrıştırıcı: madde/seçenek ayrımı, sıra, konum, cevap anahtarı."""
from ingest_helpers import U

from lai.ingest.textparse import parse_text_units


def codes(issues):
    return {i.code for i in issues}


def test_items_and_options_are_separated():
    items, keys, issues, meta = parse_text_units(U([
        "Yönerge: Size uygun olanı seçiniz.",
        "1. Işıklı ortamlarda çoğunlukla enerjik hissederim.",
        "A) Katılıyorum", "B) Katılmıyorum",
        "2. İş yerinde ayrıntılara dikkat ederim.",
        "a) Evet", "b) Hayır", "c) Bazen"]))
    assert [(i.number, i.text) for i in items] == [
        (1, "Işıklı ortamlarda çoğunlukla enerjik hissederim."),
        (2, "İş yerinde ayrıntılara dikkat ederim.")]
    assert [(o.code, o.text) for o in items[0].options] == [("A", "Katılıyorum"), ("B", "Katılmıyorum")]
    assert [o.code for o in items[1].options] == ["a", "b", "c"]  # yazıldığı gibi
    assert items[0].label == "1." and items[0].seq == 0 and items[1].seq == 1
    assert meta["preamble_lines"] == 1 and "preamble" in codes(issues)
    assert keys == []


def test_number_styles():
    items, *_ = parse_text_units(U([
        "1) Birinci", "2- İkinci", "Soru 3: Üçüncü", "Madde 4. Dördüncü", "5 – Beşinci"]))
    assert [(i.number, i.text) for i in items] == [
        (1, "Birinci"), (2, "İkinci"), (3, "Üçüncü"), (4, "Dördüncü"), (5, "Beşinci")]
    assert items[2].label == "Soru 3:"


def test_decimal_numbers_inside_text_are_not_items():
    items, *_ = parse_text_units(U(["1. Haftada", "12.5 saatten fazla çalışırım.", "A) Evet"]))
    assert len(items) == 1 and items[0].text == "Haftada 12.5 saatten fazla çalışırım."


def test_multiline_item_text_across_pages_keeps_span():
    items, *_ = parse_text_units(U(
        ["1. Önemli kararların arifesinde zaman zaman", "geçmiş deneyimlerimi"],
        ["hatırlarım.", "A) Evet", "B) Hayır"]))
    it = items[0]
    assert it.text == "Önemli kararların arifesinde zaman zaman geçmiş deneyimlerimi hatırlarım."
    assert (it.locator.page, it.locator.page_end) == (1, 2)  # metin aralığı
    assert it.raw_first == 0 and it.raw_last == 4  # seçenekler dahil maddenin ham bloğu


def test_inline_options_are_split_only_when_sequential():
    items, *_ = parse_text_units(U([
        "1. Şehir dışına çıkarım. a) Evet b) Hayır",
        "2. Bu cümlede b) gibi bir ifade var.",
        "a) Doğru   b) Yanlış   c) Emin değilim"]))
    assert items[0].text == "Şehir dışına çıkarım."
    assert [(o.code, o.text) for o in items[0].options] == [("a", "Evet"), ("b", "Hayır")]
    assert items[1].text == "Bu cümlede b) gibi bir ifade var."
    assert [(o.code, o.text) for o in items[1].options] == [
        ("a", "Doğru"), ("b", "Yanlış"), ("c", "Emin değilim")]


def test_multiline_option_is_kept_and_flagged():
    items, _, issues, _ = parse_text_units(U([
        "1. Soru", "A) Uzun bir seçenek", "metninin devamı", "B) Kısa"]))
    assert items[0].options[0].text == "Uzun bir seçenek metninin devamı"
    assert "multiline_option" in codes(issues)


def test_page_furniture_removed_but_option_lines_kept():
    pages = []
    for p in range(1, 5):
        pages.append(["SENTETİK ENVANTER", f"{p}. Madde metni {p} burada.",
                      "A) Katılıyorum", "B) Katılmıyorum", f"Sayfa {p} / 4"])
    items, _, issues, meta = parse_text_units(U(*pages))
    assert len(items) == 4
    assert all([o.text for o in it.options] == ["Katılıyorum", "Katılmıyorum"] for it in items)
    assert all("Sayfa" not in it.text and "SENTETİK" not in it.text for it in items)
    assert meta["furniture_lines"] == 8 and "furniture_removed" in codes(issues)


def test_headings_are_not_glued_to_previous_item():
    items, _, issues, meta = parse_text_units(U([
        "1. Birinci", "A) Evet", "BÖLÜM 2", "2. İkinci", "A) Evet", "Kısım 3 - Tercihler",
        "3. Üçüncü"]))
    assert items[0].options[0].text == "Evet" and items[1].options[0].text == "Evet"
    assert meta["headings"] == ["BÖLÜM 2", "Kısım 3 - Tercihler"]
    assert "heading" in codes(issues)


def test_answer_key_section_values_kept_verbatim():
    items, keys, issues, _ = parse_text_units(U([
        "1. Bir", "A) x", "B) y", "2. İki", "A) x", "B) y",
        "CEVAP ANAHTARI", "1-A  2-b", "3 = E", "anlaşılmayan satır"]))
    assert len(items) == 2
    assert [(k.item_number, k.value) for k in keys] == [(1, "A"), (2, "b"), (3, "E")]
    assert keys[1].locator.char_start is not None
    assert "unparsed_key_line" in codes(issues)


def test_options_before_first_item_are_a_possible_shared_scale_not_attached():
    items, _, issues, meta = parse_text_units(U([
        "Yanıt ölçeği:", "a) Kesinlikle katılıyorum", "b) Katılmıyorum", "1. Soru", "2. Soru iki"]))
    assert [len(i.options) for i in items] == [0, 0]
    assert meta["preamble_options"] == [("a", "Kesinlikle katılıyorum"), ("b", "Katılmıyorum")]
    assert any(i.code == "preamble_options" and i.severity == "info" for i in issues)


def test_option_after_heading_without_item_is_error():
    items, _, issues, _ = parse_text_units(U(["1. Soru", "A) Evet", "BÖLÜM 2", "A) Sahipsiz"]))
    assert [o.text for o in items[0].options] == ["Evet"]
    assert any(i.code == "orphan_option" and i.severity == "error" for i in issues)


def test_item_number_only_line_takes_following_text():
    items, *_ = parse_text_units(U(["7.", "Metin bir sonraki satırda.", "A) Evet"]))
    assert items[0].number == 7 and items[0].text == "Metin bir sonraki satırda."


def test_out_of_sequence_letter_still_an_option():
    items, *_ = parse_text_units(U(["1. Soru", "A) a", "B) b", "D) d"]))
    assert [o.code for o in items[0].options] == ["A", "B", "D"]
