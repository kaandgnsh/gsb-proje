"""Türkçe font gömülü, satır ve sayfa bölebilen yerel PDF."""

from html import escape
from pathlib import Path
from threading import Lock

from .service import check_cancel, dumps, single_tables

_FONT_LOCK = Lock()


def render(path, report, cancel):
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    with _FONT_LOCK:
        if "LAIDejaVu" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(
                TTFont(
                    "LAIDejaVu", str(Path(__file__).parent / "fonts" / "DejaVuSans.ttf")
                )
            )
    style = ParagraphStyle(
        "Türkçe",
        fontName="LAIDejaVu",
        fontSize=9,
        leading=14,
        alignment=TA_LEFT,
        splitLongWords=True,
        spaceAfter=7,
    )
    title = ParagraphStyle(
        "Başlık", parent=style, fontSize=15, leading=21, spaceBefore=12
    )
    story = []

    def paragraph(value, heading=False):
        check_cancel(cancel)
        text = value if isinstance(value, str) else dumps(value)
        # Küçük akış öğeleri: tek çok uzun kural/alıntı bile sayfalara ayrılabilir.
        for start in range(0, max(1, len(text)), 1600):
            story.append(
                Paragraph(
                    escape(text[start : start + 1600]).replace("\n", "<br/>"),
                    title if heading else style,
                )
            )

    paragraph("Yerel değerlendirme raporu", True)
    paragraph(report["notice"])
    for name, table in single_tables(report):
        paragraph(name, True)
        headers = table[0]
        for row in table[1:]:
            for header, value in zip(headers, row):
                paragraph(
                    f"{header}: "
                    + (
                        "Hesaplanmadı / yok"
                        if value is None
                        else value
                        if isinstance(value, str)
                        else dumps(value)
                    )
                )
            story.append(Spacer(1, 6))
    a = report["assessment"]

    def page(canvas, doc):
        check_cancel(cancel)
        canvas.saveState()
        canvas.setFont("LAIDejaVu", 8)
        canvas.drawString(
            36,
            23,
            f"Kayıt #{a['id']} | Profil sürümü #{a['profile_version_id']} | {a['status']} | Sayfa {doc.page}",
        )
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=42,
        title="Yerel değerlendirme raporu",
        author="LAI",
    )
    doc.build(story, onFirstPage=page, onLaterPages=page)
