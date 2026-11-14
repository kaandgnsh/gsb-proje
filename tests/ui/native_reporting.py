"""M10 gerçek Wayland oturumu: GUI ile PDF/XLSX üret, uygulama içi salt okunur görüntüleyicide aç.

PYTHONPATH=src:tests/support QT_QPA_PLATFORM=wayland python tests/ui/native_reporting.py --output /tmp/m10-proof
Sadece sentetik veriler. Mevcut Qt PDF ve Excel önizlemesini açık eylemle kullanır.
"""

import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from openpyxl import load_workbook
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QLocale, QTimer
from PySide6.QtWidgets import QApplication, QPushButton
from synthetic_desktop import ready

from lai.assessment.service import submit_single
from lai.store.store import Store
from lai.ui.window import MainWindow


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="m10-native-"))
    db = work / "sentetik.db"
    _, iv, _, pv = ready(db)
    s = Store.open(db)
    result = submit_single(
        s,
        inventory_version_id=iv,
        profile_version_id=pv,
        participant_code="M10-Sentetik-Çığ",
        alias="Özgür Şule",
        responses=[(n, "A") for n in range(1, 9)],
    )
    s.close()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    QLocale.setDefault(QLocale("tr_TR"))
    w = MainWindow(db)
    w.show()
    report = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "pyside6": pyside_version,
        "qt_platform": app.platformName(),
        "synthetic_only": True,
        "model_used": False,
        "outputs": [],
    }
    step = 0
    preview_started = 0.0
    deadline = time.monotonic() + 65
    pdf = (args.output / "M10-Sentetik-Çığ.pdf").resolve()
    excel = (args.output / "M10-Sentetik-Çığ.xlsx").resolve()

    def advance():
        nonlocal step, preview_started
        try:
            if time.monotonic() > deadline:
                raise RuntimeError("Grafik oturum süre sınırı aşıldı")
            if w.task is not None:
                return
            if step == 0:
                w.start("result", {"assessment_id": result["assessment_id"]})
            elif step == 1:
                d = w.dialogs[-1]
                w.export_path = lambda *args: str(pdf)
                d.export_format.setCurrentText("PDF")
                d.findChild(QPushButton, "export_result").click()
            elif step == 2:
                assert pdf.exists() and w.last_export["path"] == str(pdf), (
                    w.status.text()
                )
                with pymupdf.open(pdf) as doc:
                    assert "Özgür Şule" in "".join(page.get_text() for page in doc)
                    report["pdf_pages"] = len(doc)
                w.open_export_button.click()
                preview_started = time.monotonic()
            elif step == 3:
                if time.monotonic() - preview_started < 1.5:
                    return
                preview = w.dialogs[-1]
                assert preview.objectName() == "report_preview"
                assert preview.document.pageCount() == report["pdf_pages"]
                preview.grab().save(str(args.output / "pdf-preview.png"))
                report["outputs"].append(
                    {"format": "pdf", "path": str(pdf), "opened_in_gui": True}
                )
                preview.close()
                w.dialogs[-2].close()
                w.nav.setCurrentRow(4)
                w.history.selectRow(0)
                w.export_path = lambda *args: str(excel)
                w.history_export_format.setCurrentText("XLSX")
                w.findChild(QPushButton, "export_history").click()
            elif step == 4:
                assert excel.exists() and w.last_export["path"] == str(excel), (
                    w.status.text()
                )
                wb = load_workbook(excel)
                assert wb["Özet"]["B3"].value == "Özgür Şule"
                wb.close()
                w.open_export_button.click()
            elif step == 5:
                preview = w.dialogs[-1]
                assert preview.objectName() == "report_preview"
                assert preview.tabs.count() == 4
                assert preview.tabs.widget(0).item(1, 1).text() == "Özgür Şule"
                preview.grab().save(str(args.output / "xlsx-preview.png"))
                report["outputs"].append(
                    {"format": "xlsx", "path": str(excel), "opened_in_gui": True}
                )
                preview.close()
                w.grab().save(str(args.output / "export-history.png"))
                report["passed"] = True
                w.close()
                app.quit()
                return
            step += 1
        except Exception as error:  # noqa: BLE001 - GUI harness must close safely
            report.update(
                passed=False, error=str(error), step=step, status=w.status.text()
            )
            w.close()
            if w.task is None:
                app.quit()

    timer = QTimer()
    timer.timeout.connect(advance)
    timer.start(100)
    app.exec()
    (args.output / "session.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
