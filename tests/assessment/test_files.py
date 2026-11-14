import pytest
from openpyxl import Workbook

from lai.assessment.files import ResponseFileError, read_responses

from .conftest import csv_file

ITEMS = {n: {"A", "B"} for n in range(1, 9)}


def workbook(path, sheets):
    w = Workbook()
    w.remove(w.active)
    for name, rows in sheets.items():
        s = w.create_sheet(name)
        for row in rows:
            s.append(row)
    w.save(path)
    return path


def test_csv_bom_turkish_semicolon_and_empty_answers(tmp_path):
    p = tmp_path / "Türkçe boşluklu.csv"
    p.write_text("takma_ad;Soru 1;2\nDeniz;A;\n", encoding="utf-8-sig")
    r = read_responses(p, {1: {"A", "B"}, 2: {"A", "B"}})
    assert r.rows[0].identity == {"alias": "Deniz"}
    assert r.rows[0].answers == [[1, "A"], [2, None]]
    assert not r.rows[0].errors and len(r.sha256) == 64


@pytest.mark.parametrize(
    "headers,needle",
    [
        (
            ["participant_id", "Q1", "Soru 1", *[f"Q{i}" for i in range(2, 9)]],
            "aynı soruya",
        ),
        (
            ["participant_id", "participant_code", *[f"Q{i}" for i in range(1, 9)]],
            "aynı katılımcı",
        ),
        (["participant_id", *[f"Q{i}" for i in range(1, 8)]], "eksik"),
        (["participant_id", *[f"Q{i}" for i in range(1, 9)], "Q9"], "tanımsız"),
        (["participant_id", "", *[f"Q{i}" for i in range(2, 9)]], "boş sütun"),
        (["unknown", *[f"Q{i}" for i in range(1, 9)]], "belirsiz"),
        ([*[f"Q{i}" for i in range(1, 9)]], "kimliği"),
        (["participant_id", "Q1", "q1", *[f"Q{i}" for i in range(2, 9)]], "yinelenen"),
    ],
)
def test_ambiguous_or_missing_headers_rejected_before_import(tmp_path, headers, needle):
    p = csv_file(
        tmp_path / "bad.csv", headers=headers, rows=[["P"] + ["A"] * (len(headers) - 1)]
    )
    with pytest.raises(ResponseFileError) as e:
        read_responses(p, ITEMS)
    assert needle in str(e.value).lower() and e.value.issues[0].row == 1


def test_explicit_mapping_resolves_custom_headers_and_reports_ignored(tmp_path):
    p = csv_file(
        tmp_path / "custom.csv",
        headers=["Who", "One", "Two", "note"],
        rows=[["P", "A", "B", "Yorum"]],
    )
    r = read_responses(
        p,
        {1: {"A", "B"}, 2: {"A", "B"}},
        mapping={"participant_code": "Who", "questions": {"1": "One", "2": "Two"}},
    )
    assert r.rows[0].answers == [[1, "A"], [2, "B"]]
    assert r.mapping["CSV"]["ignored"] == ["note"]


@pytest.mark.parametrize(
    "mapping",
    [
        {"participant_code": "Who", "questions": {"1": "One", "2": "One"}},
        {"participant_code": "Missing", "questions": {"1": "One", "2": "Two"}},
        {"participant_code": "Who", "questions": {"1": "One", "01": "Two"}},
        {"participant_code": "Who", "questions": None},
    ],
)
def test_invalid_explicit_mapping(tmp_path, mapping):
    p = csv_file(
        tmp_path / "custom.csv", headers=["Who", "One", "Two"], rows=[["P", "A", "B"]]
    )
    with pytest.raises(ResponseFileError):
        read_responses(p, {1: {"A"}, 2: {"B"}}, mapping=mapping)


def test_row_errors_have_file_participant_question_and_row(tmp_path):
    p = csv_file(
        tmp_path / "invalid.csv", rows=[["K-1", "Z", *["A"] * 7], ["K-2", *["A"] * 8]]
    )
    r = read_responses(p, ITEMS)
    error = r.rows[0].errors[0]
    assert (
        error["file"] == "invalid.csv"
        and error["row"] == 2
        and error["participant"] == "K-1"
    )
    assert error["question"] == 1 and error["column"] == "Q1"
    assert not r.rows[1].errors


def test_duplicate_participant_rows_are_all_reported(tmp_path):
    p = csv_file(
        tmp_path / "duplicate.csv",
        rows=[["SAME", *["A"] * 8], ["OTHER", *["A"] * 8], ["SAME", *["B"] * 8]],
    )
    r = read_responses(p, ITEMS)
    assert r.rows[0].errors and r.rows[2].errors and not r.rows[1].errors


def test_xlsx_multiple_sheets_formulas_and_source_context(tmp_path):
    headers = ["alias", *[f"Q{i}" for i in range(1, 9)]]
    p = workbook(
        tmp_path / "source.xlsx",
        {
            "Bir": [headers, ["Deniz", *["A"] * 8]],
            "İki": [headers, ["Ece", "=1+1", *["B"] * 7]],
        },
    )
    r = read_responses(p, ITEMS)
    assert not r.rows[0].errors
    e = r.rows[1].errors[0]
    assert (e["sheet"], e["row"], e["participant"], e["question"]) == (
        "İki",
        2,
        "Ece",
        1,
    )
    assert "Formül" in e["message"]
    assert len(read_responses(p, ITEMS, sheets=["Bir"]).rows) == 1
    with pytest.raises(ResponseFileError):
        read_responses(p, ITEMS, sheets=["Yok"])


def test_duplicate_across_sheets(tmp_path):
    header = ["participant_id", "Q1"]
    p = workbook(
        tmp_path / "dup.xlsx", {"A": [header, ["P", "A"]], "B": [header, ["P", "A"]]}
    )
    assert all(r.errors for r in read_responses(p, {1: {"A"}}).rows)


def test_xlsx_native_numeric_option_and_boolean_rejection(tmp_path):
    p = workbook(
        tmp_path / "number.xlsx",
        {"Yanıtlar": [["participant_id", 1], ["P", 1], ["Q", True]]},
    )
    r = read_responses(p, {1: {"1", "2"}})
    assert r.rows[0].answers == [[1, "1"]] and not r.rows[0].errors
    assert r.rows[1].errors


@pytest.mark.parametrize(
    "name,data",
    [("bad.xlsx", b"not a workbook"), ("bad.csv", b"\xff\xfe"), ("bad.txt", b"any")],
)
def test_broken_input_is_turkish_file_error(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    with pytest.raises(ResponseFileError) as e:
        read_responses(p, ITEMS)
    assert name in str(e.value)


def test_missing_identity_wrong_row_length_and_case_sensitive_options(tmp_path):
    p = csv_file(
        tmp_path / "rows.csv",
        rows=[["", *["A"] * 8], ["P", "a", *["A"] * 7], ["Q", "A"]],
    )
    r = read_responses(p, ITEMS)
    assert all(row.errors for row in r.rows)


def test_empty_file_and_header_formula(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("")
    with pytest.raises(ResponseFileError):
        read_responses(p, ITEMS)
    p = workbook(tmp_path / "header.xlsx", {"Hata": [["=1+1", "Q1"], ["P", "A"]]})
    with pytest.raises(ResponseFileError) as e:
        read_responses(p, {1: {"A"}})
    assert e.value.issues[0].sheet == "Hata" and e.value.issues[0].row == 1


def test_empty_xlsx_sheet_is_skipped_and_alias_nfc_duplicates_detected(tmp_path):
    p = workbook(
        tmp_path / "empty-sheet.xlsx",
        {
            "Boş": [],
            "Yanıtlar": [["alias", "Q1"], ["Çetin", "A"], ["C\u0327etin", "A"]],
        },
    )
    rows = read_responses(p, {1: {"A"}}).rows
    assert len(rows) == 2 and all(r.errors for r in rows)


def test_blank_and_whitespace_cells_normalize_consistently(tmp_path):
    from lai.assessment.files import normalize_responses

    assert normalize_responses([(1, " A "), (2, "   "), (3, None)]) == [
        [1, "A"],
        [2, None],
        [3, None],
    ]
    with pytest.raises(ValueError, match="1. soru"):
        normalize_responses([(1, True)])


def test_explicit_file_limits_and_sheet_selection(tmp_path, monkeypatch):
    from lai.assessment import files

    p = csv_file(tmp_path / "limit.csv", rows=[["P", *["A"] * 8], ["Q", *["A"] * 8]])
    monkeypatch.setattr(files, "MAX_ROWS", 1)
    with pytest.raises(ResponseFileError):
        read_responses(p, ITEMS)
    monkeypatch.setattr(files, "MAX_BYTES", 2)
    with pytest.raises(ResponseFileError):
        read_responses(p, ITEMS)


def test_missing_identity_and_alias_column_formula_has_context(tmp_path):
    p = workbook(
        tmp_path / "identity.xlsx",
        {"Kimlik": [["participant_id", "alias", "Q1"], ["P", "=1+1", "A"]]},
    )
    row = read_responses(p, {1: {"A"}}).rows[0]
    assert row.errors[0]["participant"] == "P" and row.errors[0]["column"] == "alias"
