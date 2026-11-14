import pytest

from lai.store.enums import Confidence, ResponseKind
from lai.store.store import Store

Q = dict(group_key="E-I:ters-puanlama",
         missing_info="12. maddenin ters puanlanıp puanlanmadığı",
         why_it_matters="Ters puanlama E-I toplamının yönünü değiştirir",
         related={"items": [12], "dimensions": ["E-I"]},
         evidence_summary="Kılavuzda 'bazı maddeler ters puanlanır' yazıyor; madde listesi yok",
         impact="E-I boyut puanı ve tipi değişebilir",
         question_text="12. madde ters mi puanlanıyor?",
         choices=["Evet, ters puanlanıyor", "Hayır, düz puanlanıyor"])


def _req(store, iid, status=Confidence.UNKNOWN, key="reverse:item:12"):
    return store.questions.upsert_requirement(
        iid, req_key=key, category="reverse_scoring", description="12. madde ters mi?",
        critical=True, status=status, reason="belgede yok", dimension_code="E-I", item_number=12)


def test_requirement_matrix_and_status_history(store, inv):
    iid, _ = inv
    r = _req(store, iid)
    assert (r.status, r.critical) == ("UNKNOWN", True)
    _req(store, iid, status=Confidence.UNKNOWN)  # aynı durum: geçmişe yeni olay yok
    r2 = _req(store, iid, status=Confidence.CONFIRMED)
    assert r2.id == r.id and r2.status == "CONFIRMED"
    events = [(h["event"], h["detail"].get("to")) for h in store.knowledge.learning_history(iid)]
    assert events == [("requirement_added", None), ("requirement_status", "CONFIRMED")]
    assert [m.req_key for m in store.questions.matrix(iid)] == ["reverse:item:12"]


def test_question_has_all_six_parts(store, inv):
    iid, _ = inv
    q, new = store.questions.ask(iid, dedupe_key="reverse:item:12", **Q)
    assert new
    for f in ("missing_info", "why_it_matters", "related", "evidence_summary", "impact",
              "question_text"):
        assert getattr(q, f)
    assert q.related == {"items": [12], "dimensions": ["E-I"]}
    assert q.choices == Q["choices"] and q.status == "open"


def test_same_question_not_asked_twice(store, inv):
    iid, _ = inv
    q1, _ = store.questions.ask(iid, dedupe_key="reverse:item:12", **Q)
    q2, new = store.questions.ask(iid, dedupe_key="reverse:item:12", **Q)
    assert q2.id == q1.id and not new
    store.questions.respond(q1.id, ResponseKind.CHOICE, "Evet, ters puanlanıyor")
    q3, new = store.questions.ask(iid, dedupe_key="reverse:item:12", **Q)
    assert q3.id == q1.id and not new and q3.status == "answered"
    assert store.questions.pending(iid) == []


@pytest.mark.parametrize("kind,value,status", [
    (ResponseKind.CHOICE, "Hayır, düz puanlanıyor", "answered"),
    (ResponseKind.TEXT, "Bence ters ama emin değilim, kılavuzun eski sürümünde öyleydi", "answered"),
    (ResponseKind.DONT_KNOW, None, "dont_know"),
    (ResponseKind.LATER, None, "later"),
    (ResponseKind.SKIP, None, "skipped"),
])
def test_five_response_kinds(store, inv, kind, value, status):
    iid, _ = inv
    q, _ = store.questions.ask(iid, dedupe_key="k", **Q)
    assert store.questions.respond(q.id, kind, value).status == status
    assert store.questions.responses(q.id)[-1]["kind"] == str(kind)


def test_invalid_responses_rejected(store, inv):
    iid, _ = inv
    q, _ = store.questions.ask(iid, dedupe_key="k", **Q)
    with pytest.raises(ValueError):
        store.questions.respond(q.id, ResponseKind.CHOICE, "listede olmayan seçenek")
    with pytest.raises(ValueError):
        store.questions.respond(q.id, ResponseKind.TEXT, "   ")
    assert store.questions.get(q.id).status == "open"


def test_later_is_persistent_and_skipped_can_be_reopened(tmp_path):
    p = tmp_path / "lai.db"
    s = Store.open(p)
    iid = s.inventory.create_inventory("E")
    a, _ = s.questions.ask(iid, dedupe_key="a", **{**Q, "group_key": "g1"})
    b, _ = s.questions.ask(iid, dedupe_key="b", **{**Q, "group_key": "g0"})
    s.questions.respond(a.id, ResponseKind.LATER)
    s.questions.respond(b.id, ResponseKind.SKIP)
    s.close()
    s = Store.open(p)
    assert [q.id for q in s.questions.pending(iid)] == [a.id]
    reopened, new = s.questions.ask(iid, dedupe_key="b", **Q)
    assert not new and reopened.id == b.id and reopened.status == "open"
    assert [q.group_key for q in s.questions.pending(iid)] == ["g0", "g1"]  # gruplu sıra
    assert "question_reopened" in [h["event"] for h in s.knowledge.learning_history(iid)]
    s.close()
