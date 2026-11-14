"""Değişmezlik ve güven kuralları veritabanında zorlanır (uygulama kodu atlansa bile)."""
import json
import sqlite3

import pytest

from lai.store.db import migrate, open_db


@pytest.fixture
def conn(tmp_path):
    c = open_db(tmp_path / "lai.db")
    migrate(c, backup_dir=None)
    c.execute("INSERT INTO inventories(id, name) VALUES (1, 'Sentetik')")
    c.execute("INSERT INTO inventory_versions(id, inventory_id, version) VALUES (1, 1, 1)")
    c.execute("INSERT INTO evidence_refs(id, kind, note) VALUES (1, 'user_statement', 'kullanıcı')")
    return c


def k(conn, **kw):
    row = dict(inventory_id=1, entry_key="rule:reverse:item:12", version=1, supersedes_id=None,
               kind="rule", rule_type="reverse", content=json.dumps({"item": 12}),
               summary="12. madde ters", confidence="WEAKLY_INFERRED", source="ai_inference",
               validation="pending", confirmation_ref=None)
    row.update(kw)
    cols = ",".join(row)
    cur = conn.execute(f"INSERT INTO knowledge_entries({cols}) VALUES ({','.join('?' * len(row))})",
                       list(row.values()))
    return cur.lastrowid


def test_ai_inference_cannot_be_confirmed(conn):
    with pytest.raises(sqlite3.IntegrityError):
        k(conn, confidence="CONFIRMED", source="ai_inference", validation="validated",
          confirmation_ref="x")


def test_confirmed_requires_reference_and_validation(conn):
    with pytest.raises(sqlite3.IntegrityError):
        k(conn, confidence="CONFIRMED", source="user", validation="validated")
    with pytest.raises(sqlite3.IntegrityError):
        k(conn, confidence="CONFIRMED", source="user", validation="pending", confirmation_ref="c")
    assert k(conn, confidence="CONFIRMED", source="user", validation="validated",
             confirmation_ref="correction:1")


@pytest.mark.parametrize("src", ["ai_inference", "example_fit"])
def test_only_user_or_document_can_validate(conn, src):
    with pytest.raises(sqlite3.IntegrityError):
        k(conn, source=src, validation="validated")


def test_knowledge_is_append_only(conn):
    eid = k(conn)
    with pytest.raises(sqlite3.IntegrityError, match="salt eklenir"):
        conn.execute("UPDATE knowledge_entries SET confidence='STRONGLY_INFERRED' WHERE id=?", (eid,))
    with pytest.raises(sqlite3.IntegrityError, match="silinemez"):
        conn.execute("DELETE FROM knowledge_entries WHERE id=?", (eid,))


def test_version_chain_cannot_branch_or_skip(conn):
    v1 = k(conn)
    v2 = k(conn, version=2, supersedes_id=v1)
    with pytest.raises(sqlite3.IntegrityError):  # v1'in üstüne ikinci dal
        k(conn, version=2, supersedes_id=v1, summary="dal")
    with pytest.raises(sqlite3.IntegrityError):  # sürüm atlama
        k(conn, version=4, supersedes_id=v2)
    with pytest.raises(sqlite3.IntegrityError):  # başka anahtarın üstüne
        k(conn, entry_key="baska", version=2, supersedes_id=v2)
    with pytest.raises(sqlite3.IntegrityError):  # supersedes'siz v2
        k(conn, version=2, entry_key="yeni")
    assert k(conn, version=3, supersedes_id=v2)


def test_current_knowledge_view_shows_latest_only(conn):
    v1 = k(conn)
    v2 = k(conn, version=2, supersedes_id=v1, confidence="STRONGLY_INFERRED")
    rows = conn.execute("SELECT id FROM current_knowledge").fetchall()
    assert [r[0] for r in rows] == [v2]
    assert [r[0] for r in conn.execute("SELECT id FROM inferred_rules")] == [v2]


def _profile_version(conn, provisional):
    conn.execute("INSERT OR IGNORE INTO scoring_profiles(id, inventory_id, name) VALUES (1,1,'p')")
    v = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM scoring_profile_versions").fetchone()[0]
    return conn.execute("INSERT INTO scoring_profile_versions(profile_id, version, snapshot, "
                        "content_hash, provisional) VALUES (1, ?, '[]', ?, ?)",
                        (v, "a" * 64, provisional)).lastrowid


def _response_set(conn):
    conn.execute("INSERT OR IGNORE INTO participants(id, code) VALUES (1, 'ANON-1')")
    rs = conn.execute("INSERT INTO response_sets(participant_id, inventory_version_id) VALUES (1,1)").lastrowid
    conn.execute("INSERT INTO responses VALUES (?, 1, 'A')", (rs,))
    return rs


def _assessment(conn, pv, rs, status):
    return conn.execute("INSERT INTO assessments(response_set_id, profile_version_id, status, results,"
                        " engine_version, input_hash) VALUES (?,?,?,'{}','t',?)",
                        (rs, pv, status, "b" * 64)).lastrowid


def test_verified_assessment_needs_final_profile(conn):
    rs = _response_set(conn)
    with pytest.raises(sqlite3.IntegrityError, match="provisional"):
        _assessment(conn, _profile_version(conn, 1), rs, "VERIFIED")
    assert _assessment(conn, _profile_version(conn, 0), rs, "VERIFIED")


IMMUTABLE = {
    "scoring_profile_versions": "note='x'", "assessments": "status='VERIFIED'",
    "responses": "option_code='B'", "response_sets": "source_label='x'",
    "learning_history": "event='x'", "evidence_refs": "note='x'",
}


@pytest.mark.parametrize("table,assign", IMMUTABLE.items())
def test_tables_are_immutable(conn, table, assign):
    rs = _response_set(conn)
    _assessment(conn, _profile_version(conn, 1), rs, "PROVISIONAL")
    k(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE {table} SET {assign}")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"DELETE FROM {table}")


def test_question_content_frozen_status_changes_via_response(conn):
    qid = conn.execute("INSERT INTO clarification_questions(inventory_id, dedupe_key, group_key, "
                       "missing_info, why_it_matters, impact, question_text) VALUES "
                       "(1,'d','g','m','w','i','q?')").lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE clarification_questions SET question_text='değişti' WHERE id=?", (qid,))
    conn.execute("INSERT INTO question_responses(question_id, kind) VALUES (?, 'later')", (qid,))
    assert conn.execute("SELECT status FROM clarification_questions WHERE id=?", (qid,)).fetchone()[0] == "later"
    with pytest.raises(sqlite3.IntegrityError):  # metin yanıtı boş olamaz
        conn.execute("INSERT INTO question_responses(question_id, kind, value) VALUES (?, 'text', '  ')", (qid,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM clarification_questions")


def test_history_written_automatically(conn):
    v1 = k(conn)
    k(conn, version=2, supersedes_id=v1)
    events = [r[0] for r in conn.execute("SELECT event FROM learning_history ORDER BY id")]
    assert events == ["knowledge_created", "knowledge_revised"]
