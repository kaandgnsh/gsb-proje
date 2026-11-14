"""İçe aktarma servisi: kalıcılık, izlenebilirlik, mükerrer önleme, hata raporu."""
import json
import shutil
import stat

import pytest
pytest_plugins = ["ingest_helpers"]
from synthetic_docs import answer_key_for, make_doc_inventory, write_csv, write_json, write_pdf

from lai.ingest import service
from lai.ingest.model import IngestError
from lai.ingest.report import format_report, report_dict
from lai.ingest.service import import_document

INV = make_doc_inventory(40)


def count(store, table, where="1=1", args=()):
    return store.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


@pytest.fixture
def pdf(tmp_path):
    return write_pdf(INV, tmp_path / "envanter.pdf", answer_key=answer_key_for(INV))


def test_clean_pdf_creates_version_items_options_and_evidence(store, inventory_id, docs_dir, pdf):
    rep = import_document(store, inventory_id, pdf, documents_dir=docs_dir)
    assert rep.status == "imported", format_report(rep)
    assert (rep.item_count, rep.option_count, rep.answer_key_count) == (40, 200, 40)
    assert rep.errors == []
    items = store.inventory.items(rep.inventory_version_id)
    assert [i["number"] for i in items] == list(range(1, 41))
    assert [(i["text"], i["options"]) for i in items] == [
        (it["text"], it["options"]) for it in INV["items"]]
    # her madde ve seçenek kaynağa izlenebilir: belge + sayfa + karakter aralığı + alıntı
    row = store.conn.execute(
        "SELECT e.* FROM items i JOIN evidence_refs e ON e.id = i.evidence_id "
        "JOIN inventory_versions v ON v.id = i.inventory_version_id "
        "WHERE v.id = ? AND i.number = 9", (rep.inventory_version_id,)).fetchone()
    assert row["document_id"] == rep.document_id and row["kind"] == "document"
    assert row["quote"] == INV["items"][8]["text"]
    loc = json.loads(row["locator"])
    assert loc["page"] >= 1 and row["page"] == loc["page"] and row["char_start"] is not None
    assert count(store, "options o JOIN items i ON i.id = o.item_id",
                 "i.inventory_version_id = ? AND o.evidence_id IS NULL",
                 (rep.inventory_version_id,)) == 0
    # sürüm → çalıştırma → belge zinciri
    v = store.conn.execute("SELECT * FROM inventory_versions WHERE id=?",
                           (rep.inventory_version_id,)).fetchone()
    assert v["source_run_id"] == rep.run_id and len(v["content_hash"]) == 64


def test_raw_and_parsed_data_are_stored_separately(store, inventory_id, docs_dir, pdf):
    rep = import_document(store, inventory_id, pdf, documents_dir=docs_dir)
    raw = store.conn.execute("SELECT text, page, locator FROM document_raw_units WHERE document_id=? "
                             "ORDER BY idx", (rep.document_id,)).fetchall()
    assert any(r["text"].startswith("Sayfa 1 /") for r in raw)  # sayfa süsü ham içerikte duruyor
    assert any(r["text"] == "CEVAP ANAHTARI" for r in raw)
    parsed = store.conn.execute("SELECT number, text, raw_first, raw_last FROM parsed_items "
                                "WHERE import_run_id=? ORDER BY seq", (rep.run_id,)).fetchall()
    assert len(parsed) == 40 and all("Sayfa" not in p["text"] for p in parsed)
    first_raw = store.conn.execute("SELECT text FROM document_raw_units WHERE document_id=? AND "
                                   "idx=?", (rep.document_id, parsed[0]["raw_first"])).fetchone()
    assert first_raw["text"].startswith("1. ")
    assert count(store, "parsed_answer_keys", "import_run_id=?", (rep.run_id,)) == 40


def test_original_file_is_kept_read_only_and_content_addressed(store, inventory_id, docs_dir, pdf):
    rep = import_document(store, inventory_id, pdf, documents_dir=docs_dir)
    stored = store.conn.execute("SELECT stored_path FROM source_documents WHERE id=?",
                                (rep.document_id,)).fetchone()[0]
    from pathlib import Path
    p = Path(stored)
    assert p.read_bytes() == pdf.read_bytes() and rep.sha256 in p.name
    assert stat.S_IMODE(p.stat().st_mode) == 0o400


def test_same_document_twice_creates_no_duplicates(store, inventory_id, docs_dir, pdf, tmp_path):
    first = import_document(store, inventory_id, pdf, documents_dir=docs_dir)
    before = {t: count(store, t) for t in ("source_documents", "document_raw_units",
                                           "parsed_items", "items", "inventory_versions",
                                           "evidence_refs")}
    copy = shutil.copy(pdf, tmp_path / "baska-ad.pdf")
    for p in (pdf, copy):
        rep = import_document(store, inventory_id, p, documents_dir=docs_dir)
        assert rep.status == "duplicate" and rep.duplicate_of_run_id == first.run_id
        assert rep.inventory_version_id == first.inventory_version_id
        assert "daha önce" in format_report(rep)
    assert {t: count(store, t) for t in before} == before
    assert count(store, "import_runs", "status='duplicate'") == 2


def test_same_content_other_format_does_not_create_new_version(store, inventory_id, docs_dir,
                                                               pdf, tmp_path):
    a = import_document(store, inventory_id, pdf, documents_dir=docs_dir)
    b = import_document(store, inventory_id,
                        write_csv(INV, tmp_path / "e.csv", answer_key=answer_key_for(INV)),
                        documents_dir=docs_dir)
    assert b.status == "imported" and b.inventory_version_id == a.inventory_version_id
    assert b.same_content and "aynı" in format_report(b)
    assert count(store, "inventory_versions") == 1


def test_same_file_in_another_inventory_is_imported_separately(store, inventory_id, docs_dir, pdf):
    import_document(store, inventory_id, pdf, documents_dir=docs_dir)
    other = store.inventory.create_inventory("Başka Envanter")
    rep = import_document(store, other, pdf, documents_dir=docs_dir)
    assert rep.status == "imported"


def test_errors_block_version_but_keep_parsed_data(store, inventory_id, docs_dir, tmp_path):
    items = [it for it in INV["items"] if it["number"] != 17]  # 17 eksik
    p = write_pdf(INV, tmp_path / "eksik.pdf", items=items)
    rep = import_document(store, inventory_id, p, documents_dir=docs_dir)
    assert rep.status == "needs_review" and rep.inventory_version_id is None
    assert [i.code for i in rep.errors] == ["number_gap"] and "17" in rep.errors[0].message
    assert count(store, "parsed_items", "import_run_id=?", (rep.run_id,)) == 39
    assert count(store, "import_issues", "import_run_id=? AND severity='error'", (rep.run_id,)) == 1
    assert count(store, "items") == 0
    text = format_report(rep)
    assert "İNCELEME GEREKİYOR" in text and "number_gap" in text
    # ikinci kez aynı (hatalı) belge → yine mükerrer kayıt yok
    assert import_document(store, inventory_id, p, documents_dir=docs_dir).status == "duplicate"


def test_corrupt_file_fails_with_clear_message_and_record(store, inventory_id, docs_dir, tmp_path, pdf):
    bad = tmp_path / "bozuk.pdf"
    bad.write_bytes(pdf.read_bytes()[:300])
    rep = import_document(store, inventory_id, bad, documents_dir=docs_dir)
    assert rep.status == "failed" and rep.errors[0].code == "corrupt_file"
    assert "bozuk.pdf" in rep.message
    run = store.conn.execute("SELECT * FROM import_runs WHERE id=?", (rep.run_id,)).fetchone()
    assert run["status"] == "failed" and run["document_id"] is not None
    assert "BAŞARISIZ" in format_report(rep)


def test_failed_import_can_be_retried_with_explicit_encoding(store, inventory_id, docs_dir, tmp_path):
    p = write_csv(INV, tmp_path / "win.csv", encoding="cp1254")
    first = import_document(store, inventory_id, p, documents_dir=docs_dir)
    assert first.status == "failed" and first.errors[0].code == "encoding"
    again = import_document(store, inventory_id, p, documents_dir=docs_dir, encoding="cp1254")
    assert again.status == "imported"
    assert store.inventory.items(again.inventory_version_id)[0]["text"] == INV["items"][0]["text"]
    assert count(store, "source_documents") == 1  # aynı belge tek kayıt


def test_unsupported_file_is_reported_not_raised(store, inventory_id, docs_dir, tmp_path):
    p = tmp_path / "eski.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0 eski word")
    rep = import_document(store, inventory_id, p, documents_dir=docs_dir)
    assert rep.status == "failed" and rep.errors[0].code == "legacy_format"


def test_missing_file_raises(store, inventory_id, docs_dir, tmp_path):
    with pytest.raises(IngestError) as e:
        import_document(store, inventory_id, tmp_path / "yok.pdf", documents_dir=docs_dir)
    assert e.value.code == "not_found"


def test_failure_mid_import_leaves_no_partial_rows(store, inventory_id, docs_dir, pdf, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("yapay hata")
    monkeypatch.setattr(service, "_publish_items", boom)
    tables = ("source_documents", "document_raw_units", "import_runs", "parsed_items",
              "inventory_versions", "items", "evidence_refs", "audit_log")
    before = {t: count(store, t) for t in tables}
    with pytest.raises(RuntimeError):
        import_document(store, inventory_id, pdf, documents_dir=docs_dir)
    assert {t: count(store, t) for t in tables} == before


def test_import_is_audited(store, inventory_id, docs_dir, pdf):
    rep = import_document(store, inventory_id, pdf, documents_dir=docs_dir)
    row = store.conn.execute("SELECT detail FROM audit_log WHERE event='document_import' "
                             "AND ref_id=?", (rep.run_id,)).fetchone()
    assert json.loads(row[0])["status"] == "imported"


def test_report_dict_is_json_serialisable(store, inventory_id, docs_dir, tmp_path):
    rep = import_document(store, inventory_id, write_json(INV, tmp_path / "e.json"),
                          documents_dir=docs_dir)
    d = report_dict(rep)
    json.dumps(d, ensure_ascii=False)
    assert d["status"] == "imported" and d["counts"]["items"] == 40
