"""Şema v3 (içe aktarma tabloları) ve dosya türü tespiti."""
import hashlib
import sqlite3
import zipfile

import pytest

from lai.ingest.detect import detect_kind, sha256_file
from lai.ingest.model import IngestError, Locator


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_schema_v3_tables_and_columns(store):
    t = _tables(store.conn)
    for name in ("document_raw_units", "import_runs", "parsed_items", "parsed_options",
                 "parsed_answer_keys", "import_issues"):
        assert name in t, name
    assert "locator" in _cols(store.conn, "evidence_refs")
    assert "evidence_id" in _cols(store.conn, "options")
    assert {"source_run_id", "content_hash"} <= _cols(store.conn, "inventory_versions")


def _seed(conn):
    conn.execute("INSERT INTO inventories(id, name) VALUES (1, 'S')")
    conn.execute("INSERT INTO inventory_versions(id, inventory_id, version) VALUES (1, 1, 1)")
    conn.execute("INSERT INTO source_documents(id, inventory_id, file_name, sha256, kind) "
                 "VALUES (1, 1, 'a.pdf', ?, 'pdf')", ("a" * 64,))
    conn.execute("INSERT INTO document_raw_units(document_id, idx, text, locator) "
                 "VALUES (1, 0, 'ham', '{}')")
    conn.execute("INSERT INTO import_runs(id, inventory_id, document_id, file_name, sha256, kind, "
                 "parser_version, status) VALUES (1, 1, 1, 'a.pdf', ?, 'pdf', '1', 'needs_review')",
                 ("a" * 64,))
    conn.execute("INSERT INTO parsed_items(id, import_run_id, seq, number, text, locator) "
                 "VALUES (1, 1, 0, 1, 'Soru', '{}')")
    conn.execute("INSERT INTO parsed_options(parsed_item_id, seq, code, text, locator) "
                 "VALUES (1, 0, 'A', 'Evet', '{}')")
    conn.execute("INSERT INTO parsed_answer_keys(import_run_id, seq, item_number, value, locator) "
                 "VALUES (1, 0, 1, 'A', '{}')")
    conn.execute("INSERT INTO import_issues(import_run_id, severity, code, message) "
                 "VALUES (1, 'error', 'x', 'y')")
    conn.execute("INSERT INTO items(id, inventory_version_id, number, text) VALUES (1, 1, 1, 'S')")
    conn.execute("INSERT INTO options(item_id, code, text, position) VALUES (1, 'A', 'E', 0)")


@pytest.mark.parametrize("table", ["document_raw_units", "import_runs", "parsed_items",
                                   "parsed_options", "parsed_answer_keys", "import_issues",
                                   "items", "options"])
def test_ingested_rows_are_immutable(store, table):
    _seed(store.conn)
    with pytest.raises(sqlite3.IntegrityError):
        col = store.conn.execute(f"PRAGMA table_info({table})").fetchall()[-1][1]
        store.conn.execute(f"UPDATE {table} SET {col} = {col}")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(f"DELETE FROM {table}")


def test_import_run_status_is_constrained(store):
    _seed(store.conn)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT INTO import_runs(inventory_id, file_name, sha256, kind, "
                           "parser_version, status) VALUES (1, 'b', ?, 'pdf', '1', 'tamam')",
                           ("b" * 64,))


# ------------------------------------------------------------------ tür tespiti
def _zip(path, member):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(member, "<x/>")
    return path


def test_detect_by_extension_and_magic(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.7\n...")
    (tmp_path / "a.csv").write_text("no;soru\n1;Işık\n", encoding="utf-8")
    (tmp_path / "a.json").write_text('﻿  {"format": "lai-inventory"}', encoding="utf-8")
    _zip(tmp_path / "a.docx", "word/document.xml")
    _zip(tmp_path / "a.xlsx", "xl/workbook.xml")
    for k in ("pdf", "csv", "json", "docx", "xlsx"):
        assert detect_kind(tmp_path / f"a.{k}") == k
    assert detect_kind(tmp_path / "a.pdf", declared="pdf") == "pdf"


@pytest.mark.parametrize("name,content,code", [
    ("bos.pdf", b"", "empty_file"),
    ("sahte.pdf", b"PK\x03\x04 zip", "kind_mismatch"),
    ("sahte.docx", b"%PDF-1.4", "kind_mismatch"),
    ("eski.doc", b"\xd0\xcf\x11\xe0", "legacy_format"),
    ("eski.xls", b"\xd0\xcf\x11\xe0", "legacy_format"),
    ("not.txt", b"1. Soru", "unsupported_format"),
    ("bozuk.json", b"\x00\x01binary", "kind_mismatch"),
])
def test_detect_errors_are_explained(tmp_path, name, content, code):
    p = tmp_path / name
    p.write_bytes(content)
    with pytest.raises(IngestError) as e:
        detect_kind(p)
    assert e.value.code == code
    assert e.value.message and name in e.value.message


def test_docx_xlsx_swap_detected(tmp_path):
    p = _zip(tmp_path / "x.docx", "xl/workbook.xml")
    with pytest.raises(IngestError) as e:
        detect_kind(p)
    assert e.value.code == "kind_mismatch"


def test_size_limit(tmp_path):
    p = tmp_path / "buyuk.csv"
    p.write_bytes(b"no,soru\n" + b"1,x\n" * 100)
    with pytest.raises(IngestError) as e:
        detect_kind(p, max_bytes=100)
    assert e.value.code == "file_too_large"


def test_sha256_streams(tmp_path):
    p = tmp_path / "f.bin"
    data = b"\x00\xff" * 700_000
    p.write_bytes(data)
    assert sha256_file(p) == (hashlib.sha256(data).hexdigest(), len(data))


def test_locator_description_is_turkish():
    assert Locator(page=3, line=12).describe() == "sayfa 3, satır 12"
    assert Locator(page=3, page_end=4, line=40).describe() == "sayfa 3–4, satır 40"
    assert Locator(sheet="Sorular", row=5, column="B").describe() == "Sorular!B5"
    assert Locator(sheet="Sorular", row=5).describe() == "Sorular, satır 5"
    assert Locator(row=7).describe() == "satır 7"
    assert Locator(paragraph=9).describe() == "paragraf 9"
    assert Locator(json_path="$.items[3]").describe() == "$.items[3]"
    d = Locator(page=1, char_start=0, char_end=5).to_dict()
    assert d == {"page": 1, "char_start": 0, "char_end": 5}
    assert Locator.from_dict(d) == Locator(page=1, char_start=0, char_end=5)
