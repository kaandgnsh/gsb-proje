"""Örnek sonuçlara deterministik uyum: puan sayımı, karar kuralları, durum ataması."""
import json

import pytest
from synthetic_rules import DIMS, dim_of, make_examples, truth_rules

from lai.core.rules import rule_key
from lai.core.tally import RuleRef, build_model, score
from lai.ingest.model import IngestError
from lai.learning.examples import import_examples, parse_examples
from lai.learning.fit import evaluate, run_fit

N = 24
REV = {12, 7}


def refs(rules):
    return [RuleRef(rule_key(t, b), t, b) for t, b in rules]


def ex(**kw):
    return parse_examples(make_examples(N, 30, REV, **kw))


def outcome_of(res, rt, **match):
    keys = [k for k in res if k.startswith(f"rule:{rt}:")]
    if match.get("item"):
        keys = [k for k in keys if f":item:{match['item']}:" in k]
    if match.get("dim"):
        keys = [k for k in keys if k.endswith(f"dim:{match['dim']}")]
    assert len(keys) == 1, keys
    return res[keys[0]].outcome


# ------------------------------------------------------------------ puan sayımı
def test_model_counts_default_map_reverse_and_weights():
    rules = truth_rules(N, REV | {9}) + [("option_weight", {"item": 1, "dimension": "E-I",
                                                      "weights": {"A": {"E": 2}, "B": {"I": 3}}})]
    m = build_model("E-I", refs(rules))
    assert m.missing == []
    assert m.items[9]["A"] == {"I": 1}  # ters (9. madde E-I'de)
    assert m.items[5]["A"] == {"E": 1}
    assert m.items[1] == {"A": {"E": 2}, "B": {"I": 3}}  # ağırlık varsayılan eşlemeden önce gelir
    t = score(m, {i: "A" for i in range(1, N + 1)})
    assert t.status == "ok" and t.points == {"E": 2 + 4, "I": 1}


def test_model_missing_mapping_is_reported_not_guessed():
    rules = [("dimension_items", {"dimension": "E-I", "items": [1, 5]}),
             ("item_pole_map", {"item": 1, "dimension": "E-I", "options": {"A": "E", "B": "I"}})]
    m = build_model("E-I", refs(rules))
    assert any("5" in s for s in m.missing)


def test_missing_answer_without_policy_is_not_evaluable():
    m = build_model("E-I", refs(truth_rules(N, REV)))
    t = score(m, {i: "A" for i in range(2, N + 1)})
    assert t.status == "not_evaluable" and "1" in t.note


def test_unknown_option_is_not_evaluable():
    m = build_model("E-I", refs(truth_rules(N, REV)))
    t = score(m, {**{i: "A" for i in range(1, N + 1)}, 5: "C"})
    assert t.status == "not_evaluable" and "C" in t.note


# ------------------------------------------------------------------ uyum
def test_true_rules_are_consistent_with_examples():
    res = evaluate(refs(truth_rules(N, REV)), ex(force_tie=True))
    assert {r.outcome for r in res.values()} == {"consistent"}
    assert outcome_of(res, "tie_break", dim="E-I") == "consistent"


def test_missing_reverse_makes_only_that_dimension_contradictory():
    rules = [r for r in truth_rules(N, REV) if not (r[0] == "reverse" and r[1]["item"] == 12)]
    res = evaluate(refs(rules), ex())
    d12 = dim_of(12)
    assert outcome_of(res, "default_option_map", dim=d12) == "inconsistent"
    assert outcome_of(res, "dimension_items", dim=d12) == "inconsistent"
    # karar kuralları bildirilen puanlarla ayrı sınanır → tutarlı kalır
    assert outcome_of(res, "threshold", dim=d12) == "consistent"
    for d in DIMS:
        if d != d12:
            assert outcome_of(res, "default_option_map", dim=d) == "consistent"
    detail = res[rule_key("default_option_map", {"dimension": d12})].detail
    assert detail["tests"][0]["mismatched"] > 0 and detail["tests"][0]["mismatches"]


def test_wrong_tie_break_is_contradictory_threshold_not():
    rules = [r if r[0] != "tie_break" or r[1]["dimension"] != "E-I"
             else ("tie_break", {"dimension": "E-I", "pole": "E"}) for r in truth_rules(N, REV)]
    res = evaluate(refs(rules), ex(force_tie=True))
    assert outcome_of(res, "tie_break", dim="E-I") == "inconsistent"
    assert outcome_of(res, "threshold", dim="E-I") == "consistent"


def test_letters_only_examples_test_scoring_through_decision():
    res = evaluate(refs(truth_rules(N, REV)), ex(with_scores=False))
    assert outcome_of(res, "default_option_map", dim="S-N") == "consistent"
    assert outcome_of(res, "threshold", dim="S-N") == "consistent"


def test_min_points_threshold():
    # E-I'de 6 madde; "E için en az 4 puan" kuralı gerçekle (yüksek kazanır) çelişir
    rules = [r if r[0] != "threshold" or r[1]["dimension"] != "E-I"
             else ("threshold", {"dimension": "E-I", "method": "min_points", "pole": "E",
                                 "value": 5}) for r in truth_rules(N, REV)]
    res = evaluate(refs(rules), ex())
    assert outcome_of(res, "threshold", dim="E-I") == "inconsistent"


def test_wrong_poles_in_dimension_def():
    rules = [r if r[0] != "dimension_def" or r[1]["dimension"] != "T-F"
             else ("dimension_def", {"dimension": "T-F", "poles": ["T", "X"]})
             for r in truth_rules(N, REV)]
    res = evaluate(refs(rules), ex())
    assert outcome_of(res, "dimension_def", dim="T-F") == "inconsistent"


def test_missing_answers_with_skip_policy():
    data = make_examples(N, 10, REV)
    for e in data["examples"]:
        e["answers"]["3"] = None
    # 3. madde cevapsız: politika yoksa S-N sınanamaz
    res = evaluate(refs(truth_rules(N, REV)), parse_examples(data))
    assert outcome_of(res, "default_option_map", dim=dim_of(3)) == "not_testable"
    # "atla" politikası + puanlar madde 3 olmadan yeniden hesaplanmış örnekler → tutarlı
    for e in data["examples"]:
        ans = {int(k): v for k, v in e["answers"].items() if v is not None}
        from synthetic_rules import true_outcome
        e["result"] = true_outcome(ans, REV)
    rules = truth_rules(N, REV) + [("missing_answer_policy", {"dimension": "*", "policy": "skip"})]
    res = evaluate(refs(rules), parse_examples(data))
    assert outcome_of(res, "default_option_map", dim=dim_of(3)) == "consistent"
    assert outcome_of(res, "missing_answer_policy", dim="*") == "consistent"


def test_no_examples_means_not_testable():
    res = evaluate(refs(truth_rules(N, REV)), [])
    assert {r.outcome for r in res.values()} == {"not_testable"}


def test_normalization_is_not_testable_in_m5():
    rules = truth_rules(N, REV) + [("normalization", {"dimension": "E-I", "method": "percentage",
                                                      "parameters": {}})]
    res = evaluate(refs(rules), ex())
    assert outcome_of(res, "normalization", dim="E-I") == "not_testable"


# ------------------------------------------------------------------ örnek içe aktarma
@pytest.mark.parametrize("mut,needle", [
    (lambda d: d.update(format="x"), "$.format"),
    (lambda d: d["examples"][0].update(answers={"bir": "A"}), "$.examples[0].answers"),
    (lambda d: d["examples"][0].update(result={}), "$.examples[0].result"),
    (lambda d: d["examples"][0]["result"].update({"E-I": {}}), "E-I"),
    (lambda d: d["examples"][1].update(id="Ö-1"), "yinelenen"),
    (lambda d: d["examples"][0]["result"]["E-I"]["scores"].update(E="çok"), "scores"),
])
def test_examples_schema_errors_have_paths(mut, needle):
    d = make_examples(N, 3, REV)
    mut(d)
    with pytest.raises(IngestError) as e:
        parse_examples(d)
    assert e.value.code == "invalid_examples" and needle in e.value.message


def _write(tmp_path, data, name="örnekler.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_import_examples_once(store, inventory_id, docs_dir, tmp_path):
    p = _write(tmp_path, make_examples(N, 5, REV))
    r1 = import_examples(store, inventory_id, p, documents_dir=docs_dir)
    assert r1.count == 5 and not r1.duplicate
    r2 = import_examples(store, inventory_id, p, documents_dir=docs_dir)
    assert r2.duplicate and r2.set_id == r1.set_id
    assert store.conn.execute("SELECT COUNT(*) FROM example_results").fetchone()[0] == 5
    ev = store.inventory.get_evidence(r1.evidence_id)
    assert ev["kind"] == "example_result" and ev["document_id"] == r1.document_id


def test_import_examples_checks_items_against_inventory(store, inventory_id, docs_dir, tmp_path):
    from synthetic_docs import make_doc_inventory, write_json

    from lai.ingest.service import import_document
    inv = make_doc_inventory(N)
    import_document(store, inventory_id, write_json(inv, tmp_path / "e.json"), documents_dir=docs_dir)
    d = make_examples(N + 1, 2, REV)  # 25. madde envanterde yok
    with pytest.raises(IngestError) as e:
        import_examples(store, inventory_id, _write(tmp_path, d), documents_dir=docs_dir)
    assert "25" in e.value.message


# ------------------------------------------------------------------ bilgi kayıtlarına yazım
def _record_all(store, inventory_id, rules):
    for t, b in rules:
        store.knowledge.record(inventory_id, entry_key=rule_key(t, b), kind="rule", rule_type=t,
                               content={"body": b, "explanation": "test"}, summary=t,
                               confidence="WEAKLY_INFERRED", source="ai_inference",
                               dimension_code=b["dimension"], item_number=b.get("item"))


def test_run_fit_updates_confidence_with_new_versions(store, inventory_id, docs_dir, tmp_path):
    rules = [r for r in truth_rules(N, REV) if not (r[0] == "reverse" and r[1]["item"] == 12)]
    _record_all(store, inventory_id, rules)
    store.knowledge.apply_user_correction(
        inventory_id, entry_key="rule:tie_break:dim:E-I", content={"body": {"dimension": "E-I",
                                                                           "pole": "I"}},
        summary="eşitlikte I", explanation="kullanıcı", user_text="eşitlikte I", rule_type="tie_break")
    r0 = run_fit(store, inventory_id)
    assert r0.example_count == 0 and r0.changed == []  # örnek yok: hepsi zaten WEAKLY
    imp = import_examples(store, inventory_id, _write(tmp_path, make_examples(N, 20, REV, force_tie=True)),
                          documents_dir=docs_dir)
    rep = run_fit(store, inventory_id)
    k = store.knowledge
    sn = k.latest(inventory_id, "rule:default_option_map:dim:S-N")
    assert (sn.confidence, sn.source, sn.version) == ("STRONGLY_INFERRED", "example_fit", 2)
    assert imp.evidence_id in sn.evidence_ids
    bad = k.latest(inventory_id, f"rule:default_option_map:dim:{dim_of(12)}")
    assert bad.confidence == "WEAKLY_INFERRED"  # dimension review, not a faulty rule
    review = store.conn.execute("SELECT detail FROM dimension_reviews WHERE fit_run_id=? AND dimension_code=?",
                                (rep.fit_run_id, dim_of(12))).fetchone()
    assert bad.entry_key in json.loads(review[0])["possible_rules"]
    tb = k.latest(inventory_id, "rule:tie_break:dim:E-I")
    assert tb.confidence == "CONFIRMED" and tb.version == 2  # kullanıcı onaylı kayda dokunulmaz
    assert rep.outcomes["rule:tie_break:dim:E-I"] == "consistent"
    n_fit = store.conn.execute("SELECT COUNT(*) FROM fit_results WHERE fit_run_id=?",
                               (rep.fit_run_id,)).fetchone()[0]
    assert n_fit == len(rules) + 0  # tie_break E-I zaten rules içinde
    versions = store.conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0]
    again = run_fit(store, inventory_id)
    assert again.changed == []
    assert store.conn.execute("SELECT COUNT(*) FROM knowledge_entries").fetchone()[0] == versions
