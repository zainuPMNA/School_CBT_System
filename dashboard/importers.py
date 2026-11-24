"""
Excel / CSV importers for students and questions.

Both importers are ALL-OR-NOTHING: every row is validated first, and nothing
is written unless the whole file is clean, so a half-imported class list
can never happen. Errors are reported per spreadsheet row number.
"""

import csv
import io
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.db import transaction
from django.db.models.functions import Lower

from accounts.models import Role, StudentProfile, User, cbt_username_validator
from accounts.utils import generate_readable_password, normalize_class_name, student_password_problem
from exams.models import OPTION_KEYS, Exam, Question

MAX_ROWS = 5000


class ImportFileError(Exception):
    """The file itself is unusable (wrong type, no header, missing columns)."""


@dataclass
class RowError:
    row: int
    message: str


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[RowError] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    generated_passwords: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, row: int, message: str) -> None:
        self.errors.append(RowError(row, message))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _normalize_header(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _cell_to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _iter_xlsx(uploaded):
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(uploaded, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any openpyxl failure means a bad file
        raise ImportFileError("Could not read the Excel file. Save it as .xlsx and try again.") from exc
    try:
        sheet = workbook.worksheets[0]
        for row in sheet.iter_rows(values_only=True):
            yield list(row)
    finally:
        workbook.close()


def _iter_csv(uploaded):
    raw = uploaded.read()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        raise ImportFileError("Could not decode the CSV file. Save it as UTF-8.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    yield from csv.reader(io.StringIO(text), dialect)


def read_rows(uploaded) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """
    Return (headers, rows) where each row is (spreadsheet_row_number, {header: value}).
    Headers are normalised: 'Admission No' -> 'admission_no'.
    """
    ext = os.path.splitext(uploaded.name)[1].lower()
    if ext == ".xlsx":
        iterator = _iter_xlsx(uploaded)
    elif ext == ".csv":
        iterator = _iter_csv(uploaded)
    else:
        raise ImportFileError("Unsupported file type. Use .xlsx or .csv.")

    headers: list[str] | None = None
    rows: list[tuple[int, dict[str, str]]] = []
    for line_no, raw_row in enumerate(iterator, start=1):
        values = [_cell_to_str(v) for v in raw_row]
        if not any(values):
            continue
        if headers is None:
            headers = [_normalize_header(v) for v in values]
            continue
        record = {h: (values[i] if i < len(values) else "") for i, h in enumerate(headers) if h}
        rows.append((line_no, record))
        if len(rows) > MAX_ROWS:
            raise ImportFileError(f"Too many rows (limit {MAX_ROWS}). Split the file.")
    if headers is None:
        raise ImportFileError("The file is empty.")
    return headers, rows


def _resolve_columns(headers, aliases: dict[str, tuple[str, ...]], required: tuple[str, ...]):
    mapping: dict[str, str] = {}
    for canonical, names in aliases.items():
        for name in names:
            if name in headers:
                mapping[canonical] = name
                break
    missing = [c for c in required if c not in mapping]
    if missing:
        raise ImportFileError(
            "Missing required column(s): " + ", ".join(missing)
            + ". Download the sample template to see the expected headers."
        )
    return mapping


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
STUDENT_ALIASES = {
    "admission_no": ("admission_no", "admission_number", "admission", "adm_no", "username"),
    "full_name": ("full_name", "name", "student_name"),
    "class_name": ("class_name", "class", "batch", "batch_or_class", "section"),
    "roll_no": ("roll_no", "roll_number", "roll"),
    "password": ("password", "pass", "pwd"),
}
STUDENT_REQUIRED = ("admission_no", "full_name", "class_name")


def import_students(headers, rows, *, update_existing: bool = False) -> ImportReport:
    cols = _resolve_columns(headers, STUDENT_ALIASES, STUDENT_REQUIRED)
    report = ImportReport()

    def get(record, key):
        column = cols.get(key)
        return (record.get(column, "") if column else "").strip()

    parsed = []
    seen_usernames: dict[str, int] = {}
    seen_rolls: dict[tuple[str, str], int] = {}
    for line_no, record in rows:
        adm = get(record, "admission_no")
        name = " ".join(get(record, "full_name").split())
        cls = normalize_class_name(get(record, "class_name"))
        roll = get(record, "roll_no")
        password = get(record, "password")

        problems = []
        if not adm:
            problems.append("admission_no is empty")
        else:
            try:
                cbt_username_validator(adm)
            except Exception:  # noqa: BLE001 - ValidationError
                problems.append(f"admission_no '{adm}' has invalid characters (allowed: letters, digits, @ . + - _ /)")
            if len(adm) > 150:
                problems.append("admission_no is too long")
        if not name:
            problems.append("full_name is empty")
        elif len(name) > 150:
            problems.append("full_name is longer than 150 characters")
        if not cls:
            problems.append("class_name is empty")
        elif len(cls) > 50:
            problems.append("class_name is longer than 50 characters")
        if len(roll) > 20:
            problems.append("roll_no is longer than 20 characters")
        if password:
            weak = student_password_problem(password, adm, roll, name)
            if weak:
                problems.append(f"password {weak} (or leave it blank to auto-generate a strong one)")
        if adm:
            key = adm.lower()
            if key in seen_usernames:
                problems.append(f"duplicate admission_no (also on row {seen_usernames[key]})")
            seen_usernames.setdefault(key, line_no)
        if roll and cls:
            rkey = (cls, roll)
            if rkey in seen_rolls:
                problems.append(f"roll_no {roll} repeated in {cls} (also on row {seen_rolls[rkey]})")
            seen_rolls.setdefault(rkey, line_no)

        if problems:
            report.error(line_no, "; ".join(problems))
        else:
            parsed.append((line_no, adm, name, cls, roll, password))

    existing = {
        u.lower_username: u
        for u in User.objects.annotate(lower_username=Lower("username"))
        .filter(lower_username__in=[p[1].lower() for p in parsed])
        .select_related("student_profile")
    }
    roll_owner = {
        (p.batch_or_class, p.roll_number): p.user_id
        for p in StudentProfile.objects.filter(batch_or_class__in={p[3] for p in parsed}).exclude(roll_number="")
    }

    to_create, to_update = [], []
    for line_no, adm, name, cls, roll, password in parsed:
        user = existing.get(adm.lower())
        if user is not None and user.role != Role.STUDENT:
            report.error(line_no, f"'{adm}' belongs to a {user.get_role_display()} account")
            continue
        if user is not None and not update_existing:
            report.skipped += 1
            continue
        owner = roll_owner.get((cls, roll)) if roll else None
        if owner is not None and (user is None or owner != user.pk):
            report.error(line_no, f"roll_no {roll} in {cls} already belongs to another student")
            continue
        if user is None:
            to_create.append((adm, name, cls, roll, password))
        else:
            to_update.append((user, name, cls, roll, password))

    if report.errors:
        report.skipped = 0
        return report

    with transaction.atomic():
        new_users, new_profiles = [], []
        for adm, name, cls, roll, password in to_create:
            if not password:
                password = generate_readable_password()
                report.generated_passwords += 1
            user = User(username=adm, role=Role.STUDENT, is_active=True)
            user.set_password(password)
            new_users.append(user)
            new_profiles.append(
                StudentProfile(full_name=name, batch_or_class=cls, roll_number=roll, initial_password=password)
            )
        created_users = User.objects.bulk_create(new_users, batch_size=200)
        for user, profile in zip(created_users, new_profiles):
            profile.user = user
        StudentProfile.objects.bulk_create(new_profiles, batch_size=200)
        report.created = len(created_users)

        for user, name, cls, roll, password in to_update:
            profile = getattr(user, "student_profile", None) or StudentProfile(user=user)
            profile.full_name = name
            profile.batch_or_class = cls
            profile.roll_number = roll
            if password:
                user.set_password(password)
                user.save(update_fields=["password"])
                profile.initial_password = password
            profile.save()
            report.updated += 1

    report.classes = sorted({p[3] for p in parsed})
    return report


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
QUESTION_ALIASES = {
    "sort_order": ("sort_order", "order", "q_no", "question_no", "s_no", "sno"),
    "question_text": ("question_text", "question", "question_stem"),
    "option_a": ("option_a", "a", "opt_a", "option_1"),
    "option_b": ("option_b", "b", "opt_b", "option_2"),
    "option_c": ("option_c", "c", "opt_c", "option_3"),
    "option_d": ("option_d", "d", "opt_d", "option_4"),
    "correct_option": ("correct_option", "answer", "correct", "correct_answer", "key", "answer_key"),
    "explanation": ("explanation", "solution"),
    "topic_tag": ("topic_tag", "topic", "tag", "chapter"),
}
QUESTION_REQUIRED = ("question_text", "option_a", "option_b", "option_c", "option_d", "correct_option")
_DIGIT_TO_KEY = {"1": "A", "2": "B", "3": "C", "4": "D"}


def _parse_correct(value: str) -> str | None:
    v = value.strip().upper().replace("OPTION", "").replace("(", "").replace(")", "").strip(" .")
    if v in OPTION_KEYS:
        return v
    return _DIGIT_TO_KEY.get(v)


def import_questions(exam: Exam, headers, rows, *, replace: bool = False) -> ImportReport:
    cols = _resolve_columns(headers, QUESTION_ALIASES, QUESTION_REQUIRED)
    report = ImportReport()

    def get(record, key):
        column = cols.get(key)
        return (record.get(column, "") if column else "").strip()

    if replace:
        next_order = 1
    else:
        last = exam.questions.order_by("-sort_order").values_list("sort_order", flat=True).first()
        next_order = (last or 0) + 1

    new_questions = []
    for line_no, record in rows:
        problems = []
        text = get(record, "question_text")
        options = {k: get(record, f"option_{k.lower()}") for k in OPTION_KEYS}
        correct_raw = get(record, "correct_option")
        correct = _parse_correct(correct_raw)
        order_raw = get(record, "sort_order")
        topic = get(record, "topic_tag")

        if not text:
            problems.append("question_text is empty")
        for key, value in options.items():
            if not value:
                problems.append(f"option_{key.lower()} is empty")
        if correct is None:
            problems.append(f"correct_option '{correct_raw}' must be A, B, C or D")
        if len(topic) > 100:
            problems.append("topic_tag is longer than 100 characters")
        sort_order = None
        if order_raw:
            try:
                sort_order = int(float(order_raw))
                if sort_order < 0:
                    raise ValueError
            except ValueError:
                problems.append(f"sort_order '{order_raw}' must be a whole number")

        if problems:
            report.error(line_no, "; ".join(problems))
            continue
        if sort_order is None:
            sort_order = next_order
        next_order = max(next_order, sort_order) + 1
        new_questions.append(
            Question(
                exam=exam,
                sort_order=sort_order,
                question_text=text,
                option_a=options["A"],
                option_b=options["B"],
                option_c=options["C"],
                option_d=options["D"],
                correct_option=correct,
                explanation=get(record, "explanation"),
                topic_tag=topic,
            )
        )

    if not rows:
        report.error(0, "The file has a header row but no questions.")
    if report.errors:
        return report

    with transaction.atomic():
        if replace:
            for question in exam.questions.all():
                question.delete()  # per-object delete so image files are removed
        Question.objects.bulk_create(new_questions, batch_size=200)
    report.created = len(new_questions)
    return report
