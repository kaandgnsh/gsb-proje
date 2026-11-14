"""Kısayol dosyalarını yalnız istenen dizinde üretir; masaüstüne/OS'ye kurulum yapmaz."""

import argparse
import sys
from pathlib import Path


def generate(output, db, *, platform=None, python=None):
    output = Path(output)
    platform = platform or sys.platform
    python = str(python or sys.executable)
    if any(c in str(output) + str(db) + python for c in "\n\r\x00"):
        raise ValueError("Kısayol yolunda denetim karakteri olamaz.")
    output.mkdir(parents=True, exist_ok=True)
    source = str(Path(__file__).resolve().parents[2])
    script = output / "yerel-envanter.pyw"
    script.write_text(
        f'import sys\nsys.path.insert(0, {source!r})\nfrom lai.ui.__main__ import main\nraise SystemExit(main(["--db", {str(Path(db).absolute())!r}]))\n',
        encoding="utf-8",
    )
    if platform == "win32":
        target = output / "Yerel Envanter.cmd"

        def quote(value):
            return '"' + str(value).replace("%", "%%") + '"'

        target.write_text(
            "@echo off\r\n" + quote(python) + " " + quote(script.absolute()) + "\r\n",
            encoding="utf-8",
        )
    else:

        def quote(value):
            value = str(value).replace("%", "%%")
            for c in ("\\", '"', "`", "$"):
                value = value.replace(c, "\\" + c)
            return '"' + value + '"'

        target = output / "yerel-envanter.desktop"
        target.write_text(
            "[Desktop Entry]\nType=Application\nName=Yerel Envanter\nComment=Çevrimdışı değerlendirme ve öğrenme\nTerminal=false\nExec="
            + quote(python)
            + " "
            + quote(script.absolute())
            + "\n",
            encoding="utf-8",
        )
    return target


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Taşınabilir masaüstü başlatma dosyası üretir"
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--db", type=Path, required=True)
    a = p.parse_args(argv)
    print(generate(a.output, a.db))


if __name__ == "__main__":
    main()
