"""Başvuru belgesi içe aktarma, belge derlemi ve birebir alıntı bulma."""
import pytest
from synthetic_docs import GUIDE_LINES, make_doc_inventory, write_guide_docx, write_guide_pdf, write_xlsx

from lai.ingest.model import IngestError
from lai.ingest.service import import_document, import_reference_document
from lai.learning.corpus import Corpus


def test_reference_pdf_is_stored_as_raw_text_once(store, inventory_id, docs_dir, tmp_path):
    p = write_guide_pdf(tmp_path / "yönerge.pdf")
    r1 = import_reference_document(store, inventory_id, p, documents_dir=docs_dir)
    assert not r1.duplicate and r1.unit_count == len(GUIDE_LINES) and r1.kind == "pdf"
    texts = [t for (t,) in store.conn.execute(
        "SELECT text FROM document_raw_units WHERE document_id=? ORDER BY idx", (r1.document_id,))]
    assert texts == GUIDE_LINES
    r2 = import_reference_document(store, inventory_id, p, documents_dir=docs_dir)
    assert r2.duplicate and r2.document_id == r1.document_id
    assert store.conn.execute("SELECT COUNT(*) FROM reference_documents").fetchone()[0] == 1
    assert store.conn.execute("SELECT COUNT(*) FROM document_raw_units").fetchone()[0] == \
        len(GUIDE_LINES)
    # belge maddeler içermediği için envanter sürümü oluşmaz
    assert store.conn.execute("SELECT COUNT(*) FROM inventory_versions").fetchone()[0] == 0


def test_reference_rejects_unreadable(store, inventory_id, docs_dir, tmp_path):
    p = tmp_path / "bos.pdf"
    p.write_bytes(b"")
    with pytest.raises(IngestError) as e:
        import_reference_document(store, inventory_id, p, documents_dir=docs_dir)
    assert e.value.code == "empty_file"


def test_inventory_document_can_also_be_a_reference(store, inventory_id, docs_dir, tmp_path):
    inv = make_doc_inventory(5)
    p = write_xlsx(inv, tmp_path / "e.xlsx")
    rep = import_document(store, inventory_id, p, documents_dir=docs_dir)
    ref = import_reference_document(store, inventory_id, p, documents_dir=docs_dir)
    assert ref.document_id == rep.document_id and not ref.duplicate


@pytest.fixture
def guide(store, inventory_id, docs_dir, tmp_path):
    p = write_guide_pdf(tmp_path / "y.pdf")
    return Corpus.for_document(store, import_reference_document(
        store, inventory_id, p, documents_dir=docs_dir).document_id)


def test_corpus_text_is_units_joined(guide):
    assert guide.text == "\n".join(GUIDE_LINES)
    assert guide.file_name == "y.pdf"


def test_quote_found_across_line_break_and_extra_spaces(guide):
    q = "bir puan verir.  Ters   puanlanan\nmaddelerde"
    hit = guide.find(q)
    assert hit is not None
    assert hit.text == "bir puan verir.\nTers puanlanan maddelerde"  # belgedeki birebir metin
    ev = hit.evidence_fields()
    assert ev["page"] == 1 and ev["quote"] == hit.text
    assert ev["locator"]["line"] == 4


def test_quote_char_span_is_page_relative(guide):
    hit = guide.find("12. madde ters puanlanır")
    ev = hit.evidence_fields()
    page_text = guide.text  # tek sayfa
    assert page_text[ev["char_start"]:ev["char_end"]] == "12. madde ters puanlanır"


@pytest.mark.parametrize("q", ["12. madde TERS puanlanır", "12. madde ters puanlanir",
                               "13. madde ters puanlanır", "madde ters puanlanır."[:-1] + "!"])
def test_quote_differing_in_letters_is_not_found(guide, q):
    assert guide.find(q) is None


def test_occurrences_counted(guide):
    hit = guide.find("kutbuna")
    assert hit.occurrences == 2


def test_docx_reference_paragraph_locator(store, inventory_id, docs_dir, tmp_path):
    p = write_guide_docx(tmp_path / "y.docx")
    c = Corpus.for_document(store, import_reference_document(
        store, inventory_id, p, documents_dir=docs_dir).document_id)
    ev = c.find("Puanlar eşit çıkarsa").evidence_fields()
    assert ev["page"] is None and ev["locator"]["paragraph"] == 7
    assert ev["char_start"] == 0


def test_table_rows_render_as_cells(store, inventory_id, docs_dir, tmp_path):
    inv = make_doc_inventory(3)
    p = write_xlsx(inv, tmp_path / "e.xlsx")
    rep = import_document(store, inventory_id, p, documents_dir=docs_dir)
    c = Corpus.for_document(store, rep.document_id)
    first = inv["items"][0]
    assert f"1 | {first['text']}" in c.text and '["' not in c.text
    ev = c.find(first["text"]).evidence_fields()
    assert ev["locator"]["sheet"] == "Sorular" and ev["locator"]["row"] == 2
    assert ev["char_start"] is None  # tablo hücresinde sayfa içi aralık yok


def test_chunks_cover_all_text(guide):
    chunks = guide.chunks(max_chars=120)
    assert len(chunks) > 1 and chunks[0].start == 0 and chunks[-1].end == len(guide.text)
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.start <= a.end  # boşluk yok (örtüşme olabilir)
