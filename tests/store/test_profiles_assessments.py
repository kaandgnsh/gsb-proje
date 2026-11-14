import pytest

from lai.store.enums import AssessmentStatus, Confidence, Source
from synthetic import load_into, make_inventory, make_responses


@pytest.fixture
def setup(store):
    inv = make_inventory(8)
    iid, ver = load_into(store, inv)
    store.knowledge.record(iid, entry_key="rule:reverse:item:3", kind="rule", rule_type="reverse",
                           content={"item": 3}, summary="3. madde ters olabilir",
                           confidence=Confidence.WEAKLY_INFERRED, source=Source.AI_INFERENCE)
    store.questions.upsert_requirement(iid, req_key="reverse:item:3", category="reverse_scoring",
                                       description="3. madde ters mi?", critical=True,
                                       status=Confidence.UNKNOWN, reason="belirsiz")
    pid = store.profiles.ensure_profile(iid, "Varsayılan")
    return iid, ver, pid


def test_profile_versions_are_frozen_snapshots(store, setup):
    iid, _, pid = setup
    v1 = store.profiles.publish_version(pid, note="ilk")
    assert v1.version == 1 and v1.provisional
    assert v1.snapshot["rules"][0]["confidence"] == "WEAKLY_INFERRED"
    assert v1.snapshot["unconfirmed_critical"] == ["reverse:item:3"]
    store.knowledge.apply_user_correction(iid, entry_key="rule:reverse:item:3", kind="rule",
                                          rule_type="reverse", content={"item": 3},
                                          summary="3. madde ters", explanation="kullanıcı",
                                          user_text="3. soru ters puanlanıyor.")
    store.questions.upsert_requirement(iid, req_key="reverse:item:3", category="reverse_scoring",
                                       description="3. madde ters mi?", critical=True,
                                       status=Confidence.CONFIRMED, reason="kullanıcı onayı")
    v2 = store.profiles.publish_version(pid, note="kullanıcı düzeltmesi")
    assert v2.version == 2 and not v2.provisional and v2.content_hash != v1.content_hash
    again = store.profiles.get_version(v1.id)
    assert again.snapshot == v1.snapshot and again.content_hash == v1.content_hash
    assert [v.version for v in store.profiles.versions(pid)] == [1, 2]


def test_republish_without_changes_returns_same_version(store, setup):
    _, _, pid = setup
    a = store.profiles.publish_version(pid, note="a")
    b = store.profiles.publish_version(pid, note="b")
    assert a.id == b.id


def test_profile_without_rules_is_provisional(store):
    iid = store.inventory.create_inventory("Boş")
    pid = store.profiles.ensure_profile(iid, "p")
    v = store.profiles.publish_version(pid, note="boş")
    assert v.provisional and v.snapshot["rules"] == []


def test_anonymous_participant_and_optional_fields(store):
    p = store.participants.create()
    assert p.code.startswith("ANON-") and len(p.code) == 13
    assert (p.alias, p.full_name, p.contact, p.category) == (None, None, None, None)
    q = store.participants.create(code="K-007", alias="Deniz", category="danışan",
                                  attributes={"yaş_aralığı": "25-34"})
    assert store.participants.find_by_code("K-007").attributes == {"yaş_aralığı": "25-34"}
    with pytest.raises(ValueError, match="zaten"):
        store.participants.create(code="K-007")
    store.participants.update_details(q.id, contact="deniz@example.invalid")
    assert store.participants.get(q.id).contact == "deniz@example.invalid"
    with pytest.raises(ValueError):
        store.participants.update_details(q.id, code="değişmez")


def test_response_set_with_missing_answers(store, setup):
    _, ver, _ = setup
    p = store.participants.create()
    rs = store.participants.add_response_set(p.id, ver, {1: "A", 2: None, 3: "E"}, source_label="t")
    assert store.participants.responses(rs) == {1: "A", 2: None, 3: "E"}
    with pytest.raises(ValueError):
        store.participants.add_response_set(p.id, ver, {0: "A"})


def test_assessments_are_never_changed_by_new_knowledge(store, setup):
    iid, ver, pid = setup
    p = store.participants.create()
    rs = store.participants.add_response_set(p.id, ver, make_responses(8, 1)[0])
    v1 = store.profiles.publish_version(pid, note="v1")
    with pytest.raises(ValueError, match="geçici"):
        store.assessments.record(response_set_id=rs, profile_version_id=v1.id,
                                 status=AssessmentStatus.VERIFIED, results={}, limitations=[],
                                 engine_version="test")
    a1 = store.assessments.record(response_set_id=rs, profile_version_id=v1.id,
                                  status=AssessmentStatus.PROVISIONAL,
                                  results={"E-I": {"E": 3, "I": 1}},
                                  limitations=["3. maddenin yönü doğrulanmadı"], engine_version="test")
    # yeni bilgi ve yeni profil sürümü
    store.knowledge.apply_user_correction(iid, entry_key="rule:reverse:item:3", kind="rule",
                                          rule_type="reverse", content={"item": 3},
                                          summary="3. madde ters", explanation="k", user_text="k")
    store.questions.upsert_requirement(iid, req_key="reverse:item:3", category="reverse_scoring",
                                       description="d", critical=True, status=Confidence.CONFIRMED,
                                       reason="k")
    v2 = store.profiles.publish_version(pid, note="v2")
    old = store.assessments.get(a1.id)
    assert (old.status, old.results, old.profile_version_id) == ("PROVISIONAL", a1.results, v1.id)
    a2 = store.assessments.record(response_set_id=rs, profile_version_id=v2.id,
                                  status=AssessmentStatus.VERIFIED, results={"E-I": {"E": 2, "I": 2}},
                                  limitations=[], engine_version="test", recalculated_from=a1.id)
    assert a2.recalculated_from == a1.id and a2.input_hash != a1.input_hash
    assert [a.id for a in store.assessments.for_response_set(rs)] == [a1.id, a2.id]
    assert store.assessments.get(a1.id).results == {"E-I": {"E": 3, "I": 1}}


def test_input_hash_is_deterministic(store, setup):
    _, ver, pid = setup
    p = store.participants.create()
    rs = store.participants.add_response_set(p.id, ver, {1: "A", 2: "B"})
    v = store.profiles.publish_version(pid, note="v")
    kw = dict(response_set_id=rs, profile_version_id=v.id, status=AssessmentStatus.PROVISIONAL,
              results={}, limitations=[], engine_version="1.0")
    assert store.assessments.record(**kw).input_hash == store.assessments.record(**kw).input_hash
    assert store.assessments.record(**{**kw, "engine_version": "1.1"}).input_hash != \
        store.assessments.record(**kw).input_hash


def test_feedback_is_kept(store, setup):
    _, ver, pid = setup
    p = store.participants.create()
    rs = store.participants.add_response_set(p.id, ver, {1: "A"})
    a = store.assessments.record(response_set_id=rs, profile_version_id=store.profiles.publish_version(
        pid, note="v").id, status=AssessmentStatus.PROVISIONAL, results={}, limitations=[],
        engine_version="t")
    store.assessments.add_feedback(a.id, "E-I sonucu bana uygun görünmüyor")
    assert store.assessments.feedback(a.id)[0]["feedback"] == "E-I sonucu bana uygun görünmüyor"
