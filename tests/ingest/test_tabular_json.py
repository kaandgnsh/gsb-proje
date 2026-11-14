"""Tablo (XLSX/CSV/DOCX tablosu) ve JSON ayrıştırma."""
import pytest

from lai.ingest.jsonfmt import parse_json_document
from lai.ingest.model import IngestError, Locator, RawUnit
from lai.ingest.tabular import map_columns, parse_table_units


def T(rows, sheet="Sorular"):
    return [RawUnit(i, "", Locator(sheet=sheet, row=i + 1), tuple(r)) for i, r in enumerate(rows)]


@pytest.mark.parametrize("header,expect", [
    (["Soru No", "Soru Metni", "A", "B"], {"number": 0, "text": 1, "options": {"A": 2, "B": 3}}),
    (["SIRA NO", "İFADE", "Seçenek a", "Seçenek b", "Cevap Anahtarı"],
     {"number": 0, "text": 1, "options": {"a": 2, "b": 3}, "key": 4}),
    (["no", "madde", "şık 1", "şık 2"], {"number": 0, "text": 1, "options": {"1": 2, "2": 3}}),
    (["Item No", "Question", "Option A", "Answer"],
     {"number": 0, "text": 1, "options": {"A": 2}, "key": 3}),
])
def test_header_synonyms(header, expect):
    m = map_columns(header)
    assert m is not None
    assert (m.number, m.text, m.options, m.key) == (
        expect["number"], expect["text"], expect["options"], expect.get("key"))


def test_header_without_text_column_is_not_a_header():
    assert map_columns(["Ad", "Soyad"]) is None


def test_rows_map_to_items_options_and_keys():
    items, keys, issues, meta = parse_table_units(T([
        ["Bu sayfa sentetik"],
        ["Soru No", "Soru Metni", "Seçenek A", "Seçenek B", "Not", "Cevap"],
        ["1", "Işık açıkken çalışırım.", "Evet", "Hayır", "x", "A"],
        ["2", "Çay içerim.", "Evet", "", "", ""],
    ]))
    assert [(i.number, i.text) for i in items] == [(1, "Işık açıkken çalışırım."), (2, "Çay içerim.")]
    assert [(o.code, o.text) for o in items[0].options] == [("A", "Evet"), ("B", "Hayır")]
    assert items[0].options[1].locator.describe() == "Sorular!D3"
    assert items[0].locator.describe() == "Sorular, satır 3"
    assert [(k.item_number, k.value) for k in keys] == [(1, "A")]
    assert any(i.code == "ignored_column" and "Not" in i.message for i in issues)
    assert meta["header_rows"] == ["Sorular, satır 2"]


def test_invalid_and_missing_numbers_are_errors_not_guessed():
    items, _, issues, _ = parse_table_units(T([
        ["No", "Soru"], ["", "Numarasız"], ["12a", "Bozuk numara"], ["3", "Tamam"]]))
    assert [i.number for i in items] == [None, None, 3]
    errs = {(i.code, i.item_seq) for i in issues if i.severity == "error"}
    assert ("missing_number", 0) in errs and ("invalid_number", 1) in errs
    assert items[1].label == "12a"


def test_unrecognized_sheet_with_data_is_warned():
    rows = T([["No", "Soru"], ["1", "Bir"]]) + T([["rastgele", "veri"], ["x", "y"]], sheet="Notlar")
    for i, u in enumerate(rows):
        object.__setattr__(u, "index", i)
    items, _, issues, _ = parse_table_units(rows)
    assert len(items) == 1
    assert any(i.code == "unrecognized_sheet" and "Notlar" in i.message for i in issues)


def test_multiple_item_sheets_are_concatenated_in_order():
    rows = T([["No", "Soru"], ["1", "Bir"]], "S1") + T([["No", "Soru"], ["2", "İki"]], "S2")
    for i, u in enumerate(rows):
        object.__setattr__(u, "index", i)
    items, *_ = parse_table_units(rows)
    assert [(i.number, i.seq) for i in items] == [(1, 0), (2, 1)]


def test_no_header_anywhere_is_fatal():
    with pytest.raises(IngestError) as e:
        parse_table_units(T([["a", "b"], ["c", "d"]]))
    assert e.value.code == "no_header" and "Soru" in e.value.message


# ------------------------------------------------------------------ JSON
def _doc(**kw):
    d = {"format": "lai-inventory", "version": 1,
         "items": [{"number": 1, "text": "Işık", "options": [{"code": "A", "text": "Evet"}]}]}
    d.update(kw)
    return d


def test_json_valid_document():
    items, keys, issues, _ = parse_json_document(_doc(answer_key=[{"item": 1, "value": "A"}]))
    assert items[0].text == "Işık" and items[0].options[0].text == "Evet"
    assert items[0].locator.json_path == "$.items[0]"
    assert items[0].options[0].locator.json_path == "$.items[0].options[0]"
    assert keys[0].value == "A" and issues == []


@pytest.mark.parametrize("doc,needle", [
    ([], "$"), ({"format": "baska", "version": 1, "items": []}, "$.format"),
    (_doc(version=2), "$.version"), (_doc(items="x"), "$.items"),
    (_doc(extra=1), "extra"),
])
def test_json_structure_errors_are_fatal_with_path(doc, needle):
    with pytest.raises(IngestError) as e:
        parse_json_document(doc)
    assert e.value.code == "schema" and needle in e.value.message


def test_json_bad_item_reported_with_path_others_kept():
    d = _doc(items=[{"number": "1", "text": "Bir"}, {"number": 2, "text": "İki"},
                    {"number": 3, "text": "Üç", "options": [{"code": "A"}]}])
    items, _, issues, _ = parse_json_document(d)
    assert [i.number for i in items] == [None, 2, 3]
    paths = {i.locator.json_path for i in issues if i.severity == "error"}
    assert "$.items[0].number" in paths and "$.items[2].options[0]" in paths
    assert items[2].options == []
