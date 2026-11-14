"""python -m lai.ui [--db YOL] — yalnız uygulamayı açar; kurulum/model başlatmaz."""

import argparse
import os
import sys
from pathlib import Path


def main(argv=None):
    p = argparse.ArgumentParser(description="Türkçe yerel envanter masaüstü")
    p.add_argument("--db", type=Path)
    a = p.parse_args(argv)
    if a.db is None:
        from lai_setup.paths import resolve_paths

        paths = resolve_paths(
            "windows" if sys.platform == "win32" else "linux", os.environ, Path.home()
        )
        a.db = paths.data / "lai.db"
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    from PySide6.QtWidgets import QApplication

    from .window import MainWindow

    app = QApplication(sys.argv[:1])
    QLocale.setDefault(QLocale("tr_TR"))
    translator = QTranslator(app)
    translator.load("qtbase_tr", QLibraryInfo.path(QLibraryInfo.TranslationsPath))
    app.installTranslator(translator)
    app.setApplicationName("Yerel Envanter")
    app.setOrganizationName("LocalAssessmentAI")
    w = MainWindow(a.db)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
