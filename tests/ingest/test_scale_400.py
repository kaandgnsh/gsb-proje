"""400 soruluk sentetik belgeler: her biçimden kayıpsız içe aktarma, süre, bellek, bütünlük, CLI."""
import json
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import pytest
from synthetic_docs import (answer_key_for, make_doc_inventory, write_csv, write_docx, write_json,
                            write_pdf, write_xlsx)

from lai.ingest.__main__ import main
from lai.ingest.parse import parse_file
from lai.ingest.service import import_document


INV = make_doc_inventory(400, forced_every=25)  # her 25. madde iki seçenekli
KEY = answer_key_for(INV)
EXPECTED = [(it["number"], it["text"], it["options"]) for it in INV["items"]]

WRITERS = {
    "pdf": lambda p: write_pdf(INV, p, answer_key=KEY),
    "pdf-inline": lambda p: write_pdf(INV, p, answer_key=KEY, inline_options=True),
    "docx": lambda p: write_docx(INV, p, answer_key=KEY),
    "docx-auto": lambda p: write_docx(INV, p, auto_numbering=True, answer_key=KEY),
    "docx-table": lambda p: write_docx(INV, p, as_table=True),
    "xlsx": lambda p: write_xlsx(INV, p, answer_key=KEY, extra_sheet_first=True),
    "csv": lambda p: write_csv(INV, p, answer_key=KEY),
    "json": lambda p: write_json(INV, p, answer_key=KEY),
}
EXT = {"pdf-inline": "pdf", "docx-auto": "docx", "docx-table": "docx"}


@pytest.mark.parametrize("variant", list(WRITERS))
def test_400_items_lossless_every_format(store, inventory_id, docs_dir, tmp_path, variant):
    path = WRITERS[variant](tmp_path / f"envanter.{EXT.get(variant, variant)}")
    t = time.perf_counter()
    rep = import_document(store, inventory_id, path, documents_dir=docs_dir)
    elapsed = time.perf_counter() - t
    assert rep.status == "imported", [(i.code, i.message) for i in rep.errors]
    # Beklenen uyarılar: iki seçenekli maddeler (her 25.) bilerek farklı; satır içi seçenekli
    # PDF'te uzun seçenek satırı alt satıra kayar. Başka uyarı olmamalı.
    allowed = {"option_count_differs"} | ({"multiline_option"} if variant == "pdf-inline" else set())
    assert {i.code for i in rep.warnings} <= allowed, [(i.code, i.message) for i in rep.warnings]
    assert sorted(i.item_number for i in rep.warnings if i.code == "option_count_differs") == \
        list(range(25, 401, 25))
    got = store.inventory.items(rep.inventory_version_id)
    # numara, özgün sıra, metin (Türkçe karakterler birebir) ve seçenek eşleşmesi
    assert [(i["number"], i["text"], i["options"]) for i in got] == EXPECTED
    order = [r[0] for r in store.conn.execute(
        "SELECT number FROM parsed_items WHERE import_run_id=? ORDER BY seq", (rep.run_id,))]
    assert order == list(range(1, 401))
    if variant != "docx-table":
        keys = dict(store.conn.execute("SELECT item_number, value FROM parsed_answer_keys "
                                       "WHERE import_run_id=?", (rep.run_id,)).fetchall())
        assert keys == KEY
    assert elapsed < 30, f"{variant}: {elapsed:.1f} sn"
    assert store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_all_formats_into_one_inventory_yield_one_version(store, inventory_id, docs_dir, tmp_path):
    reps = [import_document(store, inventory_id, WRITERS[v](tmp_path / f"e-{v}.{EXT.get(v, v)}"),
                            documents_dir=docs_dir) for v in WRITERS]
    assert {r.status for r in reps} == {"imported"}
    assert len({r.inventory_version_id for r in reps}) == 1  # içerik aynı → tek sürüm
    assert store.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 400


def test_parse_memory_is_bounded_for_large_tabular_file(tmp_path):
    """20.000 satırlık (~4 MB) CSV: Python bellek tepe değeri sınırlı kalır."""
    big = make_doc_inventory(400)
    rows = [{"number": n, "text": f"{big['items'][(n - 1) % 400]['text']} [{n}]",
             "options": big["items"][0]["options"]} for n in range(1, 20001)]
    p = write_csv({"name": "büyük", "items": rows}, tmp_path / "buyuk.csv")
    tracemalloc.start()
    res = parse_file(p)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(res.items) == 20000 and not res.has_errors
    mb = p.stat().st_size / 1e6
    assert peak / 1e6 < 60 * max(mb, 1), f"tepe {peak / 1e6:.0f} MB, dosya {mb:.1f} MB"


def test_large_pdf_rss_in_subprocess(tmp_path):
    """2.000 maddelik (~250 sayfa) PDF ayrı süreçte ayrıştırılır; en yüksek RSS ölçülür."""
    inv = make_doc_inventory(400)
    items = [{"number": n, "text": f"{inv['items'][(n - 1) % 400]['text']} ({n})",
              "options": inv["items"][0]["options"]} for n in range(1, 2001)]
    p = write_pdf({"name": "b", "items": items}, tmp_path / "buyuk.pdf")
    # ru_maxrss exec'ten sonra da üst sürecin (pytest) değerini taşır; VmHWM exec'te sıfırlanır.
    code = ("import sys; from lai.ingest.parse import parse_file; r=parse_file(sys.argv[1]); "
            "hwm=[l.split()[1] for l in open('/proc/self/status') if l.startswith('VmHWM')][0]; "
            "print(len(r.items), hwm)")
    src = Path(__file__).resolve().parents[2] / "src"
    out = subprocess.run([sys.executable, "-c", code, str(p)], capture_output=True, text=True,
                         env={"PYTHONPATH": str(src)}, timeout=300, check=True).stdout.split()
    n, rss_kb = int(out[0]), int(out[1])
    assert n == 2000
    assert rss_kb / 1024 < 400, f"RSS {rss_kb / 1024:.0f} MB"


def test_cli_parse_and_import(tmp_path, capsys):
    pdf = write_pdf(INV, tmp_path / "e.pdf", answer_key=KEY)
    assert main(["parse", str(pdf)]) == 0
    assert "400 madde" in capsys.readouterr().out
    db = tmp_path / "data" / "lai.db"
    args = ["import", "--db", str(db), "--inventory", "Sentetik İş Envanteri", str(pdf)]
    assert main(args) == 0
    assert "İÇE AKTARILDI" in capsys.readouterr().out
    assert main(args + ["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["status"] == "duplicate"
    bad = tmp_path / "eski.xls"
    bad.write_bytes(b"\xd0\xcf\x11\xe0")
    assert main(["import", "--db", str(db), "--inventory", "X", str(bad)]) == 1
    assert "BAŞARISIZ" in capsys.readouterr().out
