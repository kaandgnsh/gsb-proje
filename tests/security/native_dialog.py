"""Yalıtım altında gerçek Wayland dosya seçici ve normal masaüstü giriş noktası."""

import json
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit

from lai_runtime.sandbox import require_isolation

work = Path(sys.argv[1])
work.mkdir(parents=True, exist_ok=True)
report = {"kernel": require_isolation()}
if "--entry" in sys.argv:
    import lai.ui.window
    from lai.ui.__main__ import main

    original = lai.ui.window.MainWindow

    class Checked(original):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)

            def close_when_ready():
                if self.task is not None:
                    QTimer.singleShot(50, close_when_ready)
                    return
                report["platform"] = QApplication.platformName()
                self.grab().save(str(work / "entry.png"))
                self.close()

            QTimer.singleShot(400, close_when_ready)

    lai.ui.window.MainWindow = Checked
    report["entry_returncode"] = main(["--db", str(work / "entry.db")])
    assert report["entry_returncode"] == 0 and (work / "entry.db").exists()
else:
    app = QApplication([])
    report["platform"] = app.platformName()
    source = work / "Türkçe Sentetik Seçim.json"
    source.write_text('{"sentetik":true}')
    from lai.ui.window import MainWindow

    window = MainWindow(work / "dialog.db")
    window.show()

    def choose():
        if window.task is not None:
            QTimer.singleShot(50, choose)
            return

        def select():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, QFileDialog)
            assert dialog.labelText(QFileDialog.Accept) == "Seç"
            dialog.findChild(QLineEdit, "fileNameEdit").setText(str(source))
            dialog.grab().save(str(work / "file-dialog.png"))
            dialog.accept()

        QTimer.singleShot(300, select)
        window.choose_file(window.doc_path, "Sentetik belge seç", "JSON (*.json)")
        assert window.doc_path.text() == str(source)
        report["file_selection"] = True
        window.close()
        app.quit()

    QTimer.singleShot(100, choose)
    QTimer.singleShot(10000, app.quit)
    app.exec()
    assert report.get("file_selection"), "Dosya seçimi süre sınırı aşıldı"

(
    work / ("entry-proof.json" if "--entry" in sys.argv else "dialog-proof.json")
).write_text(json.dumps(report, indent=2))
print(json.dumps(report))
