"""M3 eksik giderme: spec §6 `audit_log`, gereksinim alan geçmişi, düzeltmede alan boşaltma."""
import sqlite3

import pytest

from lai.store.db import LATEST_VERSION


def _events(store, table=None):
    q = "SELECT event, ref_table, ref_id, detail FROM audit_log"
    rows = store.conn.execute(q + " ORDER BY id").fetchall()
    return [r for r in rows if table is None or r["ref_table"] == table]


def test_schema_has_audit_log_and_two_or_more_migrations(store):
    assert LATEST_VERSION >= 2
    names = {r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "audit_log" in names


def test_audit_log_is_append_only(store):
    store.participants.create(code="K-1")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE audit_log SET event='x'")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM audit_log")


def test_data_events_are_audited(store, inv):
    iid, ver = inv
    doc, _ = store.inventory.add_document(iid, file_name="a.pdf", sha256="a" * 64, kind="pdf")
    p = store.participants.create(code="K-1")
    rs = store.participants.add_response_set(p.id, ver, {1: "A"})
    prof = store.profiles.ensure_profile(iid, "varsayılan")
    pv = store.profiles.publish_version(prof, note="ilk")
    a = store.assessments.record(response_set_id=rs, profile_version_id=pv.id, status="PROVISIONAL",
                                 results={}, limitations=[], engine_version="t")
    store.assessments.add_feedback(a.id, "uygun")
    events = [r["event"] for r in _events(store)]
    for e in ("inventory_created", "inventory_version_created", "document_added",
              "participant_created", "response_set_created", "profile_version_published",
              "assessment_recorded", "assessment_feedback"):
        assert e in events, e
    assert doc


def test_participant_change_logs_field_names_not_personal_values(store):
    p = store.participants.create(code="K-2", full_name="Ayşe Yılmaz", contact="ayse@example.org")
    store.participants.update_details(p.id, full_name=None, contact=None)
    rows = [r for r in _events(store, "participants") if r["event"] == "participant_updated"]
    assert len(rows) == 1
    detail = rows[0]["detail"]
    assert "full_name" in detail and "contact" in detail
    assert "Ayşe" not in detail and "example.org" not in detail
    # oluşturma kaydı da kişisel değer taşımaz
    assert all("Ayşe" not in r["detail"] for r in _events(store))


def test_requirement_field_changes_are_in_learning_history(store, inv):
    iid, _ = inv
    kw = dict(req_key="map:E-I", category="answer_mapping", description="eski açıklama",
              critical=False, status="UNKNOWN", reason=None)
    store.questions.upsert_requirement(iid, **kw)
    store.questions.upsert_requirement(iid, **{**kw, "description": "yeni açıklama",
                                               "critical": True})
    hist = [h for h in store.knowledge.learning_history(iid) if h["event"] == "requirement_changed"]
    assert len(hist) == 1
    d = hist[0]["detail"]
    assert d["from"]["description"] == "eski açıklama" and d["to"]["description"] == "yeni açıklama"
    assert d["from"]["critical"] == 0 and d["to"]["critical"] == 1


def test_user_correction_can_clear_item_scope(store, inv):
    iid, _ = inv
    e = store.knowledge.record(iid, entry_key="rule:x", kind="rule", rule_type="reverse",
                               content={"a": 1}, summary="madde kuralı", confidence="UNKNOWN",
                               source="ai_inference", item_number=12)
    _, new = store.knowledge.apply_user_correction(
        iid, entry_key="rule:x", content={"a": 2}, summary="boyut kuralı",
        explanation="kural maddeye değil boyuta ait", user_text="Bu kural E-I boyutunun tamamı için.",
        rule_type="reverse", item_number=None, dimension_code="E-I", target_entry_id=e.id)
    assert new.item_number is None and new.dimension_code == "E-I"
    assert store.knowledge.get(e.id).item_number == 12  # eski sürüm aynen


def test_user_correction_omitted_fields_are_kept(store, inv):
    iid, _ = inv
    e = store.knowledge.record(iid, entry_key="rule:y", kind="rule", rule_type="reverse",
                               content={}, summary="s", confidence="UNKNOWN",
                               source="ai_inference", item_number=7)
    _, new = store.knowledge.apply_user_correction(
        iid, entry_key="rule:y", content={"r": True}, summary="s2", explanation="onay",
        user_text="7 ters", target_entry_id=e.id)
    assert new.item_number == 7 and new.rule_type == "reverse"


def test_existing_v1_database_upgrades_with_backup(tmp_path, monkeypatch):
    from lai.store import db as dbmod
    from lai.store.store import Store
    path = tmp_path / "lai.db"
    with monkeypatch.context() as m:
        m.setattr(dbmod, "MIGRATIONS", dbmod.MIGRATIONS[:1])
        m.setattr(dbmod, "LATEST_VERSION", 1)
        s = Store.open(path)
        s.inventory.create_inventory("Eski", None)
        s.close()
    s = Store.open(path, backup_dir=tmp_path / "yedek")
    try:
        assert dbmod.current_version(s.conn) == LATEST_VERSION
        assert s.conn.execute("SELECT name FROM inventories").fetchone()[0] == "Eski"
        assert len(list((tmp_path / "yedek").glob("*.db"))) == 1
        s.inventory.create_inventory("Yeni", None)
        assert [r["event"] for r in _events(s)] == ["inventory_created"]
    finally:
        s.close()
