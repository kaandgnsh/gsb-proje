"""İsteğe bağlı gerçek grafik oturumu; yalnız geçici sentetik veri, model çağrısı yok.

PYTHONPATH=src:tests/support python tests/ui/native_smoke.py --output /tmp/m09-proof
"""

import argparse
import csv
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

from PySide6 import __version__ as qt_version
from PySide6.QtCore import QLocale, QTimer
from PySide6.QtWidgets import QApplication, QPushButton
from synthetic_desktop import document, rules

from lai.assessment.service import job_status
from lai.store.store import Store
from lai.ui.backend import Backend
from lai.ui.window import MainWindow


class Paced(Backend):
    def _perform(self, s, op, a, cancel, progress, check):
        original = progress
        if op == "run_job":

            def progress(info):
                original(info)
                if "job" in info:
                    time.sleep(0.05)

        return super()._perform(s, op, a, cancel, progress, check)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="m09-native-"))
    db = work / "sentetik.db"
    doc = document(work / "Türkçe Sorular.json")
    source = work / "Sentetik Cevaplar.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["participant_id", *[f"Q{i}" for i in range(1, 9)]])
        writer.writerows([[f"SYN-{j}", *["A"] * 8] for j in range(20)])
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    QLocale.setDefault(QLocale("tr_TR"))
    w = MainWindow(db, backend=Paced(db))
    w.show()
    state = {"step": 0, "ticks": 0}
    deadline = time.monotonic() + 70
    report = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "pyside6": qt_version,
        "qt_platform": app.platformName(),
        "synthetic_only": True,
        "model_used": False,
    }

    def click(name):
        w.findChild(QPushButton, name).click()

    def advance():
        nonlocal w
        state["ticks"] += 1
        try:
            if time.monotonic() > deadline:
                raise RuntimeError("Native oturum süre sınırı aşıldı")
            step = state["step"]
            if step == 9:
                if w.task is not None or w.isVisible():
                    return
                report["close_and_pause"] = True
                w = MainWindow(db)
                w.show()
                state["step"] = 10
                return
            if step == 8:
                if "katılımcı kaydedildi" not in w.status.text():
                    return
                w.grab().save(str(args.output / "batch.png"))
                w.close()
                state["step"] = 9
                return
            if w.task is not None:
                return
            if step == 0:
                w.new_name.setText("Sentetik Yerel Oturum")
                click("create_inventory")
            elif step == 1:
                w.doc_path.setText(str(doc))
                click("preview_document")
            elif step == 2:
                assert w.preview and w.preview["questions"] == 8
                w.grab().save(str(args.output / "documents.png"))
                click("save_document")
            elif step == 3:
                s = Store.open(db)
                rules(s, w.inventory.currentData(), missing_policy=True)
                s.profiles.publish_version(
                    s.profiles.ensure_profile(w.inventory.currentData())
                )
                s.close()
                w.nav.setCurrentRow(1)
                click("learning_refresh")
            elif step == 4:
                index = next(
                    i
                    for i, g in enumerate(w.gap_data)
                    if g["category"] == "missing_answer_policy"
                )
                w.gap_table.selectRow(index)
                w.rule_editor.dimension.setText("*")
                w.rule_editor.kind.setCurrentIndex(
                    w.rule_editor.kind.findData("missing_answer_policy")
                )
                w.rule_editor.method.setCurrentIndex(
                    w.rule_editor.method.findData("skip")
                )
                w.answer_text.setPlainText(
                    "Sentetik açık bilgi: cevapsız madde atlanır."
                )
                click("teach")
            elif step == 5:
                w.grab().save(str(args.output / "learning.png"))
                w.nav.setCurrentRow(2)
                latest = w.catalog_data["profiles"][-1]["id"]
                w.profile.setCurrentIndex(w.profile.findData(latest))
                w.batch_profile.setCurrentIndex(w.batch_profile.findData(latest))
                click("load_items")
            elif step == 6:
                for i in range(w.answer_table.rowCount()):
                    w.answer_table.cellWidget(i, 2).setCurrentIndex(1)
                w.alias.setText("Sentetik Türkçe Katılımcı")
                click("evaluate")
            elif step == 7:
                assert w.last_result["result"]["dimensions"][0]["final"] is not None
                trace = w.dialogs[-1].trace
                trace.topLevelItem(0).setExpanded(True)
                trace.topLevelItem(0).child(0).setExpanded(True)
                w.dialogs[-1].grab().save(str(args.output / "result.png"))
                w.dialogs[-1].close()
                w.nav.setCurrentRow(3)
                w.batch_path.setText(str(source))
                click("preview_batch")
                state["step"] = 70
                return
            elif step == 70:
                assert w.batch_preview["total"] == 20
                click("prepare_job")
                state["step"] = 71
                return
            elif step == 71:
                state["job"] = w.job_id
                w.jobs.selectRow(0)
                click("run_job")
                state["step"] = 8
                return
            elif step == 10:
                w.nav.setCurrentRow(3)
                w.jobs.selectRow(0)
                click("run_job")
            elif step == 11:
                s = Store.open(db)
                job = job_status(s, state["job"], include_rows=True)
                assert job["state"] == "completed" and job["done"] == 20
                assert len({r["assessment_id"] for r in job["rows"]}) == 20
                assert (
                    s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0]
                    == 21
                )
                report.update(
                    import_preview=True,
                    learning_answer=True,
                    single_result=True,
                    batch_resume=True,
                    duplicate_results=False,
                    heartbeat_ticks=state["ticks"],
                    job_counts=job["counts"],
                )
                s.close()
                report["passed"] = True
                timer.stop()
                w.close()
                app.quit()
                return
            state["step"] += 1
        except Exception as e:  # noqa: BLE001 — deneme raporu yazılır ve worker güvenle kapanır.
            report.update(passed=False, error=str(e), step=state["step"])
            timer.stop()
            w.close()

            def stop_when_safe():
                if w.task is None:
                    app.quit()
                else:
                    QTimer.singleShot(50, stop_when_safe)

            stop_when_safe()

    timer = QTimer()
    timer.timeout.connect(advance)
    timer.start(80)
    app.exec()
    (args.output / "session.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
