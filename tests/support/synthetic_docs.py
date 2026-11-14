"""Sentetik (gerçek olmayan) envanter belgeleri: PDF, DOCX, XLSX, CSV, JSON.

Metinler bilinçli olarak Türkçe karakterlerle doludur (ı İ ş Ş ğ Ğ ü Ü ö Ö ç Ç) ve her
madde birbirinden belirgin biçimde farklıdır. İçerik tohumla (seed) tekrarlanabilir.
"""
from __future__ import annotations

import csv
import html
import json
import random
from pathlib import Path

LIKERT = [("A", "Kesinlikle katılıyorum"), ("B", "Katılıyorum"), ("C", "Kararsızım"),
          ("D", "Katılmıyorum"), ("E", "Kesinlikle katılmıyorum")]

_WHEN = ["Işıklı ve kalabalık ortamlarda", "İş yerinde yeni fikirler üretirken",
         "Çalışma planımı önceden hazırlarken", "Öğretmenimin öğütlerini dinlerken",
         "Üzgün bir arkadaşımla konuşurken", "Şehir dışına yolculuk düzenlerken",
         "Ağır bir sınav haftasında", "Değişiklik içeren projelerde",
         "Ilık bir yaz akşamında", "Güçlü eleştiriler aldığımda", "Sıradışı sorunlarla uğraşırken",
         "Gündelik işlerimi sıraya koyarken", "Yeni insanlarla tanışırken",
         "Önemli kararların arifesinde", "Takım çalışmasının ortasında",
         "Uzun bir toplantıdan sonra", "Hafta sonu boş vakit bulduğumda",
         "Çocukluk anılarımı düşünürken", "Beklenmedik haberler geldiğinde",
         "Sınırlı süre içinde çalışırken"]
_WHO = ["çoğunlukla", "genellikle", "bazen", "nadiren", "kimi zaman", "çoğu kez", "sıklıkla",
        "zaman zaman", "her fırsatta", "ister istemez"]
_WHAT = ["kendimi enerjik hissederim", "ayrıntılara dikkat ederim",
         "duygularımı ön plana çıkarırım", "mantıksal çözümler ararım",
         "son dakikaya kadar beklerim", "somut verilere güvenirim",
         "olasılıkları düşlemeyi severim", "kurallara sıkı sıkıya bağlı kalırım",
         "esnek davranmayı tercih ederim", "yalnız kalıp düşünmek isterim",
         "çevremdekilerin görüşlerini sorarım", "işleri listeye dökerim",
         "içgüdülerime güvenirim", "geçmiş deneyimlerimi hatırlarım",
         "ortamı neşelendirmeye çalışırım", "uzun vadeli sonuçları tartarım",
         "sessizce gözlem yapmayı yeğlerim", "hızlıca harekete geçerim",
         "başkalarına öncülük etmek isterim", "yaratıcı yollar keşfederim",
         "adil olmaya özen gösteririm", "hedeflerimi yazıya geçiririm",
         "merakla sorular sorarım", "geleneksel yöntemleri seçerim"]
_TAIL = (" Özellikle beklenmedik değişikliklerle karşılaştığımda, çevremdeki insanların "
         "düşüncelerini de göz önünde bulundurarak ölçülü ve dengeli bir karar vermeye çalışırım.")


def make_doc_inventory(n_items: int = 400, seed: int = 7, *, forced_every: int = 0) -> dict:
    """Her madde: {number, text, options[(kod, metin)]}. `forced_every` > 0 ise o aralıktaki
    maddeler iki seçenekli (A/B) olur. Her 9. madde uzundur (satır kaydırması)."""
    rnd = random.Random(seed)
    combos = [(a, b, c) for a in _WHEN for b in _WHO for c in _WHAT]
    rnd.shuffle(combos)
    used_pairs: set[tuple] = set()
    chosen = []
    for a, b, c in combos:  # aynı (zaman, eylem) çifti iki kez kullanılmaz → belirgin farklılık
        if (a, c) in used_pairs:
            continue
        used_pairs.add((a, c))
        chosen.append((a, b, c))
        if len(chosen) == n_items:
            break
    if len(chosen) < n_items:
        raise ValueError("yeterli farklı madde üretilemedi")
    items = []
    for n, (a, b, c) in enumerate(chosen, start=1):
        text = f"{a} {b} {c}."
        if n % 9 == 0:
            text += _TAIL
        if forced_every and n % forced_every == 0:
            opts = [("A", f"{a.split()[0]} durumunda daha çok konuşurum"),
                    ("B", f"{a.split()[0]} durumunda daha çok dinlerim")]
        else:
            opts = list(LIKERT)
        items.append({"number": n, "text": text, "options": opts})
    return {"name": f"Sentetik Belge Envanteri ({n_items})", "items": items}


def answer_key_for(inv: dict, seed: int = 3) -> dict[int, str]:
    rnd = random.Random(seed)
    return {it["number"]: rnd.choice([c for c, _ in it["options"]]) for it in inv["items"]}


# ------------------------------------------------------------------------------ PDF
def write_pdf(inv: dict, path: Path, *, header: str | None = "SENTETİK KİŞİLİK ENVANTERİ",
              footer: bool = True, inline_options: bool = False, preamble: bool = True,
              answer_key: dict[int, str] | None = None, items: list[dict] | None = None,
              raw_lines: list[str] | None = None) -> Path:
    """Story ile akan çok sayfalı PDF. Başlık her sayfada, 'Sayfa i / n' altta."""
    import pymupdf
    parts = []
    if preamble:
        parts.append("<p>Yönerge: Her ifadeyi okuyup size en uygun seçeneği işaretleyiniz.</p>")
    for it in (items if items is not None else inv["items"]):
        parts.append(f"<p>{it['number']}. {html.escape(it['text'])}</p>")
        if inline_options:
            parts.append("<p>" + "&nbsp;&nbsp;&nbsp;".join(
                f"{c}) {html.escape(t)}" for c, t in it["options"]) + "</p>")
        else:
            parts.extend(f"<p>{c}) {html.escape(t)}</p>" for c, t in it["options"])
    for line in raw_lines or []:
        parts.append(f"<p>{html.escape(line)}</p>")
    if answer_key:
        parts.append("<p>CEVAP ANAHTARI</p>")
        keys = sorted(answer_key.items())
        for i in range(0, len(keys), 10):
            parts.append("<p>" + "  ".join(f"{n}-{v}" for n, v in keys[i:i + 10]) + "</p>")
    css = "p { font-size: 10pt; margin: 0 0 3pt 0; }"
    story = pymupdf.Story(html="".join(parts), user_css=css)
    tmp = Path(path).with_suffix(".story.pdf")
    writer = pymupdf.DocumentWriter(str(tmp))
    page_rect = pymupdf.paper_rect("a4")
    where = page_rect + (56, 72, -56, -72)
    more = True
    while more:
        dev = writer.begin_page(page_rect)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    doc = pymupdf.open(tmp)
    total = doc.page_count
    for i, page in enumerate(doc, start=1):
        if header:
            page.insert_htmlbox(pymupdf.Rect(56, 30, 540, 60), f"<p>{html.escape(header)}</p>",
                                css="p {font-size: 9pt;}")
        if footer:
            page.insert_htmlbox(pymupdf.Rect(56, 790, 540, 820), f"<p>Sayfa {i} / {total}</p>",
                                css="p {font-size: 9pt; text-align: center;}")
    doc.save(path)
    doc.close()
    tmp.unlink()
    return Path(path)


# ------------------------------------------------------------------------------ DOCX
def write_docx(inv: dict, path: Path, *, auto_numbering: bool = False, as_table: bool = False,
               answer_key: dict[int, str] | None = None) -> Path:
    import docx
    d = docx.Document()
    d.add_paragraph("SENTETİK KİŞİLİK ENVANTERİ")
    d.add_paragraph("Yönerge: Her ifadeyi okuyup size en uygun seçeneği işaretleyiniz.")
    if as_table:
        codes = [c for c, _ in inv["items"][0]["options"]]
        t = d.add_table(rows=1, cols=2 + len(codes))
        for cell, h in zip(t.rows[0].cells, ["Soru No", "Soru Metni", *codes], strict=True):
            cell.text = h
        for it in inv["items"]:
            row = t.add_row().cells
            row[0].text = str(it["number"])
            row[1].text = it["text"]
            for j, (_, txt) in enumerate(it["options"]):
                row[2 + j].text = txt
    else:
        for it in inv["items"]:
            if auto_numbering:
                d.add_paragraph(it["text"], style="List Number")
            else:
                d.add_paragraph(f"{it['number']}. {it['text']}")
            for c, t in it["options"]:
                d.add_paragraph(f"{c}) {t}")
    if answer_key:
        d.add_paragraph("Cevap Anahtarı")
        keys = sorted(answer_key.items())
        for i in range(0, len(keys), 10):
            d.add_paragraph("  ".join(f"{n}-{v}" for n, v in keys[i:i + 10]))
    d.save(path)
    return Path(path)


# ------------------------------------------------------------------------------ tablolar
def _rows(inv: dict, *, key: dict[int, str] | None):
    codes = [c for c, _ in inv["items"][0]["options"]]
    header = ["Soru No", "Soru Metni", *(f"Seçenek {c}" for c in codes)]
    if key:
        header.append("Cevap Anahtarı")
    rows = [header]
    for it in inv["items"]:
        r = [it["number"], it["text"], *(t for _, t in it["options"])]
        r += [""] * (len(codes) - len(it["options"]))
        if key:
            r.append(key.get(it["number"], ""))
        rows.append(r)
    return rows


def write_xlsx(inv: dict, path: Path, *, answer_key: dict[int, str] | None = None,
               extra_sheet_first: bool = False) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    if extra_sheet_first:
        ws.title = "Yönerge"
        ws.append(["Bu sayfa yalnız açıklama içerir."])
        ws = wb.create_sheet("Sorular")
    else:
        ws.title = "Sorular"
    for r in _rows(inv, key=answer_key):
        ws.append(r)
    wb.save(path)
    return Path(path)


def write_csv(inv: dict, path: Path, *, delimiter: str = ";", encoding: str = "utf-8-sig",
              answer_key: dict[int, str] | None = None) -> Path:
    with open(path, "w", encoding=encoding, newline="") as f:
        w = csv.writer(f, delimiter=delimiter)
        for r in _rows(inv, key=answer_key):
            w.writerow(r)
    return Path(path)


def write_json(inv: dict, path: Path, *, answer_key: dict[int, str] | None = None) -> Path:
    doc = {"format": "lai-inventory", "version": 1, "name": inv["name"],
           "items": [{"number": it["number"], "text": it["text"],
                      "options": [{"code": c, "text": t} for c, t in it["options"]]}
                     for it in inv["items"]]}
    if answer_key:
        doc["answer_key"] = [{"item": n, "value": v} for n, v in sorted(answer_key.items())]
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return Path(path)
