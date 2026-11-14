"""M7: yalnız el hesabı ve bağımsız sentetik doğruluk kaynağı; gerçek model yok."""

import copy
import json
import socket
import time
from decimal import Decimal, getcontext, localcontext

import pytest

from lai.core.rules import rule_key, validate_rule
from lai.core.scoring import (
    ExamVersion,
    ScoringInputError,
    ScoringProfile,
    score_assessment,
    select_signed_profile,
)
from lai.core.tally import RuleRef, build_model, score

DIMS = ("E-I", "S-N", "T-F", "J-P")
POLES = (("E", "I"), ("S", "N"), ("T", "F"), ("J", "P"))


def entry(t, b, confidence="CONFIRMED"):
    return {
        "entry_id": 1,
        "entry_key": rule_key(t, b),
        "version": 1,
        "rule_type": t,
        "content": {"body": b},
        "confidence": confidence,
        "source": "user" if confidence == "CONFIRMED" else "ai_inference",
        "validation": "validated" if confidence == "CONFIRMED" else "pending",
        "confirmation_ref": "synthetic:explicit" if confidence == "CONFIRMED" else None,
        "evidence_ids": [1],
    }


def fixture(n=8):
    rules = []
    for j, (d, ps) in enumerate(zip(DIMS, POLES, strict=True)):
        for t, b in [
            ("dimension_def", {"poles": list(ps)}),
            ("dimension_items", {"items": list(range(j + 1, n + 1, 4))}),
            ("default_option_map", {"options": {"A": ps[0], "B": ps[1]}}),
            ("threshold", {"method": "higher_wins"}),
            ("tie_break", {"pole": ps[1]}),
        ]:
            rules.append(entry(t, {"dimension": d, **b}))
    exam = ExamVersion(11, 1, {n: ("A", "B") for n in range(1, n + 1)}, DIMS)
    profile = ScoringProfile(22, 1, 11, {"rules": rules, "requirements": []})
    return exam, profile


def setrule(profile, t, dim="E-I", **body):
    b = {"dimension": dim, **body}
    profile.snapshot["rules"][:] = [
        e for e in profile.snapshot["rules"] if e["entry_key"] != rule_key(t, b)
    ]
    profile.snapshot["rules"].append(entry(t, b))


def remove(profile, t, dim="E-I"):
    profile.snapshot["rules"][:] = [
        e
        for e in profile.snapshot["rules"]
        if not (e["rule_type"] == t and e["content"]["body"]["dimension"] == dim)
    ]


def run(exam, profile, answers=None):
    return score_assessment(
        exam, profile, answers if answers is not None else {n: "A" for n in exam.items}
    )


def test_hand_calculated_end_to_end_entp_with_reverse():
    exam, p = fixture()
    setrule(p, "reverse", item=5)
    r = run(exam, p, {1: "A", 5: "B", 2: "B", 6: "B", 3: "A", 7: "A", 4: "B", 8: "B"})
    assert r.full_profile == "ENTP" and r.status == "VERIFIED"
    assert [d.final for d in r.dimensions] == [
        {"E": 2, "I": 0},
        {"S": 0, "N": 2},
        {"T": 2, "F": 0},
        {"J": 0, "P": 2},
    ]
    d = r.dimensions[0]
    assert d.contributions[1]["points"] == {"E": 1, "I": 0}
    assert d.contributions[1]["transformations"] == ["reverse: kutuplar yer değiştirdi"]
    assert d.decision["profile_version_id"] == 22
    assert r.to_dict()["dimensions"][0]["raw"]["E"] == "2"
    json.dumps(r.to_dict(), allow_nan=False)


def test_four_supplied_numbers_are_only_letter_mapping_inputs():
    axes = [("E", "I", "I"), ("S", "N", "N"), ("T", "F", "F"), ("J", "P", "P")]
    assert (
        select_signed_profile(["1.85", "-1.32", "2.45", "-3.10"], axes=axes) == "ENTP"
    )
    assert (
        select_signed_profile(
            [Decimal("1.85"), Decimal("-1.32"), Decimal("2.45"), Decimal("-3.10")],
            axes=axes,
        )
        == "ENTP"
    )
    assert select_signed_profile([0, 0, 0, 0], axes=axes) == "INFP"


@pytest.mark.parametrize(
    "values",
    [
        [1, 2, 3],
        [1, 2, 3, float("nan")],
        [True, 1, 2, 3],
        ['__import__("os")', 1, 2, 3],
    ],
)
def test_invalid_signed_scores(values):
    with pytest.raises(ScoringInputError):
        select_signed_profile(values, axes=[("E", "I", "I")] * 4)


def test_decimal_weights_mean_dimension_weight_and_scaled_normalization():
    exam, p = fixture()
    setrule(p, "option_weight", item=1, weights={"A": {"E": 0.1}, "B": {"I": 0.2}})
    setrule(p, "option_weight", item=5, weights={"A": {"E": 0.2}, "B": {"I": 0.1}})
    setrule(p, "aggregation", method="mean")
    setrule(p, "dimension_weight", value=2)
    setrule(
        p, "normalization", method="scaled", parameters={"factor": 10, "offset": -1}
    )
    r = run(exam, p).dimensions[0]
    # (0.1+0.2)/2*2*10-1 = 2; I = 0/2*2*10-1 = -1.
    assert r.raw == {"E": Decimal("0.3"), "I": 0}
    assert r.final == {"E": 2, "I": -1}
    assert r.selected_pole == "E"
    assert [t["type"] for t in r.transformations] == [
        "aggregation",
        "dimension_weight",
        "normalization",
        "display_rounding",
    ]


def test_percentage_requires_explicit_denominator():
    exam, p = fixture()
    setrule(p, "normalization", method="percentage", parameters={"denominator": 4})
    assert run(exam, p).dimensions[0].final == {"E": 50, "I": 0}
    setrule(p, "normalization", method="percentage", parameters={})
    r = run(exam, p)
    assert r.dimensions[0].raw is None and r.full_profile is None
    assert "parametre" in " ".join(r.dimensions[0].limitations)


@pytest.mark.parametrize(
    "method,params",
    [
        ("percentage", {"denominator": 0}),
        ("percentage", {"denominator": -1}),
        ("scaled", {"factor": 1}),
        ("scaled", {"factor": 1, "offset": 0, "code": "x"}),
        ("other", {"formula": '__import__("os").system("bad")'}),
        ("scaled", {"factor": float("inf"), "offset": 0}),
    ],
)
def test_bad_normalization_is_not_executed(method, params):
    exam, p = fixture()
    setrule(p, "normalization", method=method, parameters=params)
    d = run(exam, p).dimensions[0]
    assert d.status == "NOT_EVALUABLE" and d.raw is None and d.limitations


@pytest.mark.parametrize(
    "policy,letter",
    [("select_pole", "I"), ("use_threshold", "E"), ("invalidate", None)],
)
def test_exact_zero_policy(policy, letter):
    exam, p = fixture()
    setrule(p, "dimension_weight", value=0)
    setrule(p, "threshold", method="min_points", pole="E", value=0)
    setrule(
        p,
        "zero_score",
        policy=policy,
        **({"pole": "I"} if policy == "select_pole" else {}),
    )
    d = run(exam, p).dimensions[0]
    assert d.selected_pole == letter
    assert (d.final is None) == (letter is None)


@pytest.mark.parametrize("value,selected", [(0.3, "E"), (0.300000000001, "I")])
def test_exact_threshold_decimal_inclusive(value, selected):
    exam, p = fixture()
    for n, v in [(1, 0.1), (5, 0.2)]:
        setrule(p, "option_weight", item=n, weights={"A": {"E": v}, "B": {"I": 1}})
    setrule(p, "threshold", method="min_points", pole="E", value=value)
    assert run(exam, p).dimensions[0].selected_pole == selected


@pytest.mark.parametrize(
    "rounding,display", [("half_even", "2.44"), ("half_up", "2.45")]
)
def test_rounding_boundary_and_decision_before_display(rounding, display):
    exam, p = fixture()
    for n in (1, 5):
        setrule(p, "option_weight", item=n, weights={"A": {"E": 1.2225}, "B": {"I": 1}})
    setrule(p, "threshold", method="min_points", pole="E", value=2.446)
    setrule(p, "precision", places=2, rounding=rounding)
    d = run(exam, p).dimensions[0]
    assert d.final["E"] == Decimal("2.445") and d.displayed["E"] == display
    assert (
        d.selected_pole == "I"
    )  # Both printed rounding modes must leave the decision unchanged.


def test_equal_scores_need_explicit_tie_break():
    exam, p = fixture()
    answers = {n: ("B" if n > 4 else "A") for n in exam.items}
    assert run(exam, p, answers).dimensions[0].selected_pole == "I"
    remove(p, "tie_break")
    d = run(exam, p, answers).dimensions[0]
    assert d.final is None and "eşit" in " ".join(d.limitations)


@pytest.mark.parametrize(
    "policy,expected",
    [
        ("skip", {"E": 1, "I": 0}),
        ("count_as_pole", {"E": 1, "I": 1}),
        ("invalidate", None),
    ],
)
def test_missing_policies(policy, expected):
    exam, p = fixture()
    setrule(
        p,
        "missing_answer_policy",
        policy=policy,
        **({"pole": "I"} if policy == "count_as_pole" else {}),
    )
    answers = {n: "A" for n in exam.items if n != 5}
    d = run(exam, p, answers).dimensions[0]
    assert d.final == expected


def test_global_missing_policy_and_specific_override():
    exam, p = fixture()
    setrule(p, "missing_answer_policy", dim="*", policy="invalidate")
    setrule(p, "missing_answer_policy", policy="skip")
    d = run(exam, p, {n: "A" for n in exam.items if n != 5}).dimensions[0]
    assert d.final == {"E": 1, "I": 0}


def test_mean_skip_uses_answered_count_and_empty_mean_is_blocked():
    exam, p = fixture()
    setrule(p, "aggregation", method="mean")
    setrule(p, "missing_answer_policy", policy="skip")
    assert run(exam, p, {n: "A" for n in exam.items if n != 5}).dimensions[0].final == {
        "E": 1,
        "I": 0,
    }
    assert (
        run(exam, p, {n: "A" for n in exam.items if n not in (1, 5)}).dimensions[0].raw
        is None
    )


@pytest.mark.parametrize(
    "responses,needle",
    [
        ([(1, "A"), (1, "B")], "yinelenen"),
        ({999: "A"}, "madde"),
        ({1: "C"}, "seçenek"),
        ({1: 3}, "seçenek"),
        ({True: "A"}, "madde"),
        ({"1": "A"}, "madde"),
        ([(1, "A", "B")], "çift"),
        ("A", "Cevaplar"),
    ],
)
def test_invalid_answers_raise_turkish_error(responses, needle):
    exam, p = fixture()
    with pytest.raises(ScoringInputError, match=needle):
        run(exam, p, responses)


@pytest.mark.parametrize("state", ["UNKNOWN", "CONTRADICTORY", "REJECTED", "DEFERRED"])
def test_critical_rule_state_blocks_only_affected_dimension(state):
    exam, p = fixture()
    p.snapshot["rules"][2]["confidence"] = state
    r = run(exam, p)
    assert r.dimensions[0].raw is None and r.full_profile is None
    assert all(d.status == "VERIFIED" for d in r.dimensions[1:])


@pytest.mark.parametrize("state", ["STRONGLY_INFERRED", "WEAKLY_INFERRED"])
def test_inference_stays_provisional(state):
    exam, p = fixture()
    p.snapshot["rules"][2] = entry(
        "default_option_map",
        {"dimension": "E-I", "options": {"A": "E", "B": "I"}},
        state,
    )
    r = run(exam, p)
    assert r.status == "PROVISIONAL" and r.dimensions[0].status == "PROVISIONAL"
    assert r.full_profile == "ESTJ"


def test_critical_requirement_and_review_are_frozen_and_scoped():
    exam, p = fixture()
    p.snapshot["requirements"] = [
        {
            "req_key": "reverse",
            "critical": True,
            "status": "UNKNOWN",
            "dimensions": ["S-N"],
        }
    ]
    r = run(exam, p)
    assert r.dimensions[1].raw is None and r.dimensions[0].selected_pole == "E"
    p.snapshot["requirements"] = []
    p.snapshot["dimension_reviews"] = [
        {"dimension_code": "T-F", "detail": {"possible_rules": []}}
    ]
    assert run(exam, p).dimensions[2].raw is None


@pytest.mark.parametrize(
    "kind", ["dimension_def", "dimension_items", "threshold", "default_option_map"]
)
def test_missing_essential_rule(kind):
    exam, p = fixture()
    remove(p, kind)
    assert run(exam, p).dimensions[0].raw is None


def test_rule_conflict_rejection_wrong_source_and_bad_proof_block():
    for field, value in [
        ("validation", "rejected"),
        ("source", "ai_inference"),
        ("confirmation_ref", None),
    ]:
        exam, p = fixture()
        p.snapshot["rules"][0][field] = value
        assert run(exam, p).dimensions[0].raw is None
    exam, p = fixture()
    p.snapshot["rules"][0]["content"]["conflict"] = True
    assert run(exam, p).dimensions[0].raw is None


def test_duplicate_rule_and_unknown_item_pole_or_option():
    exam, p = fixture()
    p.snapshot["rules"].append(copy.deepcopy(p.snapshot["rules"][0]))
    assert run(exam, p).dimensions[0].raw is None
    for t, body in [
        ("reverse", {"item": 99}),
        ("tie_break", {"pole": "Z"}),
        ("item_pole_map", {"item": 2, "options": {"A": "E", "B": "I"}}),
        ("default_option_map", {"options": {"A": "E", "B": "I", "C": "E"}}),
    ]:
        exam, p = fixture()
        setrule(p, t, **body)
        assert run(exam, p).dimensions[0].raw is None


def test_explicit_map_weight_precedence_matches_m5_reverse_semantics():
    exam, p = fixture()
    setrule(p, "reverse", item=1)
    setrule(p, "item_pole_map", item=1, options={"A": "E", "B": "I"})
    setrule(p, "option_weight", item=1, weights={"A": {"I": 3}, "B": {"E": 2}})
    d = run(exam, p).dimensions[0]
    assert d.raw == {"E": 1, "I": 3}
    assert "uygulanmadı" in d.contributions[0]["transformations"][0]
    refs = [
        RuleRef(e["entry_key"], e["rule_type"], e["content"]["body"])
        for e in p.snapshot["rules"]
    ]
    assert score(build_model("E-I", refs), {n: "A" for n in exam.items}).points == d.raw


def test_m5_unit_semantics_all_four_dimensions():
    exam, p = fixture(40)
    answers = {n: ("A" if n % 3 else "B") for n in exam.items}
    refs = [
        RuleRef(e["entry_key"], e["rule_type"], e["content"]["body"])
        for e in p.snapshot["rules"]
    ]
    for d in run(exam, p, answers).dimensions:
        assert d.raw == score(build_model(d.dimension, refs), answers).points


def test_1000_runs_independent_of_input_order_and_external_decimal_context():
    exam, p = fixture()
    baseline = run(exam, p).to_dict()
    original = copy.deepcopy(p.snapshot)
    with localcontext() as ctx:
        ctx.prec = 3
        for _ in range(1000):
            assert (
                run(exam, p, dict(reversed([(n, "A") for n in exam.items]))).to_dict()
                == baseline
            )
    assert p.snapshot == original
    assert getcontext().prec != 3


def test_no_network_or_ai_required(monkeypatch):
    def fail(*a, **kw):
        raise AssertionError("Ağ çağrısı")

    monkeypatch.setattr(socket, "socket", fail)
    exam, p = fixture()
    assert run(exam, p).full_profile == "ESTJ"


def test_inventory_profile_version_mismatch():
    exam, p = fixture()
    with pytest.raises(ScoringInputError, match="uyuşmuyor"):
        run(exam, ScoringProfile(p.id, 1, 99, p.snapshot))
    with pytest.raises(ScoringInputError, match="uyuşmuyor"):
        run(exam, ScoringProfile(p.id, 9, 11, p.snapshot))


def test_non_four_dimensions_never_produce_four_letter_profile():
    exam, p = fixture()
    p.snapshot["rules"][:] = [
        e for e in p.snapshot["rules"] if e["content"]["body"]["dimension"] in DIMS[:3]
    ]
    exam = ExamVersion(exam.id, exam.inventory_id, exam.items, DIMS[:3])
    assert run(exam, p).full_profile is None


def test_400_synthetic_items_independent_oracle_and_measurement():
    from synthetic_rules import true_outcome

    exam, p = fixture(400)
    reverse = set(range(7, 401, 7))
    for n in sorted(reverse):
        setrule(p, "reverse", dim=DIMS[(n - 1) % 4], item=n)
    answers = {n: ("B" if n % 5 in (0, 1) else "A") for n in exam.items}
    expected = true_outcome(answers, reverse)
    start = time.perf_counter()
    r = run(exam, p, answers)
    elapsed = time.perf_counter() - start
    for d in r.dimensions:
        assert d.raw == expected[d.dimension]["scores"]
        assert d.selected_pole == expected[d.dimension]["letter"]
    assert sum(len(d.contributions) for d in r.dimensions) == 400
    assert elapsed < 5
    print(f"\nM7 400 madde: {elapsed:.6f} saniye; profil={r.full_profile}")


def test_extended_catalog_new_types_and_zero_shape():
    for t, b in [
        ("aggregation", {"method": "mean"}),
        ("dimension_weight", {"value": 2}),
        ("precision", {"places": 2, "rounding": "half_up"}),
        ("zero_score", {"policy": "invalidate"}),
    ]:
        assert validate_rule(t, {"dimension": "X", **b}) == []
    assert validate_rule("zero_score", {"dimension": "X", "policy": "select_pole"})
    assert validate_rule(
        "zero_score", {"dimension": "X", "policy": "invalidate", "pole": "X"}
    )


def test_explicit_no_normalization_and_output_has_no_shared_snapshot_references():
    exam, p = fixture()
    setrule(p, "normalization", method="none", parameters={})
    r = run(exam, p)
    r.dimensions[0].rules[0]["content"]["body"]["poles"][0] = "Z"
    r.dimensions[0].decision["threshold"]["method"] = "bad"
    assert run(exam, p).full_profile == "ESTJ"


def test_unknown_legacy_critical_requirement_is_not_lost_by_new_matrix():
    exam, p = fixture()
    p.snapshot["requirements"] = [
        {
            "req_key": "other",
            "critical": False,
            "status": "CONFIRMED",
            "dimensions": ["E-I"],
        }
    ]
    p.snapshot["unconfirmed_critical"] = ["legacy:unknown"]
    assert all(d.raw is None for d in run(exam, p).dimensions)


def test_nonexecuting_m6_weight_fact_never_becomes_an_implicit_formula():
    exam, p = fixture()
    p.snapshot["learning_facts"] = [
        {
            "entry_key": "fact:dimension_weight:E-I",
            "content": {"text": "E-I ağırlığı ikidir"},
            "confidence": "CONFIRMED",
        }
    ]
    assert run(exam, p).dimensions[0].raw is None
    setrule(p, "dimension_weight", value=2)
    assert run(exam, p).dimensions[0].final == {"E": 4, "I": 0}


def test_unassigned_inventory_item_blocks_definitive_result():
    exam, p = fixture()
    e = ExamVersion(
        exam.id, exam.inventory_id, {**exam.items, 9: ("A", "B")}, exam.dimension_order
    )
    assert all(d.raw is None for d in run(e, p).dimensions)


def test_missing_answer_without_policy_has_no_partial_numeric_result():
    exam, p = fixture()
    d = run(exam, p, {n: "A" for n in exam.items if n != 5}).dimensions[0]
    assert d.raw is None and d.final is None and d.limitations


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {"rules": None},
        {"rules": [None]},
        {"rules": [{"content": {"body": None}}]},
        {"rules": [], "requirements": "bad"},
        {"rules": [], "requirements": [None]},
        {
            "rules": [],
            "requirements": [
                {"status": "UNKNOWN", "critical": True, "dimensions": ["Y"]}
            ],
        },
        {"rules": [], "dimension_reviews": [{}]},
        {"rules": [], "learning_facts": [None]},
        {"rules": [], "unconfirmed_critical": [None]},
    ],
)
def test_malformed_profile_envelope_is_rejected(snapshot):
    exam, p = fixture()
    with pytest.raises(ScoringInputError):
        run(exam, ScoringProfile(p.id, 1, 11, snapshot))


def test_nan_negative_precision_and_malicious_catalog_type_are_blocked():
    for t, b in [
        ("precision", {"places": -1, "rounding": "half_even"}),
        ("dimension_weight", {"value": float("nan")}),
        ("execute", {"command": '__import__("os")'}),
    ]:
        exam, p = fixture()
        setrule(p, t, **b)
        assert run(exam, p).dimensions[0].raw is None


def test_legacy_provenance_never_promotes_to_verified():
    exam, p = fixture()
    for e in p.snapshot["rules"]:
        e.pop("validation")
        e.pop("confirmation_ref")
    r = run(exam, p)
    assert r.status == "PROVISIONAL" and r.full_profile == "ESTJ"


def test_entp_sign_mapping_rejects_unstated_zero_behavior():
    with pytest.raises(ScoringInputError):
        select_signed_profile([1, -1, 1, -1], axes=[("E", "I", "?")] * 4)


def test_provisional_profile_flag_is_never_lost():
    exam, p = fixture()
    p = ScoringProfile(p.id, p.inventory_id, p.inventory_version_id, p.snapshot, True)
    assert run(exam, p).status == "PROVISIONAL"


def test_skipped_item_trace_does_not_claim_weight_or_reverse_was_used():
    exam, p = fixture()
    setrule(p, "reverse", item=5)
    setrule(p, "missing_answer_policy", policy="skip")
    d = run(exam, p, {n: "A" for n in exam.items if n != 5}).dimensions[0]
    row = next(c for c in d.contributions if c["item"] == 5)
    assert row["rules"] == [
        "rule:dimension_items:dim:E-I",
        "rule:missing_answer_policy:dim:E-I",
    ]
    assert row["points"] == {"E": 0, "I": 0}


def test_boolean_version_id_and_unhashable_options_are_input_errors():
    exam, p = fixture()
    with pytest.raises(ScoringInputError):
        run(
            ExamVersion(True, 1, exam.items, DIMS),
            ScoringProfile(1, 1, True, p.snapshot),
        )
    with pytest.raises(ScoringInputError):
        run(ExamVersion(11, 1, {1: [{}]}, DIMS), p)


def test_unknown_source_and_invalid_validation_are_rejected():
    for field, value in [
        ("source", "untrusted"),
        ("validation", "FABRICATED"),
        ("confirmation_ref", 1),
    ]:
        exam, p = fixture()
        p.snapshot["rules"][0][field] = value
        assert run(exam, p).dimensions[0].raw is None


def test_negative_option_contributions_and_signed_rounding():
    exam, p = fixture()
    for n in (1, 5):
        setrule(
            p, "option_weight", item=n, weights={"A": {"E": -1.2225}, "B": {"I": 0}}
        )
    setrule(p, "precision", places=2, rounding="half_up")
    d = run(exam, p).dimensions[0]
    assert d.raw["E"] == Decimal("-2.445") and d.displayed["E"] == "-2.45"
    assert d.selected_pole == "I"
