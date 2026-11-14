"""İçe aktarma raporu: Türkçe metin ve JSON."""
from __future__ import annotations

from .model import ERROR, INFO, WARNING

_STATUS = {"imported": "İÇE AKTARILDI", "needs_review": "İNCELEME GEREKİYOR",
           "failed": "BAŞARISIZ", "duplicate": "ZATEN İÇE AKTARILMIŞ"}
_KIND = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel", "csv": "CSV", "json": "JSON"}
_TITLE = {ERROR: ("Hatalar", "✗"), WARNING: ("Uyarılar", "!"), INFO: ("Bilgi", "·")}
LIST_LIMIT = 50


def _issue_line(i) -> str:
    where = f" — {i.locator.describe()}" if i.locator is not None else ""
    return f"  {_TITLE[i.severity][1]} [{i.code}] {i.message}{where}"


def format_report(rep, *, limit: int = LIST_LIMIT) -> str:
    lines = [f"İçe aktarma: {rep.file_name} ({_KIND.get(rep.kind, rep.kind)}, sha256 "
             f"{rep.sha256[:12]}…)"]
    status = _STATUS[rep.status]
    if rep.status == "imported":
        status += f" — envanter sürümü v{rep.inventory_version}"
        if rep.same_content:
            status += " (mevcut sürüm, içerik aynı)"
    elif rep.status == "needs_review":
        status += " — envanter sürümü oluşturulmadı"
    lines.append(f"Durum: {status}")
    if rep.message:
        lines.append(rep.message)
    if rep.status in ("imported", "needs_review"):
        lines.append(f"Madde: {rep.item_count} · Seçenek: {rep.option_count} · "
                     f"Cevap anahtarı: {rep.answer_key_count}")
    for sev in (ERROR, WARNING, INFO):
        group = [i for i in rep.issues if i.severity == sev]
        if not group:
            continue
        lines.append(f"{_TITLE[sev][0]} ({len(group)}):")
        lines += [_issue_line(i) for i in group[:limit]]
        if len(group) > limit:
            lines.append(f"  … ve {len(group) - limit} tane daha (tam liste veritabanında, "
                         f"çalıştırma #{rep.run_id}).")
    return "\n".join(lines)


def report_dict(rep) -> dict:
    return {
        "status": rep.status, "run_id": rep.run_id, "file_name": rep.file_name,
        "sha256": rep.sha256, "kind": rep.kind, "document_id": rep.document_id,
        "inventory_version_id": rep.inventory_version_id,
        "inventory_version": rep.inventory_version, "same_content": rep.same_content,
        "duplicate_of_run_id": rep.duplicate_of_run_id, "message": rep.message,
        "counts": {"items": rep.item_count, "options": rep.option_count,
                   "answer_keys": rep.answer_key_count, "errors": len(rep.errors),
                   "warnings": len(rep.warnings), "info": len(rep.infos)},
        "issues": [{"severity": i.severity, "code": i.code, "message": i.message,
                    "location": i.locator.describe() if i.locator else None,
                    "locator": i.locator.to_dict() if i.locator else None,
                    "item_number": i.item_number} for i in rep.issues],
    }
