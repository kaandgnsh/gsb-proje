"""Şema v4 (keşif tabloları) ve kural kataloğu."""
import sqlite3

import pytest

from lai.core.rules import RULE_TYPES, canonical, rule_dimension, rule_key, validate_rule

TABLES = ("reference_documents", "example_sets", "example_results", "discovery_runs",
          "discovery_chunks", "discovery_candidates", "fit_runs", "fit_results")


def test_v4_tables_exist(store):
    names = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(TABLES) <= names


def _run(store, inventory_id, status="running"):
    return store.conn.execute(
        "INSERT INTO discovery_runs(inventory_id, model, prompt_version, think, status) "
        "VALUES (?,?,?,?,?)", (inventory_id, "sahte", "1", 1, status)).lastrowid


def test_run_status_only_leaves_running_once(store, inventory_id):
    rid = _run(store, inventory_id)
    store.conn.execute("UPDATE discovery_runs SET status='completed', finished_at='x' WHERE id=?",
                       (rid,))
    with pytest.raises(sqlite3.DatabaseError, match="değiştirilemez"):
        store.conn.execute("UPDATE discovery_runs SET status='failed' WHERE id=?", (rid,))
    with pytest.raises(sqlite3.DatabaseError, match="silinemez"):
        store.conn.execute("DELETE FROM discovery_runs WHERE id=?", (rid,))


def test_run_identity_fields_cannot_change_while_running(store, inventory_id):
    rid = _run(store, inventory_id)
    with pytest.raises(sqlite3.DatabaseError):
        store.conn.execute("UPDATE discovery_runs SET model='baska' WHERE id=?", (rid,))


def test_chunks_candidates_fit_are_append_only(store, inventory_id):
    rid = _run(store, inventory_id)
    cid = store.conn.execute(
        "INSERT INTO discovery_chunks(run_id, chunk_index, char_start, char_end, prompt_sha256, "
        "status) VALUES (?,?,?,?,?,?)", (rid, 0, 0, 10, "0" * 64, "ok")).lastrowid
    kid = store.conn.execute(
        "INSERT INTO discovery_candidates(run_id, chunk_id, seq, raw, status, reject_code) "
        "VALUES (?,?,?,?,?,?)", (rid, cid, 0, "{}", "rejected", "schema")).lastrowid
    fr = store.conn.execute("INSERT INTO fit_runs(inventory_id, example_count) VALUES (?,0)",
                            (inventory_id,)).lastrowid
    fid = store.conn.execute(
        "INSERT INTO fit_results(fit_run_id, rule_key, outcome, detail) VALUES (?,?,?,?)",
        (fr, "rule:x", "not_testable", "{}")).lastrowid
    for table, i in (("discovery_chunks", cid), ("discovery_candidates", kid),
                     ("fit_runs", fr), ("fit_results", fid)):
        with pytest.raises(sqlite3.DatabaseError):
            store.conn.execute(f"UPDATE {table} SET id=id WHERE id=?", (i,))
        with pytest.raises(sqlite3.DatabaseError):
            store.conn.execute(f"DELETE FROM {table} WHERE id=?", (i,))


def test_run_creation_is_audited(store, inventory_id):
    rid = _run(store, inventory_id)
    row = store.conn.execute("SELECT event FROM audit_log WHERE ref_table='discovery_runs' "
                             "AND ref_id=?", (rid,)).fetchone()
    assert row[0] == "discovery_started"


# ------------------------------------------------------------------ katalog
VALID = {
    "dimension_def": {"dimension": "E-I", "poles": ["E", "I"]},
    "dimension_items": {"dimension": "E-I", "items": [1, 5, 9]},
    "default_option_map": {"dimension": "E-I", "options": {"A": "E", "B": "I"}},
    "item_pole_map": {"item": 12, "dimension": "E-I", "options": {"A": "I", "B": "E"}},
    "option_weight": {"item": 3, "dimension": "S-N", "weights": {"A": {"S": 2}, "B": {"N": 1}}},
    "reverse": {"item": 12, "dimension": "E-I"},
    "threshold": {"dimension": "E-I", "method": "min_points", "pole": "E", "value": 12},
    "tie_break": {"dimension": "E-I", "pole": "I"},
    "missing_answer_policy": {"dimension": "*", "policy": "skip"},
    "normalization": {"dimension": "E-I", "method": "percentage", "parameters": {}},
}


def test_catalog_is_exactly_the_planned_types():
    assert set(RULE_TYPES) == set(VALID)


@pytest.mark.parametrize("rt", list(VALID))
def test_valid_bodies(rt):
    assert validate_rule(rt, VALID[rt]) == []


@pytest.mark.parametrize("rt,body,needle", [
    ("dimension_def", {"dimension": "E-I", "poles": ["E", "E"]}, "farklı"),
    ("dimension_def", {"dimension": "E-I", "poles": ["E", "I", "X"]}, "en çok 2"),
    ("dimension_items", {"dimension": "E-I", "items": []}, "en az 1"),
    ("dimension_items", {"dimension": "E-I", "items": [1, 1]}, "yinelenen"),
    ("item_pole_map", {"item": 0, "dimension": "E-I", "options": {"A": "E"}}, "en az 1"),
    ("item_pole_map", {"item": 1, "dimension": "E-I", "options": {}}, "boş"),
    ("item_pole_map", {"item": "1", "dimension": "E-I", "options": {"A": "E"}}, "integer"),
    ("option_weight", {"item": 1, "dimension": "E-I", "weights": {"A": {"E": "2"}}}, "number"),
    ("reverse", {"item": 1, "dimension": "E-I", "fazla": 1}, "şemada olmayan"),
    ("threshold", {"dimension": "E-I", "method": "min_points"}, "pole"),
    ("threshold", {"dimension": "E-I", "method": "higher_wins", "pole": "E"}, "kullanılmaz"),
    ("threshold", {"dimension": "E-I", "method": "median"}, "izinli"),
    ("missing_answer_policy", {"dimension": "*", "policy": "count_as_pole"}, "pole"),
    ("missing_answer_policy", {"dimension": "*", "policy": "uydur"}, "izinli"),
    ("tie_break", {"dimension": "", "pole": "E"}, "kısa"),
])
def test_invalid_bodies(rt, body, needle):
    errs = validate_rule(rt, body)
    assert errs and any(needle in e for e in errs), errs


def test_unknown_type_is_rejected():
    errs = validate_rule("puan_uydur", {"dimension": "E-I"})
    assert errs and "kataloğ" in errs[0]


def test_keys_are_stable_and_scoped():
    assert rule_key("reverse", VALID["reverse"]) == "rule:reverse:item:12:dim:E-I"
    assert rule_key("threshold", VALID["threshold"]) == "rule:threshold:dim:E-I"
    assert rule_key("missing_answer_policy", VALID["missing_answer_policy"]) == \
        "rule:missing_answer_policy:dim:*"
    assert rule_dimension(VALID["option_weight"]) == "S-N"


def test_canonical_ignores_key_order_and_item_list_order():
    a = {"dimension": "E-I", "items": [9, 1, 5]}
    b = {"items": [1, 5, 9], "dimension": "E-I"}
    assert canonical("dimension_items", a) == canonical("dimension_items", b)
    assert canonical("item_pole_map", {"item": 1, "dimension": "E-I", "options": {"A": "E"}}) != \
        canonical("item_pole_map", {"item": 1, "dimension": "E-I", "options": {"A": "I"}})
