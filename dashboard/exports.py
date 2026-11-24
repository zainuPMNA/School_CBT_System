"""
Result exports: Excel workbook (rank list, question analysis, topics, full
response matrix), CSV rank list, and PDF scorecards (ReportLab — pure Python,
works offline, no system libraries).
"""

import csv
import io
import os
import re
from decimal import Decimal
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from exams.models import OPTION_KEYS, Exam, StudentAnswer

from .models import institution_name

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADER_FILL = PatternFill("solid", fgColor="4F46E5")
GREEN = PatternFill("solid", fgColor="D1FAE5")
RED = PatternFill("solid", fgColor="FFE4E6")
GREY = PatternFill("solid", fgColor="F1F5F9")


def safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:80] or "exam"


def _num(value):
    return float(value) if isinstance(value, Decimal) else value


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def _header(ws, row_idx, columns, widths=None):
    for col, title in enumerate(columns, start=1):
        cell = ws.cell(row=row_idx, column=col, value=title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col)].width = (widths or {}).get(title, 14)


def results_workbook_response(exam: Exam, results, analysis) -> HttpResponse:
    wb = Workbook()

    # --- Sheet 1: rank list ---------------------------------------------------
    ws = wb.active
    ws.title = "Rank List"
    ws.append([f"{institution_name()} — {exam.title}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"{exam.subject} · Class {exam.target_class} · {results.total_questions} questions · "
               f"Max {results.max_marks} · +{exam.positive_marks}/-{exam.negative_marks} · "
               f"Exported {timezone.localtime():%d %b %Y %H:%M}"])
    stats = results.stats
    ws.append([f"Appeared {stats['appeared']} · Absent {stats['absent']} · Average {stats['average'] or '-'} · "
               f"Highest {stats['highest'] or '-'} · Lowest {stats['lowest'] or '-'} · Median {stats['median'] or '-'}"])
    ws.append([])
    columns = ["Rank", "Admission No", "Name", "Class", "Roll", "Score", "Percentage", "Correct", "Wrong",
               "Attempted", "Skipped", "Accuracy %", "Time Taken (min:s)", "Status", "Violations"]
    _header(ws, 5, columns, {"Name": 28, "Admission No": 16, "Status": 16, "Time Taken (min:s)": 16})
    for r in results.rows:
        ws.append([r["rank"], r["username"], r["name"], r["class_name"], r["roll"], _num(r["score"]),
                   _num(r["percentage"]), r["correct"], r["wrong"], r["attempted"], r["skipped"],
                   _num(r["accuracy"]), r["time_text"], r["status_display"], r["violations"]])
    for r in results.writing:
        ws.append(["", r["username"], r["name"], r["class_name"], r["roll"], "", "", "", "", "", "", "", "", "Still writing", r["violations"]])
    for r in results.absent:
        ws.append(["", r["username"], r["name"], r["class_name"], r["roll"], "", "", "", "", "", "", "", "", "Absent", ""])
    ws.freeze_panes = "C6"

    # --- Sheet 2: question analysis -------------------------------------------
    qa = wb.create_sheet("Question Analysis")
    q_columns = ["Q No", "Topic", "Key", "Correct", "Wrong", "Skipped", "% Correct", "% Wrong", "% Skipped",
                 "Chose A", "Chose B", "Chose C", "Chose D", "Difficulty", "Discrimination", "Flags", "Question"]
    _header(qa, 1, q_columns, {"Topic": 22, "Flags": 40, "Question": 80})
    for item in analysis["items"]:
        q = item["question"]
        dist = {d["key"]: d["count"] for d in item["distribution"]}
        qa.append([item["number"], item["topic"], q.correct_option, item["correct"], item["wrong"], item["skipped"],
                   _num(item["pct_correct"]), _num(item["pct_wrong"]), _num(item["pct_skipped"]),
                   dist["A"], dist["B"], dist["C"], dist["D"], item["difficulty"],
                   _num(item["discrimination"]) if item["discrimination"] is not None else "",
                   "; ".join(item["flags"]), " ".join(q.question_text.split())[:500]])
        key_col = 10 + OPTION_KEYS.index(q.correct_option)
        qa.cell(row=qa.max_row, column=key_col).fill = GREEN
    qa.freeze_panes = "B2"

    # --- Sheet 3: topics ------------------------------------------------------
    tp = wb.create_sheet("Topics")
    _header(tp, 1, ["Topic", "Questions", "Correct", "Wrong", "Skipped", "% Correct"], {"Topic": 30})
    for t in analysis["topics"]:
        tp.append([t["topic"], t["questions"], t["correct"], t["wrong"], t["skipped"], _num(t["pct_correct"])])

    # --- Sheet 4: full response matrix -----------------------------------------
    rm = wb.create_sheet("Responses")
    questions = [item["question"] for item in analysis["items"]]
    _header(rm, 1, ["Rank", "Admission No", "Name", "Score"] + [f"Q{i}" for i in range(1, len(questions) + 1)],
            {"Name": 26, "Admission No": 16})
    rm.append(["", "", "Answer key", ""] + [q.correct_option for q in questions])
    for col in range(5, 5 + len(questions)):
        rm.cell(row=2, column=col).font = Font(bold=True)
    attempt_ids = [r["attempt"].pk for r in results.rows]
    chosen = {
        (a, q): opt
        for a, q, opt in StudentAnswer.objects.filter(attempt_id__in=attempt_ids)
        .values_list("attempt_id", "question_id", "selected_option")
    }
    for r in results.rows:
        rm.append([r["rank"], r["username"], r["name"], _num(r["score"])]
                  + [chosen.get((r["attempt"].pk, q.pk)) or "-" for q in questions])
        for offset, q in enumerate(questions):
            cell = rm.cell(row=rm.max_row, column=5 + offset)
            cell.alignment = Alignment(horizontal="center")
            cell.fill = GREY if cell.value == "-" else (GREEN if cell.value == q.correct_option else RED)
    for col in range(5, 5 + len(questions)):
        rm.column_dimensions[get_column_letter(col)].width = 5
    rm.freeze_panes = "E3"

    buffer = io.BytesIO()
    wb.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{safe_filename(exam.title)}_results.xlsx"'
    return response


def results_csv_response(exam: Exam, results) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["rank", "admission_no", "name", "class", "roll", "score", "max_marks", "percentage", "correct",
                     "wrong", "attempted", "skipped", "accuracy_pct", "time_taken_seconds", "status", "violations"])
    for r in results.rows:
        writer.writerow([r["rank"], r["username"], r["name"], r["class_name"], r["roll"], r["score"], results.max_marks,
                         r["percentage"], r["correct"], r["wrong"], r["attempted"], r["skipped"], r["accuracy"],
                         r["time_taken"] if r["time_taken"] is not None else "", r["status"], r["violations"]])
    for r in results.writing:
        writer.writerow(["", r["username"], r["name"], r["class_name"], r["roll"], "", results.max_marks, "", "", "", "", "", "", "", "IN_PROGRESS", r["violations"]])
    for r in results.absent:
        writer.writerow(["", r["username"], r["name"], r["class_name"], r["roll"], "", results.max_marks, "", "", "", "", "", "", "", "ABSENT", ""])
    response = HttpResponse("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{safe_filename(exam.title)}_results.csv"'
    return response


# ---------------------------------------------------------------------------
# PDF scorecards
# ---------------------------------------------------------------------------
_FONT_CANDIDATES = [
    # Ubuntu (fonts-dejavu-core is installed by default) — covers Devanagari-free Unicode names,
    # accented Latin, Greek, etc. Falls back to Helvetica if none is found.
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
]
_font_cache: tuple[str, str] | None = None


def _fonts() -> tuple[str, str]:
    global _font_cache
    if _font_cache:
        return _font_cache
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold in _FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("CBTSans", regular))
                pdfmetrics.registerFont(TTFont("CBTSans-Bold", bold))
                _font_cache = ("CBTSans", "CBTSans-Bold")
                return _font_cache
            except Exception:  # noqa: BLE001 - unreadable font, try next
                continue
    _font_cache = ("Helvetica", "Helvetica-Bold")
    return _font_cache


def _scorecard_flowables(card: dict, styles, fonts):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    regular, bold = fonts
    exam = card["exam"]
    attempt = card["attempt"]
    row = card["row"] or {}
    student = attempt.student
    profile = getattr(student, "student_profile", None)
    stats = card["stats"]
    out = []

    school = Paragraph(xml_escape(institution_name()), styles["school"])
    title = Paragraph(f"Scorecard — {xml_escape(exam.title)}", styles["title"])
    logo = _logo_flowable(10 * mm, 22 * mm)
    if logo is not None:
        head = Table([[logo, [school, title]]], colWidths=[logo.drawWidth + 4 * mm, None])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (0, 0), 4 * mm), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        head.hAlign = "LEFT"
        out.append(head)
    else:
        out.append(school)
        out.append(title)
    out.append(Paragraph(f"{xml_escape(exam.subject)} · Class {xml_escape(exam.target_class)} · "
                         f"{timezone.localtime(attempt.submitted_at):%d %b %Y}" if attempt.submitted_at else xml_escape(exam.subject),
                         styles["muted"]))
    out.append(Spacer(1, 4 * mm))

    info = [
        ["Name", profile.full_name if profile else student.username, "Admission No", student.username],
        ["Class", profile.batch_or_class if profile else "-", "Roll No", (profile.roll_number if profile else "") or "-"],
    ]
    t = Table(info, colWidths=[28 * mm, 62 * mm, 30 * mm, 50 * mm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), regular, 9.5),
        ("FONT", (0, 0), (0, -1), bold, 9.5),
        ("FONT", (2, 0), (2, -1), bold, 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#475569")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    out.append(t)
    out.append(Spacer(1, 4 * mm))

    rank_text = f"{card['rank']} / {card['appeared']}" if card["rank"] else "-"
    summary = [
        ["Score", "Rank", "Correct", "Wrong", "Skipped", "Accuracy", "Time"],
        [f"{attempt.total_score} / {stats['max_marks']}", rank_text, str(attempt.correct_count), str(attempt.wrong_count),
         str(row.get("skipped", "-")), f"{row.get('accuracy', '-')}%", row.get("time_text", "-")],
    ]
    t = Table(summary, colWidths=[34 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 24 * mm, 24 * mm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), bold, 8.5),
        ("FONT", (0, 1), (-1, 1), bold, 12),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4F46E5")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#EEF2FF")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, 1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C7D2FE")),
    ]))
    out.append(t)
    out.append(Spacer(1, 2 * mm))
    out.append(Paragraph(
        f"Class average {stats['average'] or '-'} · Highest {stats['highest'] or '-'} · Marking +{exam.positive_marks} / -{exam.negative_marks}",
        styles["muted"]))
    out.append(Spacer(1, 5 * mm))

    out.append(Paragraph("Topic-wise performance", styles["h2"]))
    topic_rows = [["Topic", "Questions", "Correct", "Wrong", "Skipped", "Marks", "Accuracy"]]
    for tp in card["topics"]:
        topic_rows.append([Paragraph(xml_escape(tp["topic"]), styles["cell"]), tp["questions"], tp["correct"], tp["wrong"],
                           tp["skipped"], str(tp["marks"]), f"{tp['accuracy']}%"])
    t = Table(topic_rows, colWidths=[62 * mm, 20 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 20 * mm], repeatRows=1)
    t.setStyle(_grid_style(regular, bold))
    out.append(t)
    out.append(Spacer(1, 5 * mm))

    out.append(Paragraph("Question-wise result (in the order you saw the questions)", styles["h2"]))
    q_rows = [["Q", "Topic", "Your answer", "Correct answer", "Result", "Marks"]]
    result_colors = []
    for i, item in enumerate(card["items"], start=1):
        q_rows.append([item["number"], Paragraph(xml_escape(item["question"].topic_tag or "-"), styles["cell"]),
                       item["your_label"] or "-", item["correct_label"], item["outcome"].capitalize(), str(item["marks"])])
        color = {"correct": "#D1FAE5", "wrong": "#FFE4E6"}.get(item["outcome"], "#F1F5F9")
        result_colors.append(("BACKGROUND", (4, i), (4, i), colors.HexColor(color)))
    t = Table(q_rows, colWidths=[12 * mm, 70 * mm, 24 * mm, 28 * mm, 20 * mm, 20 * mm], repeatRows=1)
    style = _grid_style(regular, bold)
    for cmd in result_colors:
        style.add(*cmd)
    t.setStyle(style)
    out.append(t)
    return out


def _logo_flowable(max_h, max_w):
    """The school logo from Settings as a ReportLab Image, or None if there is none / it can't be read."""
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image

    try:
        from .models import SiteSettings

        path = SiteSettings.load().logo_path
        if not path:
            return None
        w, h = ImageReader(path).getSize()
        scale = min(max_h / h, max_w / w)
        return Image(path, width=w * scale, height=h * scale)
    except Exception:  # noqa: BLE001 - a broken logo must never stop scorecards
        return None


def _grid_style(regular, bold):
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    return TableStyle([
        ("FONT", (0, 0), (-1, -1), regular, 8.5),
        ("FONT", (0, 0), (-1, 0), bold, 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("ALIGN", (2, 1), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ])


def scorecards_pdf(cards: list[dict]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, SimpleDocTemplate

    fonts = _fonts()
    regular, bold = fonts
    styles = {
        "school": ParagraphStyle("school", fontName=bold, fontSize=9, textColor=colors.HexColor("#4338CA")),
        "title": ParagraphStyle("title", fontName=bold, fontSize=16, leading=20, spaceBefore=2),
        "muted": ParagraphStyle("muted", fontName=regular, fontSize=8.5, textColor=colors.HexColor("#64748B")),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=11, spaceAfter=4),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=8.5, leading=10),
    }

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title="Scorecards", author=institution_name(),
    )

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#94A3B8"))
        canvas.drawString(14 * mm, 8 * mm, f"Generated {timezone.localtime():%d %b %Y %H:%M} · {institution_name()} CBT")
        canvas.drawRightString(A4[0] - 14 * mm, 8 * mm, f"Page {document.page}")
        canvas.restoreState()

    story = []
    for index, card in enumerate(cards):
        if index:
            story.append(PageBreak())
        story.extend(_scorecard_flowables(card, styles, fonts))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def pdf_response(content: bytes, filename: str, inline: bool = False) -> HttpResponse:
    response = HttpResponse(content, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{safe_filename(filename)}.pdf"'
    return response
