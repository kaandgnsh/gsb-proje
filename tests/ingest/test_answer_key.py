import json

import pytest
from synthetic_desktop import ready
from synthetic_docs import write_guide_docx, write_guide_pdf

from lai.ingest.__main__ import main
from lai.ingest.answer_key import import_answer_key, parse_answer_key
from lai.learning.interactive import analyze
from lai.store.store import Store


@pytest.mark.parametrize("kind", ["csv", "xlsx", "json", "docx", "pdf"])
def test_standalone_key_preview_persist_source_and_no_scoring_rules(tmp_path, kind):
    from openpyxl import Workbook

    path = tmp_path / ("anahtar." + kind)
    if kind == "csv":
        path.write_text("Soru No,Cevap Anahtarı\n1,A\n2,B\n", encoding="utf-8")
    elif kind == "json":
        path.write_text(
            json.dumps(
                {
                    "format": "lai-answer-key",
                    "version": 1,
                    "answer_key": [
                        {"item": 1, "value": "A"},
                        {"item": 2, "value": "B"},
                    ],
                }
            )
        )
    elif kind == "xlsx":
        w = Workbook()
        s = w.active
        s.append(["Soru No", "Cevap Anahtarı"])
        s.append([1, "A"])
        s.append([2, "B"])
        w.save(path)
    elif kind == "docx":
        write_guide_docx(path, ["Cevap Anahtarı", "1-A 2-B"])
    else:
        write_guide_pdf(path, ["Cevap Anahtarı", "1-A 2-B"])
    parsed = parse_answer_key(path, known_items={1, 2})
    assert not parsed.has_errors and len(parsed.answer_keys) == 2
    assert parsed.answer_keys[0].locator.describe() != "konum bilinmiyor"
    db = tmp_path / "s.db"
    inv, *_ = ready(db)
    store = Store.open(db)
    before = len(store.knowledge.current(inv, kind="rule"))
    r = import_answer_key(store, inv, path, documents_dir=tmp_path / "documents")
    assert r["answer_key_count"] == 2 and not r["duplicate"]
    assert import_answer_key(store, inv, path, documents_dir=tmp_path / "documents")[
        "duplicate"
    ]
    assert (
        store.conn.execute("SELECT count(*) FROM parsed_answer_keys").fetchone()[0] == 2
    )
    assert len(store.knowledge.current(inv, kind="rule")) == before
    assert any(g.category == "answer_key" for g in analyze(store, inv))
    store.close()


def test_invalid_and_duplicate_key_have_row_context(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("Soru No,Cevap Anahtarı\n1,A\n1,B\n99,A\n", encoding="utf-8")
    result = parse_answer_key(p, known_items={1, 2})
    assert result.has_errors
    assert {i.code for i in result.issues} >= {"duplicate_key", "unknown_item"}
    assert all(i.locator.row for i in result.issues)


def test_key_cli_uses_same_service(tmp_path, capsys):
    db = tmp_path / "s.db"
    ready(db)
    p = tmp_path / "key.csv"
    p.write_text("Soru No,Cevap Anahtarı\n1,A\n", encoding="utf-8")
    assert (
        main(
            [
                "key",
                "--db",
                str(db),
                "--inventory",
                "Sentetik Türkçe Envanter",
                str(p),
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["answer_key_count"] == 1
