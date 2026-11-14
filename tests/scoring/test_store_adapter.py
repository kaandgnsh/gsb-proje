"""Donmuş sürüm ve saf M8 giriş noktası arasında sentetik entegrasyon."""

import pytest
from test_engine import DIMS, POLES, fixture

from lai.core.rules import rule_key
from lai.core.scoring import ScoringInputError
from lai.scoring import evaluate
from lai.store.store import Store


@pytest.fixture
def saved(tmp_path):
    s = Store.open(tmp_path / "sentetik.db")
    i = s.inventory.create_inventory("Sentetik M7")
    v = s.inventory.add_version(i)
    s.inventory.add_items(
        v,
        [
            {
                "number": n,
                "text": f"Sentetik {n}",
                "options": [("A", "Bir"), ("B", "İki")],
            }
            for n in range(1, 9)
        ],
    )
    for d, ps in zip(DIMS, POLES, strict=True):
        s.inventory.add_dimension(i, d, poles=[(p, None) for p in ps])
    _, p = fixture()
    for e in p.snapshot["rules"]:
        b = e["content"]["body"]
        s.knowledge.apply_user_correction(
            i,
            entry_key=e["entry_key"],
            content=e["content"],
            summary="sentetik",
            explanation="Açık sentetik kural",
            user_text="Doğruluyorum",
            rule_type=e["rule_type"],
            dimension_code=b["dimension"],
        )
    s.knowledge.apply_user_correction(
        i,
        entry_key="rule:missing_answer_policy:dim:*",
        rule_type="missing_answer_policy",
        dimension_code="*",
        content={"body": {"dimension": "*", "policy": "skip"}},
        summary="Eksik cevap atlanır",
        explanation="Sentetik açık politika",
        user_text="Atla",
    )
    pid = s.profiles.ensure_profile(i)
    yield s, i, v, pid
    s.close()


def test_adapter_readonly_sources_and_frozen_profiles(saved):
    s, i, v, pid = saved
    first = s.profiles.publish_version(pid)
    before = s.conn.total_changes
    a = evaluate(
        s,
        inventory_version_id=v,
        profile_version_id=first.id,
        responses={n: "A" for n in range(1, 9)},
    )
    assert (
        s.conn.total_changes == before
        and a.status == "VERIFIED"
        and a.full_profile == "ESTJ"
    )
    assert a.dimensions[0].rules[0]["source"] == "user"
    assert a.dimensions[0].rules[0]["evidence_ids"]
    assert first.snapshot["catalog_version"] == 2
    assert first.snapshot["inventory_version_id"] == v
    assert first.snapshot["dimension_order"] == list(DIMS)
    assert s.profiles.publish_version(pid).id == first.id
    key = rule_key("default_option_map", {"dimension": "E-I"})
    s.knowledge.apply_user_correction(
        i,
        entry_key=key,
        content={"body": {"dimension": "E-I", "options": {"A": "I", "B": "E"}}},
        summary="Düzeltme",
        explanation="Yeni sentetik kaynak",
        user_text="Eşleme değişti",
    )
    second = s.profiles.publish_version(pid)
    b = evaluate(
        s,
        inventory_version_id=v,
        profile_version_id=second.id,
        responses={n: "A" for n in range(1, 9)},
    )
    assert b.full_profile == "ISTJ" and a.full_profile == "ESTJ"
    assert (
        evaluate(
            s,
            inventory_version_id=v,
            profile_version_id=first.id,
            responses={n: "A" for n in range(1, 9)},
        ).to_dict()
        == a.to_dict()
    )
    assert first.snapshot == s.profiles.get_version(first.id).snapshot


def test_previous_assessment_remains_unchanged(saved):
    s, i, v, pid = saved
    pv = s.profiles.publish_version(pid)
    responses = {n: "A" for n in range(1, 9)}
    r = evaluate(
        s, inventory_version_id=v, profile_version_id=pv.id, responses=responses
    )
    participant = s.participants.create(code="SYNTHETIC-M7")
    rs = s.participants.add_response_set(participant.id, v, responses)
    old = s.assessments.record(
        response_set_id=rs,
        profile_version_id=pv.id,
        status=r.status,
        results=r.to_dict(),
        limitations=[],
        engine_version=r.engine_version,
    )
    s.knowledge.apply_user_correction(
        i,
        entry_key="rule:reverse:item:1:dim:E-I",
        content={"body": {"item": 1, "dimension": "E-I"}},
        rule_type="reverse",
        dimension_code="E-I",
        summary="sentetik",
        explanation="Düzeltme",
        user_text="1 ters",
    )
    newpv = s.profiles.publish_version(pid)
    assert newpv.id != pv.id
    evaluate(
        s, inventory_version_id=v, profile_version_id=newpv.id, responses=responses
    )
    assert s.assessments.get(old.id) == old


def test_exam_version_binding_and_foreign_inventory(saved):
    s, i, _v, pid = saved
    first = s.profiles.publish_version(pid)
    v2 = s.inventory.add_version(i)
    s.inventory.add_items(
        v2,
        [
            {
                "number": n,
                "text": "Sentetik yeni",
                "options": [("A", "Bir"), ("B", "İki")],
            }
            for n in range(1, 9)
        ],
    )
    with pytest.raises(ScoringInputError, match="uyuşmuyor"):
        evaluate(s, inventory_version_id=v2, profile_version_id=first.id, responses={})
    second = s.profiles.publish_version(pid)
    assert second.id != first.id and second.snapshot["inventory_version_id"] == v2
    foreign = s.inventory.create_inventory("Sentetik yabancı")
    fv = s.inventory.add_version(foreign)
    with pytest.raises(ScoringInputError, match="uyuşmuyor"):
        evaluate(s, inventory_version_id=fv, profile_version_id=first.id, responses={})


def test_m6_gap_scope_is_frozen_and_blocks_only_affected_dimension(saved):
    from lai.learning.interactive import analyze

    s, i, v, pid = saved
    analyze(s, i)
    # Synthetic critical scoped requirement, independent of M6's optional questions.
    s.questions.upsert_requirement(
        i,
        req_key="special:T-F",
        category="other",
        description="Eksik formül",
        critical=True,
        status="UNKNOWN",
        reason="bilinmiyor",
        dimension_code="T-F",
    )
    pv = s.profiles.publish_version(pid)
    r = evaluate(
        s,
        inventory_version_id=v,
        profile_version_id=pv.id,
        responses={n: "A" for n in range(1, 9)},
    )
    assert r.dimensions[2].raw is None and r.full_profile is None
    assert r.dimensions[0].raw == {"E": 2, "I": 0}
    req = next(x for x in pv.snapshot["requirements"] if x["req_key"] == "special:T-F")
    assert req["dimensions"] == ["T-F"]


def test_dimension_review_frozen_in_profile(saved):
    s, i, v, pid = saved
    with s.transaction() as c:
        fr = c.execute(
            "INSERT INTO fit_runs(inventory_id,example_count) VALUES (?,1)", (i,)
        ).lastrowid
        c.execute(
            "INSERT INTO dimension_reviews(inventory_id,fit_run_id,dimension_code,detail) VALUES (?,?,?,?)",
            (i, fr, "S-N", '{"possible_rules":["synthetic"]}'),
        )
    pv = s.profiles.publish_version(pid)
    r = evaluate(
        s,
        inventory_version_id=v,
        profile_version_id=pv.id,
        responses={n: "A" for n in range(1, 9)},
    )
    assert r.dimensions[1].raw is None and r.dimensions[0].selected_pole == "E"
    assert pv.snapshot["dimension_reviews"][0]["dimension_code"] == "S-N"


def test_discovered_dimension_order_does_not_change_after_definition_correction(
    tmp_path,
):
    s = Store.open(tmp_path / "synthetic-order.db")
    try:
        i = s.inventory.create_inventory("Sentetik sıra")
        s.inventory.add_version(i)
        for d, ps in zip(DIMS, POLES, strict=True):
            s.knowledge.apply_user_correction(
                i,
                entry_key="rule:dimension_def:dim:" + d,
                rule_type="dimension_def",
                dimension_code=d,
                content={"body": {"dimension": d, "poles": list(ps)}},
                summary="sentetik",
                explanation="açık kural",
                user_text="Onay",
            )
        pid = s.profiles.ensure_profile(i)
        first = s.profiles.publish_version(pid)
        s.knowledge.apply_user_correction(
            i,
            entry_key="rule:dimension_def:dim:E-I",
            content={"body": {"dimension": "E-I", "poles": ["I", "E"]}},
            summary="kutuplar",
            explanation="Sentetik düzeltme",
            user_text="Sıra aynı, kutup sırası farklı",
        )
        second = s.profiles.publish_version(pid)
        assert (
            first.snapshot["dimension_order"]
            == second.snapshot["dimension_order"]
            == list(DIMS)
        )
    finally:
        s.close()
