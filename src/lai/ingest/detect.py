"""Dosya türü tespiti (uzantı + içerik imzası) ve akışlı sha256."""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from .model import IngestError

SUPPORTED = ("pdf", "docx", "xlsx", "csv", "json")
MAX_BYTES = 200 * 1024 * 1024
ZIP_EXPANSION = 2  # açılmış arşiv en çok MAX_BYTES × 2
_LEGACY = {".doc": "Word 97-2003 (.doc)", ".xls": "Excel 97-2003 (.xls)"}
_NAMES = {"pdf": "PDF", "docx": "Word (DOCX)", "xlsx": "Excel (XLSX)", "csv": "CSV", "json": "JSON"}


def sha256_file(path: Path, chunk: int = 1 << 20) -> tuple[str, int]:
    h, size = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
            size += len(block)
    return h.hexdigest(), size


def _sniff(path: Path, head: bytes) -> str | None:
    """İçeriğe göre tür; anlaşılamazsa None."""
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile:
            return "zip?"
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        return "zip?"
    try:
        text = head.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        try:  # metin olabilir ama UTF-8 değil (kodlama denetimi okuyucuda)
            text = head.decode("cp1254")
        except UnicodeDecodeError:
            return None
    if any(ord(c) < 9 or 13 < ord(c) < 32 for c in text):
        return None
    s = text.lstrip()
    return "json" if s[:1] in "{[" else "text"


def detect_kind(path: Path, *, declared: str | None = None, max_bytes: int = MAX_BYTES) -> str:
    path = Path(path)
    name = path.name
    ext = path.suffix.lower()
    if ext in _LEGACY:
        raise IngestError("legacy_format",
                          f"{name}: eski {_LEGACY[ext]} biçimi desteklenmiyor. Dosyayı "
                          f"{'DOCX' if ext == '.doc' else 'XLSX'} olarak kaydedip yeniden deneyin.")
    kind = declared or ext.lstrip(".")
    if kind not in SUPPORTED:
        raise IngestError("unsupported_format",
                          f"{name}: '{ext or '(uzantısız)'}' desteklenmiyor. Desteklenen biçimler: "
                          + ", ".join(_NAMES[k] for k in SUPPORTED) + ".")
    size = path.stat().st_size
    if size == 0:
        raise IngestError("empty_file", f"{name}: dosya boş (0 bayt).")
    if size > max_bytes:
        raise IngestError("file_too_large",
                          f"{name}: dosya çok büyük ({size / 1048576:.1f} MB); sınır "
                          f"{max_bytes / 1048576:.0f} MB.")
    with open(path, "rb") as f:
        head = f.read(4096)
    sniffed = _sniff(path, head)
    if sniffed in ("docx", "xlsx"):
        # Sıkıştırılmış arşiv açıldığında belleği tüketmesin (zip bombası)
        with zipfile.ZipFile(path) as z:
            unpacked = sum(i.file_size for i in z.infolist())
        if unpacked > max_bytes * ZIP_EXPANSION:
            raise IngestError("archive_too_large",
                              f"{name}: arşiv açıldığında {unpacked / 1048576:.0f} MB olur; sınır "
                              f"{max_bytes * ZIP_EXPANSION / 1048576:.0f} MB. Dosya bozuk ya da "
                              f"kötü amaçlı olabilir.")
    ok = (sniffed == kind or (kind == "csv" and sniffed in ("text", "json"))
          or (kind == "json" and sniffed == "json"))
    if not ok:
        what = {"pdf": "PDF", "docx": "Word (DOCX)", "xlsx": "Excel (XLSX)", "json": "JSON",
                "text": "düz metin", "zip?": "tanınmayan ZIP arşivi", None: "ikili (binary) veri"}
        raise IngestError("kind_mismatch",
                          f"{name}: uzantı {_NAMES[kind]} diyor ama içerik "
                          f"{what.get(sniffed, sniffed)} görünüyor. Dosya bozuk ya da yanlış "
                          f"uzantılı olabilir.")
    return kind
