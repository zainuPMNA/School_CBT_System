"""Small helpers shared by the accounts and exams apps."""

import re
import secrets

from django.conf import settings
from django.contrib.auth.password_validation import CommonPasswordValidator
from django.core.exceptions import ValidationError

# No look-alike characters (0/O, 1/l/I) so students can read slips easily.
_LOWER = "abcdefghjkmnpqrstuvwxyz"
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
MIN_STUDENT_PASSWORD = 6


def normalize_class_name(value: str | None) -> str:
    """Canonical form for class/batch names so 'xii a ' == 'XII A'."""
    if not value:
        return ""
    return " ".join(str(value).split()).upper()


def parse_class_list(value: str | None) -> list[str]:
    """'x-a, X-B;x-a\\nXI' -> ['X-A', 'X-B', 'XI'] (normalised, no duplicates, order kept)."""
    classes: list[str] = []
    for part in re.split(r"[,;\n]+", value or ""):
        name = normalize_class_name(part)
        if name and name not in classes:
            classes.append(name)
    return classes


def generate_readable_password(length: int | None = None) -> str:
    """
    Unpredictable password for printed login slips, e.g. 'aK7mPq3xRt'.

    Capitals, small letters and digits in RANDOM positions (no fixed
    "letters then digits" pattern), drawn from a cryptographic random source.
    At the default 10 characters that is about 2^57 possibilities. Look-alike
    characters (0/O, 1/l/I) are left out so slips stay easy to read.
    """
    length = max(8, length or settings.CBT_GENERATED_PASSWORD_LENGTH)
    chars = [secrets.choice(_LOWER), secrets.choice(_UPPER), secrets.choice(_DIGITS)]
    pool = _LOWER + _UPPER + _DIGITS
    chars += [secrets.choice(pool) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def student_password_problem(password: str, admission_no: str = "", roll_no: str = "", full_name: str = "") -> str | None:
    """
    Why a password typed by a teacher is too predictable, or None if it is fine.
    Rejects: short ones, digits only (dates of birth, phone numbers), the
    student's own admission/roll number or name, repeated or sequential
    characters, and Django's list of 20,000 common passwords.
    """
    pw = password or ""
    low = pw.lower()
    if len(pw) < MIN_STUDENT_PASSWORD:
        return f"must be at least {MIN_STUDENT_PASSWORD} characters"
    if pw.isdigit():
        return "must not be only numbers (dates of birth and phone numbers are easy to guess)"
    for value, what in ((admission_no, "admission number"), (roll_no, "roll number")):
        if value and len(value) >= 2 and value.lower() in low:
            return f"must not contain the student's {what}"
    for part in re.split(r"\s+", full_name or ""):
        if len(part) >= 3 and part.lower() in low:
            return "must not contain the student's name"
    if len(set(low)) <= 2:
        return "is too repetitive"
    if low in "abcdefghijklmnopqrstuvwxyz" or low in "0123456789" or low in "qwertyuiopasdfghjklzxcvbnm":
        return "is a keyboard or alphabet sequence"
    try:
        CommonPasswordValidator().validate(pw)
    except ValidationError:
        return "is a commonly used password"
    return None


def get_client_ip(request) -> str | None:
    """Client IP, trusting X-Real-IP only when we sit behind our own Nginx."""
    if settings.CBT_TRUST_X_REAL_IP:
        real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
        if real_ip:
            return real_ip
    return request.META.get("REMOTE_ADDR") or None
