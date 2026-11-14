"""Okuyucular ham birim üretir; içerik değiştirilmez, konum saklanır."""
import json

import pytest
from synthetic_docs import make_doc_inventory, write_csv, write_docx, write_json, write_pdf, write_xlsx

from lai.ingest.model import IngestError
from lai.ingest.readers import read_csv, read_docx, read_json, read_pdf, read_xlsx

INV = make_doc_inventory(12)


# ------------------------------------------------------------------ PDF
def test_pdf_lines_have_page_offsets_and_bbox(tmp_path):
    p = write_pdf(INV, tmp_path / "e.pdf")
    out = read_pdf(p)
    assert out.meta["page_count"] >= 2
    first = next(u for u in out.units if u.text.startswith("1. "))
    assert first.locator.page == 1 and first.locator.bbox is not None
    # sayfa metni = satırların "\n" ile birleşimi; aralık birebir geri verir
    page1 = "\n".join(u.text for u in out.units if u.locator.page == 1)
    assert page1[first.locator.char_start:first.locator.char_end] == first.text
    assert any(u.locator.page == 2 for u in out.units)


def test_pdf_turkish_characters_and_ligatures_preserved(tmp_path):
    p = write_pdf(INV, tmp_path / "e.pdf")
    text = "\n".join(u.text for u in read_pdf(p).units)
    for ch in "ıİşŞğüÜöÖçÇ":
        assert ch in text, ch
    assert "ﬁ" not in text and "ﬂ" not in text  # ﬁ/ﬂ bağlı harf kalmaz
    # "fikirler" gibi sözcükler kaynaktaki harflerle çıkar
    src = [it["text"] for it in INV["items"] if "fi" in it["text"]]
    assert all(s.split(" ", 3)[0] in text for s in src)


def test_pdf_encrypted_is_rejected(tmp_path):
    import pymupdf
    p = write_pdf(INV, tmp_path / "e.pdf")
    d = pymupdf.open(p)
    d.save(tmp_path / "sifreli.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="gizli",
           owner_pw="gizli")
    with pytest.raises(IngestError) as e:
        read_pdf(tmp_path / "sifreli.pdf")
    assert e.value.code == "encrypted_pdf"


def test_pdf_image_only_page_is_error_blank_page_is_info(tmp_path):
    import pymupdf
    d = pymupdf.open()
    d.new_page()  # boş
    pg = d.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.clear_with(200)
    pg.insert_image(pymupdf.Rect(50, 50, 200, 200), pixmap=pix)
    d.save(tmp_path / "tarama.pdf")
    out = read_pdf(tmp_path / "tarama.pdf")
    codes = {(i.code, i.severity, i.locator.page) for i in out.issues}
    assert ("blank_page", "info", 1) in codes
    assert ("no_text_layer", "error", 2) in codes


def test_pdf_truncated_file(tmp_path):
    p = write_pdf(INV, tmp_path / "e.pdf")
    data = p.read_bytes()
    bad = tmp_path / "kesik.pdf"
    bad.write_bytes(data[:200])
    with pytest.raises(IngestError) as e:
        read_pdf(bad)
    assert e.value.code == "corrupt_file" and "kesik.pdf" in e.value.message


# ------------------------------------------------------------------ DOCX
def test_docx_paragraph_order_and_locator(tmp_path):
    out = read_docx(write_docx(INV, tmp_path / "e.docx"))
    texts = [u.text for u in out.units]
    assert texts[0] == "SENTETİK KİŞİLİK ENVANTERİ"
    i = texts.index(f"1. {INV['items'][0]['text']}")
    assert texts[i + 1] == "A) Kesinlikle katılıyorum"
    assert out.units[i].locator.paragraph == i + 1  # boş paragraf yok


def test_docx_auto_numbering_is_computed(tmp_path):
    out = read_docx(write_docx(INV, tmp_path / "e.docx", auto_numbering=True))
    texts = [u.text for u in out.units]
    for it in INV["items"]:
        assert f"{it['number']}. {it['text']}" in texts
    assert out.meta["auto_numbered"] == len(INV["items"])
    assert any(i.code == "auto_numbering" and i.severity == "info" for i in out.issues)


def test_docx_table_rows_keep_cells(tmp_path):
    out = read_docx(write_docx(INV, tmp_path / "e.docx", as_table=True))
    rows = [u for u in out.units if u.cells]
    assert rows[0].cells[:2] == ("Soru No", "Soru Metni")
    assert rows[1].cells[0] == "1" and rows[1].cells[1] == INV["items"][0]["text"]
    assert rows[1].locator.describe() == "Tablo 1, satır 2"
    assert json.loads(rows[1].text) == list(rows[1].cells)


def test_docx_corrupt(tmp_path):
    p = tmp_path / "bozuk.docx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 50)
    with pytest.raises(IngestError) as e:
        read_docx(p)
    assert e.value.code == "corrupt_file"


# ------------------------------------------------------------------ XLSX
def test_xlsx_sheet_and_row_locators(tmp_path):
    out = read_xlsx(write_xlsx(INV, tmp_path / "e.xlsx", extra_sheet_first=True))
    assert out.meta["sheets"] == ["Yönerge", "Sorular"]
    data = [u for u in out.units if u.locator.sheet == "Sorular"]
    assert data[0].cells[0] == "Soru No"
    assert data[1].cells[:2] == ("1", INV["items"][0]["text"])  # 1 (int) → "1"
    assert data[1].locator.row == 2


# ------------------------------------------------------------------ CSV
@pytest.mark.parametrize("delim", [";", ",", "\t"])
def test_csv_delimiters(tmp_path, delim):
    out = read_csv(write_csv(INV, tmp_path / "e.csv", delimiter=delim))
    assert out.meta["delimiter"] == delim
    assert out.units[1].cells[1] == INV["items"][0]["text"]


def test_csv_multiline_cell_row_numbers(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text('no;soru\n1;"çok\nsatırlı"\n2;İkinci\n', encoding="utf-8")
    out = read_csv(p)
    assert out.units[1].cells == ("1", "çok\nsatırlı") and out.units[1].locator.row == 2
    assert out.units[2].locator.row == 4


def test_csv_non_utf8_is_not_guessed(tmp_path):
    p = write_csv(INV, tmp_path / "win.csv", encoding="cp1254")
    with pytest.raises(IngestError) as e:
        read_csv(p)
    assert e.value.code == "encoding" and "cp1254" in e.value.message
    out = read_csv(p, encoding="cp1254")  # açıkça seçilirse doğru okunur
    assert out.units[1].cells[1] == INV["items"][0]["text"]


# ------------------------------------------------------------------ JSON
def test_json_syntax_error_has_line_and_column(tmp_path):
    p = tmp_path / "b.json"
    p.write_text('{"format": "lai-inventory",\n "items": [1, 2,]}', encoding="utf-8")
    with pytest.raises(IngestError) as e:
        read_json(p)
    assert e.value.code == "invalid_json" and "satır 2" in e.value.message


def test_json_keeps_raw_text(tmp_path):
    p = write_json(INV, tmp_path / "e.json")
    out = read_json(p)
    assert out.units[0].text == p.read_text(encoding="utf-8")
    assert out.data["items"][0]["text"] == INV["items"][0]["text"]
