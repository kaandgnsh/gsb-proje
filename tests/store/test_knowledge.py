import pytest

from lai.store.enums import Confidence, Source
from lai.store.store import Store


def _rule(store, iid, **kw):
    args = dict(entry_key="rule:reverse:item:12", kind="rule", rule_type="reverse",
                content={"item": 12, "reverse": True}, summary="12. madde ters puanlanıyor olabilir",
                confidence=Confidence.WEAKLY_INFERRED, source=Source.AI_INFERENCE,
                item_number=12, dimension_code="E-I")
    args.update(kw)
    return store.knowledge.record(iid, **args)


def test_record_and_revise_keep_history(store, inv):
    iid, _ = inv
    v1 = _rule(store, iid)
    assert (v1.version, v1.supersedes_id, v1.validation) == (1, None, "pending")
    v2 = store.knowledge.revise(v1.id, confidence=Confidence.STRONGLY_INFERRED,
                                source=Source.EXAMPLE_FIT, reason="örnek sonuçlarla tutarlı")
    assert (v2.version, v2.supersedes_id, v2.content) == (2, v1.id, v1.content)
    assert [e.id for e in store.knowledge.current(iid)] == [v2.id]
    hist = store.knowledge.history(iid, "rule:reverse:item:12")
    assert [h.version for h in hist] == [1, 2]
    assert hist[0].confidence == "WEAKLY_INFERRED"  # eski sürüm aynen duruyor


def test_revise_only_latest(store, inv):
    iid, _ = inv
    v1 = _rule(store, iid)
    store.knowledge.revise(v1.id, summary="güncel", source=Source.AI_INFERENCE, reason="r")
    with pytest.raises(ValueError, match="en son sürüm"):
        store.knowledge.revise(v1.id, summary="eski üzerinde", source=Source.AI_INFERENCE, reason="r")


def test_uncertain_cannot_become_confirmed_without_user_or_document(store, inv):
    iid, _ = inv
    v1 = _rule(store, iid)
    with pytest.raises(ValueError, match="CONFIRMED"):
        store.knowledge.revise(v1.id, confidence=Confidence.CONFIRMED, source=Source.AI_INFERENCE,
                               reason="model emin")
    with pytest.raises(ValueError):
        store.knowledge.confirm(v1.id, source=Source.AI_INFERENCE, confirmation_ref="x", reason="r")
    with pytest.raises(ValueError):
        store.knowledge.confirm(v1.id, source=Source.USER, confirmation_ref="", reason="r")
    with pytest.raises(ValueError):
        _rule(store, iid, entry_key="baska", confidence=Confidence.CONFIRMED)
    assert store.knowledge.current(iid, confidence=Confidence.CONFIRMED) == []


def test_document_confirmation_with_evidence(store, inv):
    iid, _ = inv
    doc, _ = store.inventory.add_document(iid, file_name="kilavuz.pdf", sha256="a" * 64, kind="pdf")
    ev = store.inventory.add_evidence(kind="document", document_id=doc, page=3, char_start=10,
                                      char_end=60, quote="12. madde ters puanlanır.")
    v1 = _rule(store, iid, evidence_ids=[ev])
    v2 = store.knowledge.confirm(v1.id, source=Source.DOCUMENT, confirmation_ref=f"evidence:{ev}",
                                 reason="kılavuzda açıkça yazıyor")
    assert (v2.confidence, v2.validation, v2.source) == ("CONFIRMED", "validated", "document")
    assert v1.evidence_ids == [ev]
    assert [e.id for e in store.knowledge.current(iid, confidence=Confidence.CONFIRMED)] == [v2.id]


def test_user_correction_example_item_12(store, inv):
    """Kullanıcı: '12. soru ters puanlanıyor.'"""
    iid, _ = inv
    v1 = _rule(store, iid)
    corr, entry = store.knowledge.apply_user_correction(
        iid, entry_key="rule:reverse:item:12", kind="rule", rule_type="reverse",
        content={"item": 12, "reverse": True}, summary="12. madde ters puanlanır",
        explanation="Kullanıcı 12. maddenin ters puanlandığını bildirdi",
        user_text="12. soru ters puanlanıyor.", item_number=12, dimension_code="E-I")
    assert (entry.version, entry.confidence, entry.source) == (2, "CONFIRMED", "user")
    assert corr.target_entry_id == v1.id and corr.resulting_entry_id == entry.id
    ev = store.inventory.get_evidence(corr.evidence_id)
    assert ev["kind"] == "user_statement" and ev["note"] == "12. soru ters puanlanıyor."
    assert entry.confirmation_ref == f"evidence:{corr.evidence_id}"
    assert store.knowledge.history(iid, "rule:reverse:item:12")[0].confidence == "WEAKLY_INFERRED"
    events = [h["event"] for h in store.knowledge.learning_history(iid)]
    assert events == ["knowledge_created", "knowledge_revised", "user_correction"]
    assert store.knowledge.corrections(iid)[0].user_text == "12. soru ters puanlanıyor."


def test_user_correction_for_new_knowledge(store, inv):
    iid, _ = inv
    corr, entry = store.knowledge.apply_user_correction(
        iid, entry_key="rule:missing_policy", kind="rule", rule_type="missing_answer_policy",
        content={"policy": "exclude"}, summary="Boş bırakılan madde puana katılmaz",
        explanation="kullanıcı bilgisi", user_text="Boş cevaplar hesaba katılmaz.")
    assert corr.target_entry_id is None and entry.version == 1 and entry.confidence == "CONFIRMED"


def test_reject_keeps_entry_and_marks_rejected(store, inv):
    iid, _ = inv
    v1 = _rule(store, iid)
    v2 = store.knowledge.reject(v1.id, reason="kullanıcı yanlış dedi", by="kullanıcı")
    assert v2.validation == "rejected" and v2.version == 2
    assert store.knowledge.inferred_rules(iid) == []
    assert len(store.knowledge.history(iid, v1.entry_key)) == 2


def test_persists_across_reopen(tmp_path):
    p = tmp_path / "lai.db"
    s = Store.open(p)
    iid = s.inventory.create_inventory("E")
    s.knowledge.record(iid, entry_key="fact:dims", kind="fact", content={"dims": 4},
                       summary="4 boyut var", confidence=Confidence.STRONGLY_INFERRED,
                       source=Source.AI_INFERENCE)
    s.close()
    s2 = Store.open(p)
    assert [e.summary for e in s2.knowledge.current(iid)] == ["4 boyut var"]
    s2.close()


def test_failed_operation_leaves_no_partial_rows(store, inv):
    iid, _ = inv
    v1 = _rule(store, iid)
    before = len(store.knowledge.learning_history(iid))
    with pytest.raises(TypeError):
        store.knowledge.apply_user_correction(
            iid, entry_key="rule:reverse:item:12", kind="rule",
            content={"x": object()},  # JSON'a çevrilemez: kanıt yazıldıktan sonra patlar
            summary="x", explanation="x", user_text="x")
    evidence_count = store.conn.execute("SELECT COUNT(*) FROM evidence_refs").fetchone()[0]
    assert evidence_count == 0
    assert len(store.knowledge.learning_history(iid)) == before
    assert store.knowledge.current(iid)[0].id == v1.id
    assert store.knowledge.corrections(iid) == []
