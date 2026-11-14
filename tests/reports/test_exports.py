import csv
import json
import os
import subprocess
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
from openpyxl import load_workbook
from synthetic_desktop import ready

from lai.assessment.service import create_job, run_job, submit_single
from lai.reports import service
from lai.store.store import Store


@pytest.fixture
def saved(tmp_path):
    db = tmp_path / "s.db"
    inv, iv, pid, pv = ready(db)
    store = Store.open(db)
    result = submit_single(
        store,
        inventory_version_id=iv,
        profile_version_id=pv,
        participant_code="Çığ-İş",
        alias="Özgür Şule",
        responses=[(n, "A") for n in range(1, 9)],
    )
    yield store, result["assessment_id"], (inv, iv, pid, pv)
    store.close()


def cells(path):
    wb = load_workbook(path, data_only=False)
    values = {s.title: list(s.values) for s in wb}
    wb.close()
    return values


@pytest.mark.parametrize("format", ["pdf", "xlsx", "csv", "json"])
def test_all_formats_from_saved_result(saved, tmp_path, format):
    s, aid, _ = saved
    before = service.dumps(service.assessment_report(s, aid))
    target = tmp_path / f"Çıktı.{format}"
    service.export_assessment(s, aid, target, format)
    report = service.assessment_report(s, aid)
    assert service.dumps(report) == before
    a = report["assessment"]
    if format == "json":
        assert json.loads(target.read_text()) == report
        return
    if format == "pdf":
        with pymupdf.open(target) as doc:
            text = "\n".join(p.get_text() for p in doc)
            assert len(doc) >= 2
            assert all(any("DejaVu" in font[3] for font in p.get_fonts()) for p in doc)
        for value in [
            "Çığ-İş",
            "Özgür Şule",
            a["status"],
            "Karar kuralı",
            a["created_at"],
            "Profil sürüm kimliği",
        ]:
            assert value in text
    elif format == "xlsx":
        data = cells(target)
        assert dict(data["Özet"][1:])["Profil sürüm kimliği"] == str(
            a["profile_version_id"]
        )
        assert data["Boyutlar"][1][3] == service.dumps(
            a["results"]["dimensions"][0]["final"]
        )
        text = str(data)
    else:
        rows = list(csv.reader(target.open(encoding="utf-8-sig", newline="")))
        assert len({len(r) for r in rows}) == 1
        text = str(rows)
    for d in a["results"]["dimensions"]:
        assert d["dimension"] in text
        assert d["selected_pole"] in text
        assert d["status"] in text
        for v in d["final"].values():
            assert v in text
    assert report["profile_code"] in text


def test_decimal_json_is_lossless():
    value = Decimal("12345678901234567890.0000000000000000000190")
    assert json.loads(service.dumps({"score": value}))["score"] == str(value)
    with pytest.raises(ValueError):
        service.dumps(Decimal("NaN"))


@pytest.mark.parametrize(
    "value",
    [
        "=1+1",
        "+SUM(A1)",
        "-2+3",
        "@SUM(A1)",
        "\t=1+1",
        '  =HYPERLINK("x")',
        "\r=1",
        "\n=1",
    ],
)
def test_spreadsheet_injection_is_text(saved, tmp_path, value):
    s, aid, _ = saved
    a = s.assessments.get(aid)
    participant = s.conn.execute(
        "SELECT participant_id FROM response_sets WHERE id=?", (a.response_set_id,)
    ).fetchone()[0]
    s.participants.update_details(participant, alias=value)
    xlsx = tmp_path / "s.xlsx"
    service.export_assessment(s, aid, xlsx, "xlsx")
    wb = load_workbook(xlsx, data_only=False)
    cell = wb["Özet"]["B3"]
    assert cell.value == value and cell.data_type == "s"
    assert not any(c.data_type == "f" for sheet in wb for row in sheet for c in row)
    wb.close()
    output = tmp_path / "s.csv"
    service.export_assessment(s, aid, output, "csv")
    rows = list(csv.reader(output.open(encoding="utf-8-sig", newline="")))
    assert next(r[2] for r in rows if r[1] == "Takma ad") == "'" + value


def test_partial_and_old_versions(saved, tmp_path):
    s, aid, (inv, iv, pid, pv) = saved
    from lai.core.rules import rule_key

    body = {"dimension": "E-I", "policy": "invalidate"}
    s.knowledge.apply_user_correction(
        inv,
        entry_key=rule_key("zero_score", body),
        content={"body": body},
        rule_type="zero_score",
        dimension_code="E-I",
        summary="Sıfır",
        explanation="Sentetik",
        user_text="Sıfır geçersiz",
    )
    newer = s.profiles.publish_version(pid)
    partial = submit_single(
        s,
        inventory_version_id=iv,
        profile_version_id=newer.id,
        participant_code="Kısmi",
        responses=[],
    )
    out = tmp_path / "partial.json"
    service.export_assessment(s, partial["assessment_id"], out, "json")
    r = json.loads(out.read_text())
    assert r["profile_code"] is None
    d = next(
        d for d in r["assessment"]["results"]["dimensions"] if d["dimension"] == "E-I"
    )
    assert d["selected_pole"] is None and d["limitations"]
    assert d["final"] is None
    old = service.assessment_report(s, aid)
    assert old["assessment"]["profile_version_id"] == pv
    assert old["metadata"]["profile_version"] < newer.version
    for fmt in ["pdf", "csv", "xlsx"]:
        service.export_assessment(
            s, partial["assessment_id"], tmp_path / f"p.{fmt}", fmt
        )


def test_long_pdf_preserves_end_and_pages(saved, tmp_path):
    s, aid, _ = saved
    report = service.assessment_report(s, aid)
    # Render a synthetic report snapshot; no immutable DB modification.
    report["assessment"]["results"]["dimensions"][0]["rules"].append(
        {"source": "Çığ Şule " * 5000 + "SON_KANIT_İĞ"}
    )
    out = tmp_path / "long.pdf"
    service.write_pdf(out, report, None)
    with pymupdf.open(out) as doc:
        assert len(doc) > 10
        text = "\n".join(p.get_text() for p in doc)
        assert "SON_KANIT_İĞ" in text
        assert all(f"Sayfa {n + 1}" in p.get_text() for n, p in enumerate(doc))


def test_atomic_write_error_preserves_target(saved, tmp_path, monkeypatch):
    s, aid, _ = saved
    target = tmp_path / "old.pdf"
    target.write_bytes(b"OLD")

    def failed(path, *args):
        path.write_bytes(b"PARTIAL")
        raise OSError("Sentetik disk yazma hatası")

    monkeypatch.setattr(service, "write_pdf", failed)
    with pytest.raises(OSError):
        service.export_assessment(s, aid, target, "pdf")
    assert target.read_bytes() == b"OLD"
    assert not list(tmp_path.glob(".lai-export-*"))
    target.unlink()
    with pytest.raises(OSError):
        service.export_assessment(s, aid, target, "pdf")
    assert not target.exists()


def test_replace_error_preserves_target(saved, tmp_path, monkeypatch):
    s, aid, _ = saved
    target = tmp_path / "out.json"
    target.write_text("OLD")

    def deny(*args):
        raise PermissionError("Sentetik izin hatası")

    monkeypatch.setattr(service.os, "replace", deny)
    with pytest.raises(PermissionError):
        service.export_assessment(s, aid, target, "json")
    assert target.read_text() == "OLD"
    assert not list(tmp_path.glob(".lai-export-*"))


def batch(s, iv, pv, tmp_path):
    p = tmp_path / "toplu.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["participant_code", *[f"Q{n}" for n in range(1, 9)]])
        for code, answer in [
            ("Bir", "A"),
            ("Kısmi", ""),
            ("Hatalı", "Z"),
            ("Bekleyen", "B"),
        ]:
            w.writerow([code, *[answer] * 8])
    from lai.core.rules import rule_key

    old = s.profiles.get_version(pv)
    inv = s.conn.execute(
        "SELECT inventory_id FROM inventory_versions WHERE id=?", (iv,)
    ).fetchone()[0]
    body = {"dimension": "*", "policy": "invalidate"}
    s.knowledge.apply_user_correction(
        inv,
        entry_key=rule_key("missing_answer_policy", body),
        content={"body": body},
        rule_type="missing_answer_policy",
        dimension_code="*",
        summary="Cevapsız geçersiz",
        explanation="Sentetik",
        user_text="Geçersiz say",
    )
    new = s.profiles.publish_version(old.profile_id)
    return create_job(s, p, inventory_version_id=iv, profile_version_id=new.id)["id"]


@pytest.mark.parametrize("format", ["csv", "xlsx", "pdf"])
def test_batch_failed_partial_pending_summary(saved, tmp_path, format):
    s, _, (_, iv, _, pv) = saved
    jid = batch(s, iv, pv, tmp_path)
    run_job(s, jid, limit=2)
    out = tmp_path / ("toplu.zip" if format == "pdf" else f"toplu.{format}")
    result = service.export_job(s, jid, out, format)
    assert result["counts"] == {"succeeded": 1, "partial": 1, "failed": 1, "pending": 1}
    if format == "pdf":
        with zipfile.ZipFile(out) as z:
            pdfs = [n for n in z.namelist() if n.endswith(".pdf")]
            assert len(pdfs) == 2
            with pymupdf.open(stream=z.read(pdfs[0]), filetype="pdf") as doc:
                assert "Bir" in "".join(p.get_text() for p in doc)
            text = z.read("is-ozeti.csv").decode("utf-8-sig")
    elif format == "xlsx":
        text = str(cells(out))
    else:
        text = out.read_text(encoding="utf-8-sig")
    for value in ["partial", "failed", "pending", "Hatalı", "Bekleyen", "Profil sürüm"]:
        assert value in text
    assert s.conn.execute("SELECT count(*) FROM assessments").fetchone()[0] == 3


def test_cancel_export_removes_temporary(saved, tmp_path):
    s, _, (_, iv, _, pv) = saved
    jid = batch(s, iv, pv, tmp_path)
    run_job(s, jid, limit=2)
    out = tmp_path / "cancel.zip"
    cancelled = False

    def progress(data):
        nonlocal cancelled
        if data["phase"] == "writing":
            cancelled = True

    with pytest.raises(service.ExportCancelled):
        service.export_job(
            s, jid, out, "pdf", cancel=lambda: cancelled, progress=progress
        )
    assert not out.exists() and not list(tmp_path.glob(".lai-export-*"))
    assert not s.conn.in_transaction


def test_offline_export_never_calls_model_or_motor(saved, tmp_path, monkeypatch):
    import socket

    import lai.core.scoring

    def forbidden(*a, **kw):
        raise AssertionError("Dışa aktarma ağ veya puanlama çağırdı")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(lai.core.scoring, "score_assessment", forbidden)
    s, aid, _ = saved
    for format in ["pdf", "csv", "xlsx", "json"]:
        service.export_assessment(s, aid, tmp_path / f"offline.{format}", format)


def test_cli_shared_export(saved, tmp_path):
    s, aid, _ = saved
    path = tmp_path / "cli.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lai.learning",
            "export",
            "--db",
            str(tmp_path / "s.db"),
            "--id",
            str(aid),
            "--format",
            "json",
            "--output",
            str(path),
            "--json",
        ],
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(path.read_text()) == service.assessment_report(s, aid)
    assert json.loads(result.stdout)["assessment_id"] == aid


def test_database_is_not_an_export_target(saved):
    s, aid, _ = saved
    for path in [s.path, str(s.path) + "-wal", str(s.path) + "-shm"]:
        with pytest.raises(ValueError, match="Veritabanı"):
            service.export_assessment(s, aid, path, "json")
    assert s.assessments.get(aid).id == aid


def test_excel_limit_rejects_without_partial_file(saved, tmp_path):
    s, aid, _ = saved
    r = service.assessment_report(s, aid)
    r["metadata"]["alias"] = "Ş" * 32768
    target = tmp_path / "too-long.xlsx"
    with pytest.raises(ValueError, match="32767"):
        service.atomic_write(
            target, lambda p: service.write_xlsx(p, service.single_tables(r), None)
        )
    assert not target.exists()
    assert not list(tmp_path.glob(".lai-export-*"))
