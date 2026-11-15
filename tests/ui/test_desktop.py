import csv
import json
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from synthetic_desktop import document, ready, rules

from lai.assessment.service import job_status
from lai.learning.__main__ import main as cli
from lai.store.store import Store
from lai.ui.backend import Backend

from .conftest import idle, until


def click(w, name):
    button = w.findChild(QPushButton, name)
    assert button is not None and button.isEnabled(), name
    button.click()
    idle(w)


def test_ui_import_answer_missing_rule_select_version_single_and_cli(
    tmp_path, windows, capsys
):
    db = tmp_path / "Türkçe masaüstü.db"
    w = windows(db)
    w.new_name.setText("Sentetik Öğrenme")
    click(w, "create_inventory")
    inv = w.inventory.currentData()
    p = document(tmp_path / "Sorular.json")
    w.doc_path.setText(str(p))
    click(w, "preview_document")
    assert w.preview["questions"] == 8 and w.doc_records.rowCount() == 8
    with _store(db) as s:
        assert (
            s.conn.execute("SELECT count(*) FROM inventory_versions").fetchone()[0] == 0
        )
    click(w, "save_document")
    s = Store.open(db)
    rules(s, inv, missing_policy=True)
    pid = s.profiles.ensure_profile(inv)
    s.profiles.publish_version(pid)
    s.close()
    w.nav.setCurrentRow(1)
    click(w, "learning_refresh")
    index = next(
        n for n, g in enumerate(w.gap_data) if g["category"] == "missing_answer_policy"
    )
    w.gap_table.selectRow(index)
    w.rule_editor.dimension.setText("*")
    w.rule_editor.kind.setCurrentIndex(
        w.rule_editor.kind.findData("missing_answer_policy")
    )
    w.rule_editor.method.setCurrentIndex(w.rule_editor.method.findData("skip"))
    w.answer_text.setPlainText("Cevapsız soru atlanır; sentetik açık bilgi.")
    click(w, "teach")
    assert not any(g["category"] == "missing_answer_policy" for g in w.gap_data)
    s = Store.open(db)
    pv = s.profiles.latest_version(pid)
    assert pv.version >= 2
    s.close()
    w.nav.setCurrentRow(2)
    w.profile.setCurrentIndex(w.profile.findData(pv.id))
    click(w, "load_items")
    for n in range(w.answer_table.rowCount()):
        w.answer_table.cellWidget(n, 2).setCurrentIndex(1)
    w.alias.setText("Sentetik Çağrı")
    click(w, "evaluate")
    gui = w.last_result["result"]
    assert gui["dimensions"][0]["final"] is not None
    assert w.dialogs[-1].trace.topLevelItemCount() == 4
    args = [
        "assess",
        "--db",
        str(db),
        "--inventory-version",
        str(w.exam.currentData()),
        "--profile-version",
        str(pv.id),
        "--participant-code",
        "SYN-CLI",
        "--responses",
        json.dumps([[n, "A"] for n in range(1, 9)]),
        "--json",
    ]
    assert cli(args) == 0
    assert json.loads(capsys.readouterr().out)["result"] == gui


class SlowBackend(Backend):
    def _perform(self, s, op, a, cancel, progress, check):
        if op == "run_job":
            emit = progress

            def progress(data):
                emit(data)
                if "job" in data:
                    time.sleep(0.035)

        return super()._perform(s, op, a, cancel, progress, check)


def test_ui_batch_close_reopen_resume_same_versions_and_no_duplicates(
    tmp_path, windows
):
    db = tmp_path / "resume.db"
    inv, ver, pid, pv = ready(db)
    path = tmp_path / "yanıtlar.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["participant_id", *[f"Q{i}" for i in range(1, 9)]])
        writer.writerows([[f"SYN-{j}", *["A"] * 8] for j in range(20)])
    w = windows(db, SlowBackend(db))
    w.nav.setCurrentRow(3)
    w.batch_path.setText(str(path))
    click(w, "preview_batch")
    assert w.batch_preview["total"] == 20 and w.mapping_tree.topLevelItemCount() == 1
    click(w, "prepare_job")
    job = w.job_id
    w.jobs.selectRow(0)
    w.findChild(QPushButton, "run_job").click()
    until(lambda: "katılımcı kaydedildi" in w.status.text())
    w.close()
    until(lambda: w.task is None and not w.isVisible())
    s = Store.open(db)
    paused = job_status(s, job, include_rows=True)
    assert paused["state"] == "paused" and 0 < paused["counts"]["succeeded"] < 20
    first = next(r["assessment_id"] for r in paused["rows"] if r["assessment_id"])
    old = s.assessments.get(first)
    s.knowledge.apply_user_correction(
        inv,
        entry_key="rule:default_option_map:dim:E-I",
        content={"body": {"dimension": "E-I", "options": {"A": "I", "B": "E"}}},
        summary="Sentetik yeni",
        explanation="Yeni profil",
        user_text="Değiştir",
    )
    newer = s.profiles.publish_version(pid)
    s.close()
    path.unlink()
    reopened = windows(db)
    reopened.nav.setCurrentRow(3)
    reopened.batch_profile.setCurrentIndex(reopened.batch_profile.findData(newer.id))
    reopened.jobs.selectRow(0)
    click(reopened, "run_job")
    s = Store.open(db)
    done = job_status(s, job, include_rows=True)
    assert done["state"] == "completed" and done["counts"]["succeeded"] == 20
    assert done["profile_version_id"] == pv and done["inventory_version_id"] == ver
    assert len({r["assessment_id"] for r in done["rows"]}) == 20
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 20
    assert s.assessments.get(first) == old
    assert all(
        s.assessments.get(r["assessment_id"]).results == old.results
        for r in done["rows"]
    )
    s.close()
    click(reopened, "run_job")
    s = Store.open(db)
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 20
    s.close()


def test_learning_unknown_and_deferred_survive_reopen(tmp_path, windows):
    db = tmp_path / "learning.db"
    ready(db, missing_policy=True)
    w = windows(db)
    w.nav.setCurrentRow(1)
    click(w, "learning_refresh")
    selected = w.gap_data[w.gap_table.currentRow()]["id"]
    click(w, "later")
    assert all(g["id"] != selected for g in w.gap_data)
    w.gap_filter.setCurrentIndex(w.gap_filter.findData("deferred"))
    assert any(g["id"] == selected and g["state"] == "DEFERRED" for g in w.gap_data)
    w.close()
    until(lambda: not w.isVisible())
    again = windows(db)
    again.nav.setCurrentRow(1)
    click(again, "learning_refresh")
    again.gap_filter.setCurrentIndex(again.gap_filter.findData("deferred"))
    assert any(g["id"] == selected and g["state"] == "DEFERRED" for g in again.gap_data)
    click(again, "resume_question")
    again.gap_filter.setCurrentIndex(again.gap_filter.findData("active"))
    index = next(i for i, g in enumerate(again.gap_data) if g["id"] == selected)
    again.gap_table.selectRow(index)
    click(again, "dont_know")
    again.gap_filter.setCurrentIndex(again.gap_filter.findData("deferred"))
    assert any(
        g["id"] == selected and g["payload"].get("dont_know") for g in again.gap_data
    )


def test_history_opens_original_version_and_partial_reason(tmp_path, windows):
    from lai.assessment.service import submit_single

    db = tmp_path / "history.db"
    inv, ver, pid, _pv = ready(db)
    s = Store.open(db)
    s.questions.upsert_requirement(
        inv,
        req_key="critical",
        category="other",
        description="Eksik",
        critical=True,
        status="UNKNOWN",
        reason="Sentetik",
        dimension_code="E-I",
    )
    partial = s.profiles.publish_version(pid)
    saved = submit_single(
        s,
        inventory_version_id=ver,
        profile_version_id=partial.id,
        responses={n: "A" for n in range(1, 9)},
        alias="Sentetik Geçmiş",
    )
    s.close()
    w = windows(db)
    w.nav.setCurrentRow(4)
    w.history.selectRow(0)
    click(w, "open_result")
    result = w.last_result["results"]
    assert result["profile_version_id"] == partial.id
    assert (
        result["dimensions"][0]["raw"] is None
        and result["dimensions"][0]["limitations"]
    )
    summary = w.dialogs[-1].findChild(type(w.history), "result_summary")
    assert "Hesaplanamadı" in summary.item(0, 4).text()
    assert w.last_result["id"] == saved["assessment_id"]
    trace = w.dialogs[-1].trace
    trace.setCurrentItem(trace.topLevelItem(0))
    trace.setFocus()
    QTest.keyClick(trace, Qt.Key_Right)
    assert trace.topLevelItem(0).isExpanded()


def test_worker_keeps_gui_timer_and_keyboard_responsive(tmp_path, windows):
    class Slow(Backend):
        def _perform(self, *args):
            time.sleep(0.2)
            return super()._perform(*args)

    w = windows(tmp_path / "responsive.db", Slow(tmp_path / "responsive.db"))
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    w.start("refresh", {})
    QTest.keyClick(w, Qt.Key_3, Qt.ControlModifier)
    assert w.nav.currentRow() == 2
    idle(w)
    timer.stop()
    assert len(ticks) >= 5
    w.new_name.setFocus()
    QTest.keyClick(w, Qt.Key_Tab)
    assert w.focusWidget() is not None


def test_model_missing_does_not_disable_deterministic_workflows(
    tmp_path, windows, monkeypatch
):
    from lai.ai import status

    monkeypatch.setattr(
        status,
        "model_status",
        lambda **kw: {
            "available": False,
            "message": "Yerel model bulunamadı; hazır profille puanlama kullanılabilir.",
            "models": [],
        },
    )
    db = tmp_path / "no-model.db"
    ready(db)
    w = windows(db)
    w.nav.setCurrentRow(6)
    click(w, "model_status")
    assert "bulunamadı" in w.model_message.text()
    from lai.ai.llm import LLMUnavailable

    class Down:
        def complete(self, **kwargs):
            raise LLMUnavailable("Sentetik model yok")

    w.backend.llm_factory = lambda: Down()
    w.nav.setCurrentRow(1)
    click(w, "learning_refresh")
    w.answer_text.setPlainText("Sentetik açıklama")
    click(w, "interpret")
    assert "Yanıt kaydedildi" in w.learning_model_notice.text()
    w.nav.setCurrentRow(2)
    click(w, "load_items")
    w.alias.setText("Model yokken sentetik")
    click(w, "evaluate")
    assert w.last_result["result"]["dimensions"]


from contextlib import contextmanager


@contextmanager
def _store(path):
    s = Store.open(path)
    try:
        yield s
    finally:
        s.close()


def test_invalid_document_preview_exposes_location_and_prevents_save(tmp_path, windows):
    db = tmp_path / "bad-preview.db"
    w = windows(db)
    w.new_name.setText("Sentetik Hata")
    click(w, "create_inventory")
    from synthetic_docs import make_doc_inventory, write_json

    doc = make_doc_inventory(8)
    doc["items"][1]["number"] = doc["items"][0]["number"]
    p = write_json(doc, tmp_path / "duplicate.json")
    w.doc_path.setText(str(p))
    click(w, "preview_document")
    assert any(i["severity"] == "error" and i["location"] for i in w.preview["issues"])
    assert not w.save_doc.isEnabled()
    s = Store.open(db)
    assert s.conn.execute("SELECT count(*) FROM inventory_versions").fetchone()[0] == 0
    s.close()


def test_real_file_dialog_uses_turkish_labels(tmp_path, windows, app):
    from PySide6.QtWidgets import QFileDialog

    w = windows(tmp_path / "dialog.db")
    p = document(tmp_path / "Türkçe Sorular.json")
    seen = []

    def choose():
        d = app.activeModalWidget()
        assert isinstance(d, QFileDialog)
        seen.append(
            (
                d.windowTitle(),
                d.labelText(QFileDialog.Accept),
                d.labelText(QFileDialog.Reject),
            )
        )
        from PySide6.QtWidgets import QLineEdit

        d.findChild(QLineEdit, "fileNameEdit").setText(str(p))
        d.accept()

    QTimer.singleShot(80, choose)

    def fallback():
        modal = app.activeModalWidget()
        if isinstance(modal, QFileDialog):
            modal.reject()

    QTimer.singleShot(2000, fallback)
    w.findChild(QPushButton, "document_path_choose").click()
    assert w.doc_path.text() == str(p) and seen[0][1:] == ("Seç", "Vazgeç")


def test_synthetic_discovery_and_source_quotes_in_ui(tmp_path, windows):
    from fake_llm import ScriptedLLM, oracle
    from synthetic_docs import write_guide_docx

    db = tmp_path / "discovery.db"
    _inv, *_ = ready(db)
    quote = "1. madde E-I boyutunda ters puanlanır."
    b = Backend(
        db,
        llm_factory=lambda: ScriptedLLM(
            oracle([(quote, "reverse", {"dimension": "E-I", "item": 1})])
        ),
    )
    w = windows(db, b)
    p = write_guide_docx(tmp_path / "guide.docx", [quote])
    w.doc_role.setCurrentIndex(w.doc_role.findData("reference"))
    w.doc_path.setText(str(p))
    click(w, "preview_document")
    click(w, "save_document")
    w.nav.setCurrentRow(1)
    click(w, "discover")
    assert any(
        e["rule_type"] == "reverse" and e["confidence"] == "WEAKLY_INFERRED"
        for e in w.learning_data["rules"]
    )
    assert any(e["quote"] == quote for e in w.learning_data["evidence"])
    assert w.sources.topLevelItemCount() > 0


def test_400_question_form_keeps_explicit_empty_answers(tmp_path, windows):
    from synthetic_desktop import rules as seed_rules

    from lai.ingest.service import import_document

    db = tmp_path / "large.db"
    s = Store.open(db)
    inv = s.inventory.create_inventory("Sentetik 400")
    p = document(tmp_path / "large.json", 400)
    import_document(s, inv, p, documents_dir=tmp_path / "documents")
    seed_rules(s, inv, 400)
    s.profiles.publish_version(s.profiles.ensure_profile(inv))
    s.close()
    w = windows(db)
    w.nav.setCurrentRow(2)
    click(w, "load_items")
    assert w.answer_table.rowCount() == 400
    assert all(
        w.answer_table.cellWidget(n, 2).currentData() is None for n in range(400)
    )


def test_batch_preview_errors_precede_job_persistence(tmp_path, windows):
    db = tmp_path / "errors.db"
    ready(db)
    p = tmp_path / "errors.csv"
    p.write_text(
        "participant_id,"
        + ",".join(f"Q{i}" for i in range(1, 9))
        + "\nP,Z,"
        + ",".join(["A"] * 7)
        + "\n",
        encoding="utf-8",
    )
    w = windows(db)
    w.nav.setCurrentRow(3)
    w.batch_path.setText(str(p))
    click(w, "preview_batch")
    assert w.batch_errors.rowCount() == 1 and w.batch_errors.item(0, 3).text() == "1"
    s = Store.open(db)
    assert s.conn.execute("SELECT count(*) FROM assessment_jobs").fetchone()[0] == 0
    s.close()


def test_table_palette_has_readable_contrast(tmp_path, windows, app):
    from PySide6.QtGui import QPalette

    w = windows(tmp_path / "contrast.db")
    palette = w.doc_records.palette()
    assert (
        palette.color(QPalette.Base).lightness()
        > palette.color(QPalette.Text).lightness() + 100
    )


def test_structural_batch_error_has_source_table_and_no_job(tmp_path, windows):
    db = tmp_path / "bad-columns.db"
    ready(db)
    p = tmp_path / "wrong.csv"
    p.write_text("participant_id,Q1\nSYN,A\n", encoding="utf-8")
    w = windows(db)
    w.nav.setCurrentRow(3)
    w.batch_path.setText(str(p))
    click(w, "preview_batch")
    assert w.batch_preview is None and not w.prepare_button.isEnabled()
    assert w.batch_errors.rowCount() >= 1 and w.batch_errors.item(0, 1).text() == "1"
    assert "iş kaydedilmedi" in w.batch_summary.text()


def test_standalone_key_ui_preview_and_learning_source(tmp_path, windows):
    db = tmp_path / "key-ui.db"
    ready(db)
    p = tmp_path / "key.csv"
    p.write_text("Soru No,Cevap Anahtarı\n1,A\n2,B\n", encoding="utf-8")
    w = windows(db)
    w.doc_role.setCurrentIndex(w.doc_role.findData("answer_key"))
    w.doc_path.setText(str(p))
    click(w, "preview_document")
    assert w.preview["answer_keys"] == 2 and w.doc_records.rowCount() == 2
    assert w.doc_records.item(0, 1).text() == "A"
    click(w, "save_document")
    w.nav.setCurrentRow(1)
    click(w, "learning_refresh")
    assert any(g["category"] == "answer_key" for g in w.gap_data)
    assert w.learning_data["answer_key_sources"][0]["file_name"] == "key.csv"


def test_answer_key_reading_is_confirmed_from_learning_page(tmp_path, windows):
    db = tmp_path / "key-rules.db"
    ready(db)
    p = tmp_path / "key.csv"
    p.write_text("Soru No,Cevap Anahtarı\n1,E\n2,I\n", encoding="utf-8")
    w = windows(db)
    w.doc_role.setCurrentIndex(w.doc_role.findData("answer_key"))
    w.doc_path.setText(str(p))
    click(w, "preview_document")
    click(w, "save_document")
    w.nav.setCurrentRow(1)
    click(w, "learning_refresh")
    readings = {
        g["payload"]["evidence"].get("reading"): n
        for n, g in enumerate(w.gap_data)
        if g["category"] == "answer_key"
    }
    assert set(readings) == {"pole_first", "pole_second"}
    w.gap_table.selectRow(readings["pole_first"])
    assert "2 kural" not in w.gap_detail.toPlainText()  # kısa öneri tam gösterilir
    assert "item_pole_map" in w.gap_detail.toPlainText()
    click(w, "confirm")
    s = Store.open(db)
    try:
        maps = {
            e.item_number: e.content["body"]["options"]
            for e in s.knowledge.current(1, kind="rule")
            if e.rule_type == "item_pole_map" and e.confidence == "CONFIRMED"
        }
    finally:
        s.close()
    assert maps == {1: {"A": "E", "B": "I"}, 2: {"A": "I", "B": "E"}}


def test_history_paging_opens_records_older_than_first_500(tmp_path, windows):
    from lai.assessment.service import submit_single

    db = tmp_path / "pages.db"
    _inv, ver, _pid, pv = ready(db)
    s = Store.open(db)
    saved = submit_single(
        s,
        inventory_version_id=ver,
        profile_version_id=pv,
        responses={n: "A" for n in range(1, 9)},
        participant_code="SYN-HISTORY",
    )
    original = s.assessments.get(saved["assessment_id"])
    with s.transaction():
        for _ in range(500):
            s.assessments.record(
                response_set_id=original.response_set_id,
                profile_version_id=pv,
                status=original.status,
                results=original.results,
                limitations=[],
                engine_version=original.engine_version,
            )
    s.close()
    w = windows(db)
    w.nav.setCurrentRow(4)
    assert w.history.rowCount() == 500
    click(w, "history_next")
    assert w.history.rowCount() == 1
    w.history.selectRow(0)
    click(w, "open_result")
    assert w.last_result["id"] == original.id
