"""M10: donmuş değerlendirmeler, işlem tutarlı toplu görüntü ve atomik çıktı.

Motor, Qt ve model çağrısı yoktur. Decimal JSON'da ondalık metin olarak taşınır.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import zipfile
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from lai.assessment.service import job_status


class ExportCancelled(RuntimeError):
    pass


def dumps(value, **kwargs):
    def encode(v):
        if isinstance(v, Decimal) and v.is_finite():
            return str(v)
        raise ValueError("JSON'a aktarılamayan veya sonlu olmayan değer")

    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, default=encode, **kwargs
    )


def check_cancel(cancel):
    if cancel and cancel():
        raise ExportCancelled("Dışa aktarma iptal edildi; hedef dosya değiştirilmedi.")


def assessment_report(store, assessment_id):
    """Yalnız kaydın ilişkili sürümlerini ve kaydedilmiş dökümünü oku."""
    assessment = asdict(store.assessments.get(assessment_id))
    row = store.conn.execute(
        "SELECT p.code,p.alias,r.inventory_version_id,v.version AS inventory_version,"
        "i.name AS inventory_name FROM response_sets r JOIN participants p ON p.id=r.participant_id "
        "JOIN inventory_versions v ON v.id=r.inventory_version_id "
        "JOIN inventories i ON i.id=v.inventory_id WHERE r.id=?",
        (assessment["response_set_id"],),
    ).fetchone()
    profile = store.profiles.get_version(assessment["profile_version_id"])
    meta = dict(row)
    meta.update(
        profile_version=profile.version,
        profile_hash=profile.content_hash,
        identity_note="Kod sabittir; takma ad güncel katılımcı kaydından okunur.",
    )
    result = assessment["results"]
    dims = result.get("dimensions", [])
    # Kod üretmeyiz; yalnız eksiksiz kayıtlı kodu gösteririz.
    code = result.get("full_profile")
    if not (
        len(dims) == 4
        and all(
            d.get("selected_pole")
            and d.get("final") is not None
            and d.get("status") != "NOT_EVALUABLE"
            for d in dims
        )
        and code == "".join(d["selected_pole"] for d in dims)
    ):
        code = None
    explanations = []
    for d in dims:
        if d.get("selected_pole") is None:
            explanations.append(
                f"{d['dimension']}: hesaplanamadı. "
                + "; ".join(d.get("limitations", []))
            )
        else:
            explanations.append(
                f"{d['dimension']}: kayıtlı karar kuralına göre {d['selected_pole']} seçildi."
            )
    return {
        "schema": "lai-report-v1",
        "metadata": meta,
        "assessment": assessment,
        "profile_code": code,
        "explanations": explanations,
        "notice": (
            "GEÇİCİ (PROVISIONAL): doğrulanmamış veya eksik bilgiler vardır."
            if assessment["status"] != "VERIFIED"
            else "Doğrulanmış (VERIFIED) kayıt."
        ),
        "commentary_source": "deterministic_template_v1",
    }


def job_report(store, job_id, *, cancel=None, progress=None):
    # Tek okuma transaction'ı: çalışan başka süreçteki checkpoint sayımlarıyla
    # katılımcı listesi birbiriyle tutarlıdır. Yazma kilidi alınmaz.
    if store.conn.in_transaction:
        raise ValueError("Rapor için açık bir işlem bulunmamalı")
    store.conn.execute("BEGIN")
    try:
        job = job_status(store, job_id, include_rows=True)
        reports = {}
        for n, row in enumerate(job["rows"], 1):
            check_cancel(cancel)
            if row["assessment_id"] is not None:
                reports[row["assessment_id"]] = assessment_report(
                    store, row["assessment_id"]
                )
            if progress:
                progress({"phase": "reading", "done": n, "total": job["total"]})
        return {"schema": "lai-job-report-v1", "job": job, "reports": reports}
    finally:
        store.conn.rollback()


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return dumps(value)
    return str(value)


def safe_csv(value):
    text = _text(value)
    # Excel/LibreOffice leading whitespace/control handling must not enable formulas.
    if text.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")) or text.startswith(
        ("\t", "\r", "\n")
    ):
        return "'" + text
    return text


def single_tables(report):
    a, m = report["assessment"], report["metadata"]
    summary = [
        ("Alan", "Değer"),
        ("Katılımcı kodu", m["code"]),
        ("Takma ad", m["alias"]),
        ("Kimlik açıklaması", m["identity_note"]),
        ("Envanter", m["inventory_name"]),
        ("Envanter sürüm kimliği", m["inventory_version_id"]),
        ("Envanter sürümü", m["inventory_version"]),
        ("Profil sürüm kimliği", a["profile_version_id"]),
        ("Profil sürümü", m["profile_version"]),
        ("Profil özeti SHA256", m["profile_hash"]),
        ("Değerlendirme", a["id"]),
        ("Tarih", a["created_at"]),
        ("Durum", a["status"]),
        ("Profil kodu", report["profile_code"]),
        ("Uyarı", report["notice"]),
        ("Sınırlamalar", a["limitations"]),
        ("Motor sürümü", a["engine_version"]),
        ("Girdi özeti SHA256", a["input_hash"]),
    ]
    dimensions = [
        (
            "Boyut",
            "Durum",
            "Ham puan",
            "Nihai puan",
            "Gösterim puanı",
            "Seçilen kutup",
            "Karar kuralı",
            "Gerekçe",
        )
    ]
    trace = [("Boyut", "Bölüm", "Sıra", "Kayıt")]
    for d in a["results"].get("dimensions", []):
        dimensions.append(
            tuple(
                d.get(k)
                for k in (
                    "dimension",
                    "status",
                    "raw",
                    "final",
                    "displayed",
                    "selected_pole",
                    "decision",
                    "limitations",
                )
            )
        )
        for section in ("contributions", "transformations", "rules"):
            for n, entry in enumerate(d.get(section, []), 1):
                trace.append((d["dimension"], section, n, entry))
    return [
        ("Özet", summary),
        ("Boyutlar", dimensions),
        ("Hesap dökümü", trace),
        (
            "Açıklamalar",
            [("Türkçe açıklama",), *[(v,) for v in report["explanations"]]],
        ),
    ]


def batch_tables(payload):
    job = payload["job"]
    summary = [
        ("Alan", "Değer"),
        ("İş kimliği", job["id"]),
        ("Durum", job["state"]),
        ("Dosya", job["file_name"]),
        ("Envanter sürüm kimliği", job["inventory_version_id"]),
        ("Profil sürüm kimliği", job["profile_version_id"]),
        ("Tarih", job["created_at"]),
        ("Toplam", job["total"]),
        ("Başarılı", job["counts"]["succeeded"]),
        ("Kısmi", job["counts"]["partial"]),
        ("Hatalı", job["counts"]["failed"]),
        ("Bekleyen", job["counts"]["pending"]),
    ]
    participants = [
        (
            "Sıra",
            "Katılımcı",
            "Takma ad",
            "İş durumu",
            "Değerlendirme",
            "Sonuç durumu",
            "Profil kodu",
            "Envanter sürümü",
            "Profil sürümü",
            "Tarih",
            "Dosya",
            "Sayfa",
            "Satır",
            "Hata / gerekçe",
        )
    ]
    dimensions = [
        (
            "Değerlendirme",
            "Katılımcı",
            "Boyut",
            "Durum",
            "Ham puan",
            "Nihai puan",
            "Seçilen kutup",
            "Karar kuralı",
            "Gerekçe",
        )
    ]
    for row in job["rows"]:
        r = payload["reports"].get(row["assessment_id"])
        identity = row["identity"]
        participants.append(
            (
                row["seq"],
                identity.get("participant_code"),
                identity.get("alias"),
                row["state"],
                row["assessment_id"],
                row["result_status"],
                r["profile_code"] if r else None,
                job["inventory_version_id"],
                job["profile_version_id"],
                r["assessment"]["created_at"] if r else None,
                job["file_name"],
                row["sheet_name"],
                row["row_number"],
                row["errors"] + row["limitations"],
            )
        )
        if r:
            for d in r["assessment"]["results"].get("dimensions", []):
                dimensions.append(
                    (
                        row["assessment_id"],
                        r["metadata"]["code"],
                        *[
                            d.get(k)
                            for k in (
                                "dimension",
                                "status",
                                "raw",
                                "final",
                                "selected_pole",
                                "decision",
                                "limitations",
                            )
                        ],
                    )
                )
    return [
        ("İş özeti", summary),
        ("Katılımcılar", participants),
        ("Boyutlar", dimensions),
    ]


def write_csv(path, tables, cancel, progress=None):
    total = sum(len(table) for _, table in tables)
    done = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        width = max(len(row) for _, table in tables for row in table) + 1
        writer.writerow(["Bölüm"] + [f"Alan {n}" for n in range(1, width)])
        for name, table in tables:
            for row in table:
                check_cancel(cancel)
                values = [name, *row]
                writer.writerow(
                    [safe_csv(v) for v in values] + [""] * (width - len(values))
                )
                done += 1
                if progress and (done % 100 == 0 or done == total):
                    progress({"phase": "writing", "done": done, "total": total})


def write_xlsx(path, tables, cancel, progress=None):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    total = sum(len(table) for _, table in tables)
    done = 0
    try:
        for name, table in tables:
            ws = wb.create_sheet(name)
            ws.freeze_panes = "A2"
            ws.column_dimensions["A"].width = 26
            ws.column_dimensions["B"].width = 42
            for n, row in enumerate(table):
                check_cancel(cancel)
                cells = []
                for value in row:
                    text = _text(value)
                    # Never silently truncate Excel's per-cell limit.
                    if len(text) > 32767:
                        raise ValueError(
                            "Excel hücresi 32767 karakter sınırını aşıyor; JSON kullanın."
                        )
                    cell = WriteOnlyCell(ws, text)
                    cell.data_type = "s"  # Explicit text, including =/+/-/@ and numeric Decimal strings.
                    if n == 0:
                        cell.font = Font(bold=True)
                    cells.append(cell)
                ws.append(cells)
                done += 1
                if progress and (done % 100 == 0 or done == total):
                    progress({"phase": "writing", "done": done, "total": total})
        wb.save(path)
    finally:
        for ws in wb.worksheets:
            if not ws.closed:
                ws.close()
            writer = getattr(ws, "_writer", None)
            if writer and Path(writer.out).exists():
                writer.cleanup()
        wb.close()


def write_pdf(path, report, cancel):
    from .pdf import render

    render(path, report, cancel)


def atomic_write(target, writer, *, cancel=None):
    target = Path(target).expanduser().absolute()
    if not target.parent.is_dir():
        raise ValueError("Hedef klasör bulunamadı")
    check_cancel(cancel)
    fd, temporary = tempfile.mkstemp(
        prefix=".lai-export-", suffix=target.suffix, dir=target.parent
    )
    os.close(fd)
    try:
        writer(Path(temporary))
        check_cancel(cancel)
        with open(temporary, "rb") as f:
            os.fsync(f.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(target)


def validate_target(store, target):
    """Rapor seçimi açık veritabanını veya günlüklerini ezemez."""
    if store.path:
        db = store.path.resolve()
        output = Path(target).expanduser().resolve()
        if output in {
            db,
            Path(str(db) + "-wal"),
            Path(str(db) + "-shm"),
            Path(str(db) + "-journal"),
        }:
            raise ValueError("Veritabanı veya işlem günlüğü rapor hedefi olamaz")


def export_assessment(
    store, assessment_id, target, format, *, cancel=None, progress=None
):
    if format not in ("pdf", "xlsx", "csv", "json"):
        raise ValueError("Tekil rapor biçimi PDF, XLSX, CSV veya JSON olmalı")
    validate_target(store, target)
    report = assessment_report(store, assessment_id)

    def write(path):
        check_cancel(cancel)
        if format == "json":
            path.write_text(dumps(report, indent=2), encoding="utf-8")
        elif format == "pdf":
            write_pdf(path, report, cancel)
        else:
            tables = single_tables(report)
            (write_csv if format == "csv" else write_xlsx)(
                path, tables, cancel, progress
            )

    output = atomic_write(target, write, cancel=cancel)
    if progress:
        progress({"phase": "writing", "done": 1, "total": 1})
    return {"path": output, "format": format, "assessment_id": assessment_id}


def export_job(store, job_id, target, format, *, cancel=None, progress=None):
    if format not in ("csv", "xlsx", "pdf"):
        raise ValueError("Toplu biçim CSV, XLSX veya tekil PDF arşivi olmalı")
    validate_target(store, target)
    payload = job_report(store, job_id, cancel=cancel, progress=progress)

    def write(path):
        if format != "pdf":
            (write_csv if format == "csv" else write_xlsx)(
                path, batch_tables(payload), cancel, progress
            )
            return
        # Sadece sayısal kayıt kimliği: katılımcı girdisi bir dosya yolu olamaz.
        with (
            zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive,
            tempfile.TemporaryDirectory(prefix="lai-pdfs-") as directory,
        ):
            for n, (aid, report) in enumerate(payload["reports"].items(), 1):
                check_cancel(cancel)
                pdf = Path(directory) / f"degerlendirme-{aid}.pdf"
                write_pdf(pdf, report, cancel)
                archive.write(pdf, pdf.name)
                pdf.unlink()
                if progress:
                    progress(
                        {
                            "phase": "writing",
                            "done": n,
                            "total": len(payload["reports"]),
                        }
                    )
            summary = Path(directory) / "is-ozeti.csv"
            write_csv(summary, batch_tables(payload), cancel)
            archive.write(summary, summary.name)

    output = atomic_write(target, write, cancel=cancel)
    return {
        "path": output,
        "format": format,
        "job_id": job_id,
        "counts": payload["job"]["counts"],
        "pdf_count": len(payload["reports"]) if format == "pdf" else 0,
    }
