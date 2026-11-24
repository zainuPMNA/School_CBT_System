"""Downloadable sample templates for bulk student / question upload."""

import csv
import io

from django.http import Http404, HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

STUDENT_COLUMNS = ["admission_no", "full_name", "class_name", "roll_no", "password"]
STUDENT_EXAMPLES = [
    ["2024001", "Asha Rao", "XII-A", "1", "Tq7mHw3kZp"],
    ["2024002", "Rahul Verma", "XII-A", "2", ""],
    ["2024/015", "Fatima Shaikh", "XI-B", "15", ""],
]
STUDENT_NOTES = [
    "admission_no  — REQUIRED. Becomes the login username. Letters, digits and @ . + - _ / only.",
    "full_name     — REQUIRED. Printed on the login slip and result reports.",
    "class_name    — REQUIRED. Must match the exam's Target Class exactly (e.g. XII-A). Case is ignored.",
    "roll_no       — optional. Must be unique within a class.",
    "password      — optional, best left BLANK: the app then creates a strong random password (shown on login slips).",
    "                If you type one: 6+ characters, not only numbers (no dates of birth / phone numbers),",
    "                not the admission number, roll number or name, and not a common password like 123456.",
    "",
    "Keep admission_no and roll_no as TEXT cells so Excel does not drop leading zeros.",
    "The first sheet is imported; this Instructions sheet is ignored.",
]

QUESTION_COLUMNS = [
    "sort_order",
    "question_text",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "correct_option",
    "explanation",
    "topic_tag",
]
QUESTION_EXAMPLES = [
    [
        "1",
        r"A body of mass $m = 2\,\text{kg}$ moves with speed $v = 3\,\text{m/s}$. Its kinetic energy is:",
        r"$9\,\text{J}$",
        r"$6\,\text{J}$",
        r"$18\,\text{J}$",
        r"$3\,\text{J}$",
        "A",
        r"$KE = \tfrac{1}{2}mv^2 = \tfrac{1}{2}(2)(3)^2 = 9\,\text{J}$",
        "Work, Energy & Power",
    ],
    [
        "2",
        r"The value of $\displaystyle\int_0^1 x^2\,dx$ is:",
        r"$\frac{1}{2}$",
        r"$\frac{1}{3}$",
        r"$1$",
        r"$\frac{2}{3}$",
        "B",
        r"$\int_0^1 x^2\,dx = \left[\frac{x^3}{3}\right]_0^1 = \frac{1}{3}$",
        "Definite Integrals",
    ],
    [
        "3",
        r"The chemical formula of slaked lime is:",
        r"$\ce{CaO}$",
        r"$\ce{Ca(OH)2}$",
        r"$\ce{CaCO3}$",
        r"$\ce{CaSO4}$",
        "B",
        r"Quicklime $\ce{CaO}$ reacts with water: $\ce{CaO + H2O -> Ca(OH)2}$ (slaked lime).",
        "s-Block Elements",
    ],
]
QUESTION_NOTES = [
    "sort_order      — optional. Display order when question shuffling is OFF.",
    "question_text   — REQUIRED. LaTeX: inline $...$ or \\(...\\); display $$...$$ or \\[...\\].",
    "option_a .. d   — REQUIRED. LaTeX allowed.",
    "correct_option  — REQUIRED. A, B, C or D (1-4 also accepted).",
    "explanation     — optional. Shown in post-result review if enabled.",
    "topic_tag       — optional. Used in question-wise analytics.",
    "",
    "Chemistry: use \\ce{...} (mhchem), e.g. $\\ce{H2SO4}$ or $\\ce{A + B -> C}$.",
    "Images: after uploading, open the question in the dashboard and attach the image there.",
    "The first sheet is imported; this Instructions sheet is ignored.",
]


def _xlsx_response(filename: str, sheet_title: str, columns, examples, notes, widths) -> HttpResponse:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(columns)
    header_fill = PatternFill("solid", fgColor="4F46E5")
    for idx, _ in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(idx)].width = widths.get(columns[idx - 1], 18)
    for row in examples:
        ws.append(row)
    # Text format for every column (keeps leading zeros, stops formula parsing).
    for row in ws.iter_rows(min_row=2, max_row=500, max_col=len(columns)):
        for cell in row:
            cell.number_format = "@"
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    info = wb.create_sheet("Instructions")
    info.column_dimensions["A"].width = 110
    info.append(["How to fill this template"])
    info["A1"].font = Font(bold=True, size=13)
    for line in notes:
        info.append([line])

    buffer = io.BytesIO()
    wb.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    return response


def _csv_response(filename: str, columns, examples) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(examples)
    # BOM so Excel opens UTF-8 correctly.
    response = HttpResponse("﻿" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    return response


def student_template_response(fmt: str) -> HttpResponse:
    if fmt == "xlsx":
        return _xlsx_response(
            "students_template", "Students", STUDENT_COLUMNS, STUDENT_EXAMPLES, STUDENT_NOTES,
            {"admission_no": 16, "full_name": 28, "class_name": 14, "roll_no": 10, "password": 16},
        )
    if fmt == "csv":
        return _csv_response("students_template", STUDENT_COLUMNS, STUDENT_EXAMPLES)
    raise Http404("Unknown format")


def question_template_response(fmt: str) -> HttpResponse:
    if fmt == "xlsx":
        return _xlsx_response(
            "questions_template", "Questions", QUESTION_COLUMNS, QUESTION_EXAMPLES, QUESTION_NOTES,
            {"sort_order": 10, "question_text": 60, "option_a": 22, "option_b": 22, "option_c": 22,
             "option_d": 22, "correct_option": 14, "explanation": 45, "topic_tag": 22},
        )
    if fmt == "csv":
        return _csv_response("questions_template", QUESTION_COLUMNS, QUESTION_EXAMPLES)
    raise Http404("Unknown format")
