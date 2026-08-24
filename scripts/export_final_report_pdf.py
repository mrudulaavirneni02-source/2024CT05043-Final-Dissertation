from pathlib import Path

from docx import Document
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import portrait
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.platypus import BaseDocTemplate, Frame, Image as RLImage, PageBreak, Paragraph as RLParagraph, Table, TableStyle


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "output" / "2024CT05043_Final_Dissertation_Report_Updated.docx"
TARGET = ROOT / "output" / "2024CT05043_Final_Dissertation_Report_Updated.pdf"
WORKFLOW_FIGURE = ROOT / "output" / "logical_workflow.png"
REPORT_PAGE = portrait((9 * inch, 11 * inch))
PRELIMINARY_PAGES = 6


class NumberedCanvas:
    """Simple canvas wrapper that adds footer page numbers after all pages exist."""
    def __init__(self, *args, **kwargs):
        from reportlab.pdfgen.canvas import Canvas
        self._canvas = Canvas(*args, **kwargs)
        self._saved = []

    def __getattr__(self, name):
        return getattr(self._canvas, name)

    def showPage(self):
        self._saved.append(dict(self._canvas.__dict__))
        self._canvas._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self._canvas.__dict__.update(state)
            self._canvas.setFont("Times-Roman", 9)
            physical_page = self._canvas.getPageNumber()
            if physical_page == 1:
                footer = ""
            elif physical_page <= PRELIMINARY_PAGES:
                footer = _roman(physical_page - 1).lower()
            else:
                footer = str(physical_page - PRELIMINARY_PAGES)
            if footer:
                self._canvas.drawCentredString(REPORT_PAGE[0] / 2, 0.48 * inch, footer)
            self._canvas.showPage()
        self._canvas.save()


def _roman(value):
    values = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    result = []
    for amount, glyph in values:
        while value >= amount:
            result.append(glyph)
            value -= amount
    return "".join(result)


def ordered_blocks(document):
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield DocxTable(child, document)


def has_page_break(paragraph):
    return 'w:type="page"' in paragraph._p.xml or "w:type=\"page\"" in paragraph._p.xml


def has_drawing(paragraph):
    return "<w:drawing" in paragraph._p.xml


def alignment_of(paragraph):
    if paragraph.alignment == 1:
        return TA_CENTER
    if paragraph.alignment == 3:
        return TA_JUSTIFY
    return TA_LEFT


def build():
    document = Document(SOURCE)
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontName="Times-Roman", fontSize=12,
        leading=24, spaceAfter=6, alignment=TA_JUSTIFY,
    )
    center_body = ParagraphStyle("CenterBody", parent=body, alignment=TA_CENTER)
    h1 = ParagraphStyle(
        "H1", parent=styles["Heading1"], fontName="Times-Bold", fontSize=16,
        leading=19, spaceBefore=16, spaceAfter=8, keepWithNext=True,
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontName="Times-Bold", fontSize=14,
        leading=17, spaceBefore=12, spaceAfter=6, keepWithNext=True,
    )
    h3 = ParagraphStyle(
        "H3", parent=styles["Heading3"], fontName="Times-Bold", fontSize=12,
        leading=15, spaceBefore=8, spaceAfter=4, keepWithNext=True,
    )
    bullet = ParagraphStyle("Bullet", parent=body, leftIndent=0.32 * inch, firstLineIndent=-0.18 * inch, spaceAfter=3)
    table_text = ParagraphStyle("TableText", parent=body, fontSize=8.5, leading=11, alignment=TA_LEFT, spaceAfter=0)
    table_head = ParagraphStyle("TableHead", parent=table_text, fontName="Times-Bold", alignment=TA_LEFT)

    flow = []
    for block in ordered_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            style_name = block.style.name
            if has_page_break(block):
                if text:
                    flow.append(RLParagraph(text, center_body if alignment_of(block) == TA_CENTER else body))
                flow.append(PageBreak())
                continue
            if has_drawing(block):
                flow.append(RLImage(str(WORKFLOW_FIGURE), width=6.8 * inch, height=3.04 * inch, hAlign="CENTER"))
                continue
            if not text:
                continue
            if style_name == "Heading 1":
                style = h1
            elif style_name == "Heading 2":
                style = h2
            elif style_name == "Heading 3":
                style = h3
            elif style_name.startswith("List Bullet"):
                style = bullet
                text = "&#8226; " + text
            else:
                style = center_body if alignment_of(block) == TA_CENTER else body
                if len(text) < 55 and alignment_of(block) == TA_CENTER:
                    style = ParagraphStyle("CenterSmall", parent=center_body, fontSize=12, leading=18, spaceAfter=6)
            if style_name.startswith("List Bullet"):
                safe_text = text.replace("<", "&lt;").replace(">", "&gt;")
            else:
                safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            flow.append(RLParagraph(safe_text, style))
        else:
            data = []
            for row_index, row in enumerate(block.rows):
                cells = []
                for cell in row.cells:
                    value = "<br/>".join(p.text.strip() for p in cell.paragraphs if p.text.strip()) or " "
                    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    cells.append(RLParagraph(value, table_head if row_index == 0 else table_text))
                data.append(cells)
            ncols = len(data[0]) if data else 1
            col_widths = [7 * inch / ncols] * ncols
            table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#E8EEF5")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            flow.append(table)

    frame = Frame(1 * inch, 0.75 * inch, 7 * inch, 9.25 * inch, leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
    pdf = BaseDocTemplate(
        str(TARGET), pagesize=REPORT_PAGE, leftMargin=1 * inch, rightMargin=1 * inch,
        topMargin=1 * inch, bottomMargin=0.75 * inch,
    )
    from reportlab.platypus import PageTemplate
    pdf.addPageTemplates([PageTemplate(id="report", frames=[frame])])
    pdf.build(flow, canvasmaker=NumberedCanvas)
    print(TARGET)


if __name__ == "__main__":
    build()
