"""python -m lai.ui [--db YOL] — yalnız uygulamayı açar; kurulum/model başlatmaz."""

import argparse
import os
import sys
from pathlib import Path


def main(argv=None):
    p = argparse.ArgumentParser(description="Türkçe yerel envanter masaüstü")
    p.add_argument("--db", type=Path)
    p.add_argument(
        "--write-dir",
        type=Path,
        action="append",
        default=[],
        help="Yalıtım içinde yazılabilecek ek belge/rapor klasörü",
    )
    a = p.parse_args(argv)
    if a.db is None:
        from lai_setup.paths import resolve_paths

        paths = resolve_paths(
            "windows" if sys.platform == "win32" else "linux", os.environ, Path.home()
        )
        a.db = paths.data / "lai.db"
    from lai_runtime.sandbox import GUIDANCE, IsolationError, launch, require_isolation

    try:
        if not os.environ.get("LAI_SANDBOX_ACTIVE"):
            # Qt ve değerlendirme deposu güvenlik sınırı kurulmadan açılmaz.
            if sys.platform != "linux":
                raise IsolationError(GUIDANCE)
            from lai_setup.paths import resolve_paths

            paths = resolve_paths("linux", os.environ, Path.home())
            writes = [
                a.db.expanduser().absolute().parent,
                paths.root,
                paths.config_dir,
                *a.write_dir,
            ]
            for folder in writes:
                folder.mkdir(parents=True, exist_ok=True)
            cmd = [
                sys.executable,
                "-m",
                "lai.ui",
                "--db",
                str(a.db.expanduser().absolute()),
            ]
            result = launch(
                cmd,
                writable=writes,
                gui=True,
                env={**os.environ, "LAI_HOME": str(paths.root)},
            )
            if result.returncode:
                print(GUIDANCE, file=sys.stderr)
            return result.returncode
        require_isolation()
    except (IsolationError, OSError) as error:
        print("HATA: " + str(error), file=sys.stderr)
        return 2
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
    try:
        return app.exec()
    finally:
        w.backend.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
