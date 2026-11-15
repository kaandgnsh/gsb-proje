import json
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton
from synthetic_desktop import ready

from lai.assessment.service import submit_single
from lai.reports.service import assessment_report
from lai.store.store import Store
from lai.ui.backend import Backend

from .conftest import idle, until


def saved(tmp_path):
    db = tmp_path / "rapor.db"
    _, iv, _, pv = ready(db)
    s = Store.open(db)
    try:
        r = submit_single(
            s,
            inventory_version_id=iv,
            profile_version_id=pv,
            participant_code="Türkçe Çığ",
            responses=[(n, "A") for n in range(1, 9)],
        )
        return db, r["assessment_id"], assessment_report(s, r["assessment_id"])
    finally:
        s.close()


def test_result_and_history_export_shared_snapshot(tmp_path, windows, monkeypatch):
    db, aid, expected = saved(tmp_path)
    w = windows(db)
    w.start("result", {"assessment_id": aid})
    idle(w)
    dialog = w.dialogs[-1]
    path = tmp_path / "sonuç.json"
    monkeypatch.setattr(w, "export_path", lambda *args: str(path))
    dialog.export_format.setCurrentText("JSON")
    dialog.findChild(QPushButton, "export_result").click()
    idle(w)
    assert json.loads(path.read_text()) == expected
    assert w.open_export_button.isEnabled()
    w.nav.setCurrentRow(4)
    w.history.selectRow(0)
    w.history_export_format.setCurrentText("XLSX")
    excel = tmp_path / "geçmiş.xlsx"
    monkeypatch.setattr(w, "export_path", lambda *args: str(excel))
    w.findChild(QPushButton, "export_history").click()
    idle(w)
    assert excel.read_bytes().startswith(b"PK")
    assert w.last_export["assessment_id"] == aid
    w.open_export_button.click()
    idle(w)
    preview = w.dialogs[-1]
    assert preview.objectName() == "report_preview"
    assert preview.tabs.count() == 4
    assert preview.tabs.widget(0).item(1, 1).text() == (expected["metadata"]["alias"] or "")
    path = tmp_path / "sonuç.pdf"
    monkeypatch.setattr(w, "export_path", lambda *args: str(path))
    w.select_export(aid, "pdf")
    idle(w)
    w.open_export_button.click()
    idle(w)
    assert w.dialogs[-1].document.pageCount() >= 2


def test_batch_export_pending_and_completed(tmp_path, windows, monkeypatch):
    from lai.assessment.service import create_job, run_job

    db, _, _ = saved(tmp_path)
    s = Store.open(db)
    try:
        iv = s.conn.execute("SELECT id FROM inventory_versions").fetchone()[0]
        pv = s.conn.execute("SELECT id FROM scoring_profile_versions").fetchone()[0]
        file = tmp_path / "batch.csv"
        file.write_text(
            "participant_code,"
            + ",".join(f"Q{n}" for n in range(1, 9))
            + "\nToplu,"
            + ",".join(["A"] * 8),
            encoding="utf-8",
        )
        jid = create_job(s, file, inventory_version_id=iv, profile_version_id=pv)["id"]
    finally:
        s.close()
    w = windows(db)
    w.nav.setCurrentRow(3)
    w.jobs.selectRow(0)
    path = tmp_path / "bekleyen.csv"
    monkeypatch.setattr(w, "export_path", lambda *args: str(path))
    w.findChild(QPushButton, "export_job").click()
    idle(w)
    assert "pending" in path.read_text(encoding="utf-8-sig")
    s = Store.open(db)
    run_job(s, jid)
    s.close()
    path = tmp_path / "pdfler.zip"
    w.batch_export_format.setCurrentIndex(w.batch_export_format.findData("pdf"))
    w.findChild(QPushButton, "export_job").click()
    idle(w)
    assert w.last_export["pdf_count"] == 1


class SlowExport(Backend):
    def _perform(self, s, op, a, cancel, progress, check):
        if op == "export_assessment":
            for _ in range(30):
                time.sleep(0.01)
                check()
        return super()._perform(s, op, a, cancel, progress, check)


def test_export_background_cancel_and_safe_close(tmp_path, windows, monkeypatch):
    db, aid, _ = saved(tmp_path)
    w = windows(db, SlowExport(db))
    target = tmp_path / "iptal.pdf"
    monkeypatch.setattr(w, "export_path", lambda *args: str(target))
    ticks = []
    timer = QTimer(w)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    w.select_export(aid, "pdf")
    until(lambda: len(ticks) >= 3)
    assert w.task is not None and w.cancel_button.isEnabled()
    w.close()
    until(lambda: w.task is None)
    assert not target.exists()
    assert not w.isVisible()


def test_export_write_failure_keeps_target_visible_error(
    tmp_path, windows, monkeypatch
):
    db, aid, _ = saved(tmp_path)
    w = windows(db)
    target = tmp_path / "old.pdf"
    target.write_text("OLD")
    monkeypatch.setattr(w, "export_path", lambda *args: str(target))

    def fail(*args):
        raise PermissionError("Sentetik yazma izni yok")

    monkeypatch.setattr("lai.reports.service.os.replace", fail)
    w.select_export(aid, "pdf")
    idle(w)
    assert target.read_text() == "OLD"
    assert "yazma izni yok" in w.status.text()
    assert not list(tmp_path.glob(".lai-export-*"))


def test_result_dialog_generates_model_commentary(tmp_path, windows, monkeypatch):
    from lai.ai.llm import LLMReply

    reply = {
        "summary": "Sonuç ESFJ olarak kayıtlıdır.",
        "dimensions": [
            {"dimension": d, "text": f"{d} boyutunda {p} seçildi."}
            for d, p in [("E-I", "E"), ("S-N", "S"), ("T-F", "F"), ("J-P", "J")]
        ],
    }

    class Fake:
        model, think = "yerel-test", True

        def complete(self, *, system, prompt, schema):
            return LLMReply(json.dumps(reply), "", 1, 1)

    db, aid, _ = saved(tmp_path)
    w = windows(db)
    w.backend.llm_factory = Fake
    w.start("result", {"assessment_id": aid})
    idle(w)
    dialog = w.dialogs[-1]
    dialog.findChild(QPushButton, "generate_commentary").click()
    idle(w)
    assert "Sonuç ESFJ olarak kayıtlıdır." in dialog.commentary.toPlainText()
    assert "kaydedildi" in w.status.text()
    path = tmp_path / "açıklamalı.json"
    monkeypatch.setattr(w, "export_path", lambda *args: str(path))
    dialog.export_format.setCurrentText("JSON")
    dialog.findChild(QPushButton, "export_result").click()
    idle(w)
    assert json.loads(path.read_text())["ai_commentary"]["summary"] == reply["summary"]
