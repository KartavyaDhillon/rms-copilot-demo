"""
Generate INTERVIEW_QA.pdf and SQL_LEARNING.pdf from the .md files.
Run from the rms-copilot folder:  python make_pdfs.py
"""
import re, os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
    Table, TableStyle, PageBreak, Preformatted
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ── Colour palette (dark theme colours lightened for print) ──────────────────
TEAL   = colors.HexColor("#0d9488")
AMBER  = colors.HexColor("#b45309")
BLUE   = colors.HexColor("#1d4ed8")
INK    = colors.HexColor("#0f172a")
MUTED  = colors.HexColor("#475569")
PANEL  = colors.HexColor("#f1f5f9")
CODE_BG= colors.HexColor("#0f172a")
CODE_FG= colors.HexColor("#e2e8f0")

# ── Styles ───────────────────────────────────────────────────────────────────
BASE  = getSampleStyleSheet()

def make_styles():
    s = {}
    s["h1"] = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=22,
                              textColor=TEAL, spaceAfter=4, spaceBefore=20,
                              leading=26)
    s["h2"] = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=14,
                              textColor=BLUE, spaceAfter=4, spaceBefore=14,
                              leading=18)
    s["h3"] = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=11,
                              textColor=INK, spaceAfter=3, spaceBefore=10,
                              leading=15)
    s["body"] = ParagraphStyle("body", fontName="Helvetica", fontSize=10,
                                textColor=INK, spaceAfter=5, spaceBefore=2,
                                leading=15)
    s["q"] = ParagraphStyle("q", fontName="Helvetica-Bold", fontSize=10,
                              textColor=AMBER, spaceAfter=3, spaceBefore=8,
                              leading=14)
    s["a"] = ParagraphStyle("a", fontName="Helvetica", fontSize=10,
                              textColor=INK, spaceAfter=6, spaceBefore=2,
                              leading=15, leftIndent=12)
    s["bullet"] = ParagraphStyle("bullet", fontName="Helvetica", fontSize=10,
                                  textColor=INK, spaceAfter=2, spaceBefore=1,
                                  leading=14, leftIndent=20,
                                  bulletIndent=8)
    s["code"] = ParagraphStyle("code", fontName="Courier", fontSize=8.5,
                                textColor=CODE_FG, backColor=CODE_BG,
                                spaceAfter=6, spaceBefore=4, leading=13,
                                leftIndent=8, rightIndent=8)
    s["note"] = ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=9,
                                textColor=MUTED, spaceAfter=4, spaceBefore=2,
                                leading=13)
    s["title"] = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=28,
                                 textColor=TEAL, spaceAfter=6, spaceBefore=0,
                                 alignment=TA_CENTER, leading=34)
    s["subtitle"] = ParagraphStyle("subtitle", fontName="Helvetica", fontSize=13,
                                    textColor=MUTED, spaceAfter=0, spaceBefore=0,
                                    alignment=TA_CENTER, leading=18)
    s["updated"] = ParagraphStyle("updated", fontName="Helvetica-Oblique", fontSize=9,
                                   textColor=MUTED, spaceAfter=0,
                                   alignment=TA_CENTER)
    return s

ST = make_styles()


def escape(text):
    """Escape special ReportLab XML chars."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def md_inline(text):
    """Convert **bold**, `code` in a single line to RL markup."""
    text = escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`([^`]+)`',
                  r'<font name="Courier" color="#0d9488">\1</font>', text)
    return text


def parse_md(md_text):
    """Very lightweight Markdown → ReportLab flowables converter."""
    lines = md_text.split("\n")
    flowables = []
    in_code = False
    code_buf = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # ── fenced code block ──────────────────────────────────────────────
        if line.strip().startswith("```"):
            if in_code:
                # close code block
                code_text = "\n".join(code_buf)
                flowables.append(Paragraph(
                    '<font color="#e2e8f0">' + escape(code_text).replace("\n", "<br/>") + "</font>",
                    ST["code"]
                ))
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # ── horizontal rule ─────────────────────────────────────────────────
        if re.match(r'^---+$', line.strip()):
            flowables.append(HRFlowable(width="100%", thickness=0.5,
                                        color=colors.HexColor("#cbd5e1"),
                                        spaceBefore=6, spaceAfter=6))
            i += 1
            continue

        # ── headings ────────────────────────────────────────────────────────
        if line.startswith("# "):
            flowables.append(Paragraph(escape(line[2:].strip()), ST["h1"]))
            i += 1
            continue
        if line.startswith("## "):
            flowables.append(Paragraph(escape(line[3:].strip()), ST["h2"]))
            i += 1
            continue
        if line.startswith("### "):
            flowables.append(Paragraph(escape(line[4:].strip()), ST["h3"]))
            i += 1
            continue

        # ── Q/A pattern ─────────────────────────────────────────────────────
        m = re.match(r'^\*\*Q:\s*(.+?)\*\*$', line.strip())
        if m:
            flowables.append(Paragraph("Q: " + escape(m.group(1)), ST["q"]))
            i += 1
            continue

        m = re.match(r'^A:\s*(.+)', line.strip())
        if m:
            flowables.append(Paragraph("A: " + md_inline(m.group(1)), ST["a"]))
            i += 1
            continue

        # ── bullet ──────────────────────────────────────────────────────────
        m = re.match(r'^[-*]\s+(.+)', line)
        if m:
            flowables.append(Paragraph(
                "&bull;&nbsp;&nbsp;" + md_inline(m.group(1)),
                ST["bullet"]
            ))
            i += 1
            continue

        # ── table (simple | tables) ─────────────────────────────────────────
        if "|" in line and line.strip().startswith("|"):
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip().startswith("|"):
                raw_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                # skip separator rows like |---|---|
                if all(re.match(r'^-+$', c) for c in raw_cells if c):
                    i += 1
                    continue
                rows.append(raw_cells)
                i += 1
            if rows:
                col_widths = None
                t_data = []
                for ri, row in enumerate(rows):
                    t_data.append([
                        Paragraph(md_inline(cell),
                                  ParagraphStyle("tc", fontName="Helvetica-Bold" if ri == 0 else "Helvetica",
                                                 fontSize=9, textColor=INK if ri > 0 else TEAL,
                                                 leading=12))
                        for cell in row
                    ])
                tbl = Table(t_data, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), PANEL),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                     [colors.white, colors.HexColor("#f8fafc")]),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ]))
                flowables.append(Spacer(1, 4))
                flowables.append(tbl)
                flowables.append(Spacer(1, 6))
            continue

        # ── blank line ──────────────────────────────────────────────────────
        if not line.strip():
            flowables.append(Spacer(1, 5))
            i += 1
            continue

        # ── everything else → body paragraph ────────────────────────────────
        flowables.append(Paragraph(md_inline(line.strip()), ST["body"]))
        i += 1

    return flowables


def cover_page(title, subtitle, updated_line):
    fl = []
    fl.append(Spacer(1, 60*mm))
    fl.append(Paragraph(title, ST["title"]))
    fl.append(Spacer(1, 8*mm))
    fl.append(HRFlowable(width="60%", thickness=2, color=TEAL,
                          hAlign="CENTER", spaceBefore=0, spaceAfter=8*mm))
    fl.append(Paragraph(subtitle, ST["subtitle"]))
    fl.append(Spacer(1, 6*mm))
    fl.append(Paragraph(updated_line, ST["updated"]))
    fl.append(PageBreak())
    return fl


def make_pdf(md_path, pdf_path, title, subtitle):
    print(f"Building {pdf_path} …")
    with open(md_path, encoding="utf-8") as f:
        md = f.read()

    # find last-updated line
    m = re.search(r'\*Last updated.*?\*', md)
    updated = m.group(0).strip("*") if m else ""

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=18*mm, bottomMargin=18*mm,
        title=title,
        author="RMS Copilot",
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(20*mm, 10*mm, title)
        canvas.drawRightString(A4[0] - 20*mm, 10*mm, f"Page {doc.page}")
        canvas.restoreState()

    story = cover_page(title, subtitle, updated)
    story += parse_md(md)

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"  → {pdf_path} ✓")


if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    make_pdf(
        os.path.join(base, "INTERVIEW_QA.md"),
        os.path.join(base, "INTERVIEW_QA.pdf"),
        "RMS Copilot — Interview Q&A",
        "Technical interview prep covering every concept in the project",
    )
    make_pdf(
        os.path.join(base, "SQL_LEARNING.md"),
        os.path.join(base, "SQL_LEARNING.pdf"),
        "SQL Learning Notes",
        "Learning SQL through the RMS Copilot project — beginner to confident",
    )
    print("\nDone! Both PDFs saved to the rms-copilot folder.")
