import re

from app.core.config import AppError
from app.schemas.writing import char_count, visible_heading
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from rapidfuzz.fuzz import ratio


def modification_ratio(original, current):
    return round(1 - ratio(original, current) / 100, 6)


def total_words(report, rows):
    return char_count(report.title) + sum(
        char_count(visible_heading(ch.chapter_id, ch.title)) + char_count(ch.body) for ch in rows
    )


def export_docx(report, rows, path):
    if not rows or any(ch.phase != "done" or not ch.body for ch in rows):
        raise AppError("REPORT_NOT_READY", "全文尚未生成完成，暂不能导出", 409)
    if total_words(report, rows) >= 20000:
        raise AppError("WORD_BUDGET_EXCEEDED", "可见全文须小于20000字", 409)
    doc = Document()
    # python-docx's base template can carry a decorative Title border.
    for style in doc.styles:
        for border in list(style.element.iter(qn("w:pBdr"))):
            border.getparent().remove(border)
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(0.85)
    section.left_margin = section.right_margin = Inches(0.9)
    for name, size in [("Normal", 11), ("Title", 22), ("Heading 1", 15)]:
        style = doc.styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Songti SC")
    doc.styles["Normal"].paragraph_format.line_spacing = 1.25
    doc.styles["Normal"].paragraph_format.space_after = Pt(7)
    doc.add_paragraph(report.title, "Title")
    for ch in rows:
        doc.add_heading(visible_heading(ch.chapter_id, ch.title), level=1)
        for text in ch.body.splitlines():
            if not text.strip():
                continue
            paragraph = doc.add_paragraph()
            for part in re.split(r"(\[待补充[^\]\n]*\])", text):
                run = paragraph.add_run(part)
                if part.startswith("[待补充"):
                    run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    # Plain page numbering, without introducing extra report text.
    paragraph = section.footer.paragraphs[0]
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    paragraph._p.append(field)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path
