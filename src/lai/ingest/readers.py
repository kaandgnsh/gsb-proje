"""Okuyucular: belgeyi ham metin birimlerine (`RawUnit`) çevirir. Yorum yapmaz.

- PDF: her metin satırı bir birim; sayfa, sayfa içi karakter aralığı ve bbox saklanır.
- DOCX: gövde sırasıyla paragraflar ve tablo satırları; otomatik numaralar `numbering.xml`'den
  deterministik hesaplanır.
- XLSX/CSV: boş olmayan her satır bir birim; hücreler `cells` içinde.
- JSON: dosyanın tamamı tek birim; ayrıştırılmış nesne `data` içinde.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .model import ERROR, INFO, Issue, IngestError, Locator, RawUnit


@dataclass
class ReadOutput:
    units: list[RawUnit] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    data: object = None


def _cells_text(cells) -> str:
    return json.dumps(list(cells), ensure_ascii=False)


# ---------------------------------------------------------------------------------- PDF
def read_pdf(path: Path) -> ReadOutput:
    import pymupdf
    name = Path(path).name
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001 — MuPDF çok çeşitli hata türleri üretir
        raise IngestError("corrupt_file", f"{name}: PDF açılamadı ({e}). Dosya bozuk olabilir.") from e
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise IngestError("encrypted_pdf", f"{name}: PDF parola korumalı; parolasız bir "
                                               f"kopyasını içe aktarın.")
        if doc.page_count == 0:
            raise IngestError("corrupt_file", f"{name}: PDF'te sayfa yok; dosya bozuk olabilir.")
        out = ReadOutput(meta={"page_count": doc.page_count})
        # Bağlı harfler (ﬁ, ﬂ) bileşenlerine açılır: belge "fi" yazıyor, glif tek.
        flags = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES
                 & ~pymupdf.TEXT_PRESERVE_IMAGES)
        for pno in range(doc.page_count):
            page_no = pno + 1
            try:
                page = doc[pno]
                blocks = page.get_text("dict", flags=flags)["blocks"]
            except Exception as e:  # noqa: BLE001
                out.issues.append(Issue(ERROR, "page_read_error",
                                        f"Sayfa {page_no} okunamadı: {e}",
                                        Locator(page=page_no)))
                continue
            offset, line_no, n_before = 0, 0, len(out.units)
            for b in blocks:
                for ln in b.get("lines", []):
                    text = "".join(s["text"] for s in ln["spans"])
                    if not text.strip():
                        continue
                    line_no += 1
                    loc = Locator(page=page_no, line=line_no, char_start=offset,
                                  char_end=offset + len(text), bbox=tuple(ln["bbox"]))
                    out.units.append(RawUnit(len(out.units), text, loc))
                    offset += len(text) + 1  # sayfa metni satırların "\n" ile birleşimidir
            if len(out.units) == n_before:
                if page.get_images():
                    out.issues.append(Issue(ERROR, "no_text_layer",
                                            f"Sayfa {page_no}'de metin yok, yalnız görüntü var "
                                            f"(taranmış sayfa olabilir). OCR desteklenmiyor; bu "
                                            f"sayfadaki sorular okunamadı.", Locator(page=page_no)))
                else:
                    out.issues.append(Issue(INFO, "blank_page", f"Sayfa {page_no} boş.",
                                            Locator(page=page_no)))
        return out


# ---------------------------------------------------------------------------------- DOCX
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _roman(n: int) -> str:
    vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
            (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = ""
    for v, s in vals:
        while n >= v:
            out, n = out + s, n - v
    return out


def _letters(n: int) -> str:  # Word: a..z, aa..zz, …
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def _fmt(n: int, fmt: str) -> str | None:
    if fmt in ("decimal", "decimalZero"):
        return f"{n:02d}" if fmt == "decimalZero" and n < 10 else str(n)
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    return None


class _Numbering:
    """Word otomatik numaralandırmasını hesaplar (desteklenmeyen biçimde None döner)."""

    def __init__(self, document):
        self.levels: dict[str, dict[int, dict]] = {}
        self.counters: dict[str, dict[int, int]] = {}
        self.style_num: dict[str, tuple[str, int]] = {}
        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            root = None
        abstract: dict[str, dict[int, dict]] = {}
        if root is not None:
            for an in root.iter(f"{_W}abstractNum"):
                lv = {}
                for lvl in an.iter(f"{_W}lvl"):
                    ilvl = int(lvl.get(f"{_W}ilvl"))
                    get = lambda tag, lvl=lvl: (lvl.find(f"{_W}{tag}").get(f"{_W}val")  # noqa: E731
                                                if lvl.find(f"{_W}{tag}") is not None else None)
                    lv[ilvl] = {"start": int(get("start") or 1), "fmt": get("numFmt") or "decimal",
                                "text": get("lvlText") or f"%{ilvl + 1}."}
                abstract[an.get(f"{_W}abstractNumId")] = lv
            for num in root.iter(f"{_W}num"):
                aid = num.find(f"{_W}abstractNumId").get(f"{_W}val")
                lv = {k: dict(v) for k, v in abstract.get(aid, {}).items()}
                for ov in num.iter(f"{_W}lvlOverride"):
                    so = ov.find(f"{_W}startOverride")
                    if so is not None and int(ov.get(f"{_W}ilvl")) in lv:
                        lv[int(ov.get(f"{_W}ilvl"))]["start"] = int(so.get(f"{_W}val"))
                self.levels[num.get(f"{_W}numId")] = lv
        for st in document.styles:
            el = st.element.find(f"{_W}pPr/{_W}numPr")
            if el is not None and el.find(f"{_W}numId") is not None:
                il = el.find(f"{_W}ilvl")
                self.style_num[st.style_id] = (el.find(f"{_W}numId").get(f"{_W}val"),
                                               int(il.get(f"{_W}val")) if il is not None else 0)

    def label(self, p_el, style_id: str | None) -> str | None:
        num = p_el.find(f"{_W}pPr/{_W}numPr")
        num_id, ilvl = None, 0
        if num is not None and num.find(f"{_W}numId") is not None:
            num_id = num.find(f"{_W}numId").get(f"{_W}val")
            il = num.find(f"{_W}ilvl")
            ilvl = int(il.get(f"{_W}val")) if il is not None else 0
        elif style_id in self.style_num:
            num_id, ilvl = self.style_num[style_id]
        if num_id is None or num_id == "0" or num_id not in self.levels:
            return None
        lv = self.levels[num_id]
        if ilvl not in lv:
            return None
        cnt = self.counters.setdefault(num_id, {})
        cnt[ilvl] = cnt.get(ilvl, lv[ilvl]["start"] - 1) + 1
        for deeper in [k for k in cnt if k > ilvl]:
            del cnt[deeper]
        text = lv[ilvl]["text"]
        for lvl_i in range(ilvl, -1, -1):
            token = f"%{lvl_i + 1}"
            if token in text:
                n = cnt.get(lvl_i, lv.get(lvl_i, {"start": 1})["start"])
                s = _fmt(n, lv.get(lvl_i, {"fmt": "decimal"})["fmt"])
                if s is None:
                    return None
                text = text.replace(token, s)
        return text


def read_docx(path: Path) -> ReadOutput:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    name = Path(path).name
    try:
        document = docx.Document(str(path))
    except Exception as e:  # noqa: BLE001 — zip/xml/anahtar hataları
        raise IngestError("corrupt_file", f"{name}: Word belgesi açılamadı ({e.__class__.__name__}). "
                                          f"Dosya bozuk olabilir.") from e
    out = ReadOutput()
    numbering = _Numbering(document)
    auto, para_no, table_no = 0, 0, 0
    for child in document.element.body.iterchildren():
        if child.tag == f"{_W}p":
            para_no += 1
            p = Paragraph(child, document)
            text = p.text
            if not text.strip():
                continue
            ps = child.find(f"{_W}pPr/{_W}pStyle")  # stil nesnesini çözmek paragraf başına yavaş
            label = numbering.label(child, ps.get(f"{_W}val") if ps is not None else None)
            if label:
                auto += 1
                text = f"{label} {text}"
            out.units.append(RawUnit(len(out.units), text,
                                     Locator(paragraph=para_no, char_start=0, char_end=len(text))))
        elif child.tag == f"{_W}tbl":
            table_no += 1
            t = Table(child, document)
            for r_i, row in enumerate(t.rows, start=1):
                cells = tuple(c.text for c in row.cells)
                if not any(c.strip() for c in cells):
                    continue
                out.units.append(RawUnit(len(out.units), _cells_text(cells),
                                         Locator(sheet=f"Tablo {table_no}", row=r_i), cells))
    out.meta = {"paragraphs": para_no, "tables": table_no, "auto_numbered": auto}
    if auto:
        out.issues.append(Issue(INFO, "auto_numbering",
                                f"{auto} paragrafın numarası Word otomatik numaralandırmasından "
                                f"hesaplandı (numaralar belgede düz metin olarak yok)."))
    return out


# ---------------------------------------------------------------------------------- XLSX
def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "DOĞRU" if v else "YANLIŞ"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def read_xlsx(path: Path) -> ReadOutput:
    import openpyxl
    name = Path(path).name
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise IngestError("corrupt_file", f"{name}: Excel dosyası açılamadı "
                                          f"({e.__class__.__name__}). Dosya bozuk olabilir.") from e
    out = ReadOutput(meta={"sheets": list(wb.sheetnames)})
    try:
        for ws in wb.worksheets:
            for r_i, row in enumerate(ws.iter_rows(values_only=True), start=1):
                cells = tuple(_cell_str(v) for v in row)
                while cells and cells[-1] == "":
                    cells = cells[:-1]
                if not any(c.strip() for c in cells):
                    continue
                out.units.append(RawUnit(len(out.units), _cells_text(cells),
                                         Locator(sheet=ws.title, row=r_i), cells))
    finally:
        wb.close()
    return out


# ---------------------------------------------------------------------------------- CSV
def read_csv(path: Path, encoding: str | None = None) -> ReadOutput:
    name = Path(path).name
    enc = encoding or "utf-8-sig"
    out = ReadOutput(meta={"encoding": enc})
    try:
        with open(path, encoding=enc, errors="strict", newline="") as f:
            sample = f.read(65536)
            first = sample.splitlines()[0] if sample else ""
            delim = max(";,\t", key=first.count) if first else ","
            if first.count(delim) == 0:
                delim = ","
            out.meta["delimiter"] = delim
            f.seek(0)
            reader = csv.reader(f, delimiter=delim)
            start = 1
            for cells in reader:
                line = start
                start = reader.line_num + 1
                cells = tuple(cells)
                if not any(c.strip() for c in cells):
                    continue
                out.units.append(RawUnit(len(out.units), _cells_text(cells), Locator(row=line),
                                         cells))
    except UnicodeDecodeError as e:
        hint = (" Türkçe Windows dosyaları genellikle 'cp1254' kodlamasıdır; içe aktarırken bu "
                "kodlamayı açıkça seçebilirsiniz." if encoding is None else "")
        raise IngestError("encoding", f"{name}: dosya {enc.replace('-sig', '')} olarak okunamadı "
                                      f"(bayt {e.start}). Karakterler tahmin edilmedi.{hint}") from e
    except (csv.Error, ValueError) as e:
        raise IngestError("corrupt_file", f"{name}: CSV okunamadı ({e}).") from e
    except LookupError as e:
        raise IngestError("encoding", f"{name}: bilinmeyen kodlama '{enc}'.") from e
    return out


# ---------------------------------------------------------------------------------- JSON
def read_json(path: Path) -> ReadOutput:
    name = Path(path).name
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise IngestError("encoding", f"{name}: JSON UTF-8 olmalı (bayt {e.start}).") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise IngestError("invalid_json", f"{name}: JSON sözdizimi hatası, satır {e.lineno} "
                                          f"sütun {e.colno}: {e.msg}.") from e
    return ReadOutput(units=[RawUnit(0, text, Locator(json_path="$"))], data=data)


READERS = {"pdf": read_pdf, "docx": read_docx, "xlsx": read_xlsx, "csv": read_csv,
           "json": read_json}


def read(path: Path, kind: str, *, encoding: str | None = None) -> ReadOutput:
    if kind == "csv":
        return read_csv(path, encoding)
    return READERS[kind](path)


