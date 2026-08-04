from __future__ import annotations

from io import BytesIO
import re

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


INK = RGBColor(23, 36, 58)
ACCENT = RGBColor(8, 117, 101)
MUTED = RGBColor(95, 107, 122)


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("_") or "application_document"


def _ascii_punctuation(text: str) -> str:
    return text.translate(str.maketrans({
        "–": "-", "—": "-", "‑": "-", "“": '"', "”": '"',
        "‘": "'", "’": "'", "…": "...", "•": "-",
    }))


def _set_font(run, size: float = 11, bold: bool = False, color: RGBColor = INK) -> None:
    run.font.name = "Calibri"
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Calibri")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Calibri")
    run.font.size = Pt(size)
    run.bold = bold
    run.font.color.rgb = color


def _add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run()
    _set_font(run, size=9, color=MUTED)
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText"); instruction.set(qn("xml:space"), "preserve"); instruction.text = " PAGE "
    end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, end])


def _configure_docx(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = section.right_margin = section.bottom_margin = section.left_margin = Inches(1)
    section.header_distance = section.footer_distance = Inches(0.492)
    _add_page_number(section.footer.paragraphs[0])

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"; normal.font.size = Pt(11); normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(6); normal.paragraph_format.line_spacing = 1.10
    for style_name, size, color, before, after in (
        ("Heading 1", 16, ACCENT, 16, 8),
        ("Heading 2", 13, ACCENT, 12, 6),
        ("Heading 3", 12, INK, 8, 4),
    ):
        style = document.styles[style_name]
        style.font.name = "Calibri"; style.font.size = Pt(size); style.font.bold = True; style.font.color.rgb = color
        style.paragraph_format.space_before = Pt(before); style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True


def _add_inline_markdown(paragraph, text: str) -> None:
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if not part:
            continue
        bold = part.startswith("**") and part.endswith("**")
        run = paragraph.add_run(part[2:-2] if bold else part)
        _set_font(run, bold=bold)


def create_docx(content: str, title: str) -> bytes:
    document = Document()
    _configure_docx(document)
    lines = _ascii_punctuation(content).splitlines()

    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            document.add_paragraph()
            continue
        if line.startswith("### "):
            document.add_paragraph(line[4:], style="Heading 3")
        elif line.startswith("## "):
            document.add_paragraph(line[3:], style="Heading 2")
        elif line.startswith("# "):
            document.add_paragraph(line[2:], style="Heading 1")
        elif line.startswith("- "):
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.paragraph_format.left_indent = Inches(0.5)
            paragraph.paragraph_format.first_line_indent = Inches(-0.25)
            paragraph.paragraph_format.space_after = Pt(4)
            _add_inline_markdown(paragraph, line[2:])
        elif re.match(r"^\d+\.\s", line):
            paragraph = document.add_paragraph(style="List Number")
            paragraph.paragraph_format.left_indent = Inches(0.5)
            paragraph.paragraph_format.first_line_indent = Inches(-0.25)
            paragraph.paragraph_format.space_after = Pt(4)
            _add_inline_markdown(paragraph, re.sub(r"^\d+\.\s*", "", line))
        elif line.startswith("**") and line.endswith("**") and len(line) < 100:
            paragraph = document.add_paragraph(style="Heading 2")
            paragraph.add_run(line[2:-2])
        elif index == 0 and len(line) < 100:
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_after = Pt(5)
            run = paragraph.add_run(line.replace("**", "")); _set_font(run, size=19, bold=True, color=ACCENT)
        elif index == 1 and len(line) < 140:
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_after = Pt(14)
            run = paragraph.add_run(line.replace("**", "")); _set_font(run, size=10, color=MUTED)
        else:
            paragraph = document.add_paragraph()
            _add_inline_markdown(paragraph, line)

    stream = BytesIO(); document.save(stream)
    return stream.getvalue()


def create_pdf(content: str, title: str) -> bytes:
    stream = BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ApplicationBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=10.5, leading=14, textColor="#17243A", spaceAfter=7)
    heading = ParagraphStyle("ApplicationHeading", parent=body, fontName="Helvetica-Bold", fontSize=13, leading=16, textColor="#087565", spaceBefore=10, spaceAfter=6, keepWithNext=True)
    title_style = ParagraphStyle("ApplicationTitle", parent=heading, fontSize=18, leading=22, alignment=TA_CENTER, spaceAfter=10)
    bullet = ParagraphStyle("ApplicationBullet", parent=body, leftIndent=18, firstLineIndent=-10, bulletIndent=4, spaceAfter=4)
    story = []
    for index, raw in enumerate(_ascii_punctuation(content).splitlines()):
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 5)); continue
        escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        escaped = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", escaped)
        if line.startswith("#"):
            escaped = re.sub(r"^#+\s*", "", escaped)
            story.append(Paragraph(escaped, heading))
        elif line.startswith("- "):
            story.append(Paragraph(escaped[2:], bullet, bulletText="-"))
        elif index == 0 and len(line) < 100:
            story.append(Paragraph(escaped.replace("**", ""), title_style))
        elif line.startswith("**") and line.endswith("**") and len(line) < 100:
            story.append(Paragraph(escaped, heading))
        else:
            story.append(Paragraph(escaped, body))

    pdf = SimpleDocTemplate(stream, pagesize=letter, rightMargin=inch, leftMargin=inch, topMargin=inch, bottomMargin=inch, title=title, author="")
    pdf.build(story)
    return stream.getvalue()
