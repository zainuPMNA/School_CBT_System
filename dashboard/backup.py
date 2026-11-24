"""
Full backup and restore of the CBT data (admin "Backup" page + manage.py commands).

A backup file (*.cbtbackup) is a ZIP with:
    manifest.json   what is inside: date, school, app version, counts, migrations, checksum
    data.json       every student, teacher, admin, exam, question, attempt, answer,
                    violation, login record and the school settings (Django fixture)
    media/...       uploaded files: school logo and question diagrams

With a password the whole ZIP is encrypted with AES-256-GCM. The key comes from the
password through scrypt, so a lost pendrive does not expose student data, and any
change to the file (damage or tampering) is detected before anything is restored.

Restoring REPLACES all current data with the backup's data, inside one database
transaction: if anything fails, nothing changes. A safety copy of the current data
is always saved on this computer first.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

import cbt_project

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
EXTENSION = ".cbtbackup"
MAGIC = b"CBT-BACKUP-AES256GCM-1\n"
SALT_LEN, NONCE_LEN = 16, 12
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}

# What goes into data.json. Sessions, content types and permissions are rebuilt by
# Django itself and are deliberately left out (everyone signs in again after a restore).
DUMP_LABELS = ["auth.group", "accounts", "exams", "dashboard"]
OUR_APPS = ["accounts", "exams", "dashboard"]

NAME_RE = re.compile(r"^cbt_backup_\d{8}_\d{6}(?:-\d{1,3})?_(?P<kind>[a-z-]+)\.cbtbackup$")
KINDS = {
    "manual": "Made from this page",
    "auto": "Automatic (nightly)",
    "before-restore": "Safety copy before a restore",
    "uploaded": "Uploaded from a file",
}


class BackupError(Exception):
    """A problem the admin can understand and fix (wrong password, wrong file, ...)."""


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------
def _key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)


def encrypt(data: bytes, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    return MAGIC + salt + nonce + AESGCM(_key(password, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, password: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    head = len(MAGIC)
    salt = blob[head:head + SALT_LEN]
    nonce = blob[head + SALT_LEN:head + SALT_LEN + NONCE_LEN]
    try:
        return AESGCM(_key(password, salt)).decrypt(nonce, blob[head + SALT_LEN + NONCE_LEN:], MAGIC)
    except (InvalidTag, ValueError):
        raise BackupError("Wrong backup password, or the file is damaged.") from None


def is_encrypted(blob_or_head: bytes) -> bool:
    return blob_or_head.startswith(MAGIC)


# ---------------------------------------------------------------------------
# Creating a backup
# ---------------------------------------------------------------------------
def _applied_migrations() -> dict[str, list[str]]:
    applied = MigrationRecorder(connection).applied_migrations()
    return {app: sorted(name for (a, name) in applied if a == app) for app in OUR_APPS}


def _counts() -> dict[str, int]:
    from accounts.models import Role, User
    from exams.models import Exam, Question, StudentAnswer, StudentAttempt

    return {
        "students": User.objects.filter(role=Role.STUDENT).count(),
        "staff": User.objects.exclude(role=Role.STUDENT).count(),
        "exams": Exam.objects.count(),
        "questions": Question.objects.count(),
        "attempts": StudentAttempt.objects.count(),
        "answers": StudentAnswer.objects.count(),
    }


def _media_files() -> list[tuple[str, Path]]:
    root = Path(settings.MEDIA_ROOT)
    if not root.is_dir():
        return []
    return [(p.relative_to(root).as_posix(), p) for p in sorted(root.rglob("*")) if p.is_file()]


def create_backup(*, by: str = "", password: str = "") -> bytes:
    """Return the bytes of a complete backup file (encrypted when a password is given)."""
    from dashboard.models import SiteSettings

    out = io.StringIO()
    call_command("dumpdata", *DUMP_LABELS, format="json", use_natural_foreign_keys=True,
                 stdout=out, verbosity=0)
    data = out.getvalue().encode("utf-8")
    media = _media_files()
    manifest = {
        "format": FORMAT_VERSION,
        "app": "CBT",
        "app_version": cbt_project.__version__,
        "created_at": timezone.localtime().isoformat(timespec="seconds"),
        "created_by": by,
        "school": SiteSettings.load().institution_name,
        "database": connection.vendor,
        "counts": _counts(),
        "media_files": len(media),
        "migrations": _applied_migrations(),
        "data_sha256": hashlib.sha256(data).hexdigest(),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("data.json", data)
        for rel, path in media:
            zf.write(path, f"media/{rel}")
    raw = buf.getvalue()
    return encrypt(raw, password) if password else raw


# ---------------------------------------------------------------------------
# Reading / checking a backup
# ---------------------------------------------------------------------------
@dataclass
class Backup:
    manifest: dict
    data: bytes
    media: list[tuple[str, bytes]] = field(default_factory=list)
    encrypted: bool = False

    @property
    def summary(self) -> dict:
        m = self.manifest
        return {
            "created_at": m.get("created_at", ""),
            "created_display": m.get("created_at", "")[:16].replace("T", " "),
            "created_by": m.get("created_by", ""),
            "school": m.get("school", ""),
            "app_version": m.get("app_version", ""),
            "counts": m.get("counts", {}),
            "media_files": m.get("media_files", 0),
            "encrypted": self.encrypted,
        }


def _safe_media_name(name: str) -> str | None:
    """'media/branding/x.png' -> 'branding/x.png'; None for anything that could escape MEDIA_ROOT."""
    p = PurePosixPath(name)
    parts = p.parts
    if len(parts) < 2 or parts[0] != "media" or p.is_absolute():
        return None
    if any(part in ("..", "") or ":" in part or "\\" in part for part in parts[1:]):
        return None
    return "/".join(parts[1:])


def read_backup(blob: bytes, password: str = "") -> Backup:
    """Open and fully verify a backup file. Raises BackupError with a friendly message."""
    encrypted = is_encrypted(blob)
    if encrypted:
        if not password:
            raise BackupError("This backup is password-protected. Enter its password.")
        blob = decrypt(blob, password)
    if not blob.startswith(b"PK"):
        raise BackupError("This is not a CBT backup file (it should end in .cbtbackup).")
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        names = set(zf.namelist())
        if not {"manifest.json", "data.json"} <= names:
            raise BackupError("This is not a CBT backup file (manifest or data missing).")
        manifest = json.loads(zf.read("manifest.json"))
        data = zf.read("data.json")
        media = []
        for name in zf.namelist():
            if name.endswith("/") or not name.startswith("media/"):
                continue
            rel = _safe_media_name(name)
            if rel is None:
                raise BackupError("The backup contains an unsafe file path and was rejected.")
            media.append((rel, zf.read(name)))
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, OSError, EOFError):
        raise BackupError("The backup file is damaged and cannot be read.") from None
    if manifest.get("app") != "CBT" or not isinstance(manifest.get("format"), int):
        raise BackupError("This is not a CBT backup file.")
    if manifest["format"] > FORMAT_VERSION:
        raise BackupError("This backup was made by a newer version of the CBT app. Update this app first.")
    if hashlib.sha256(data).hexdigest() != manifest.get("data_sha256"):
        raise BackupError("The backup file is damaged (checksum does not match). It was not restored.")
    return Backup(manifest=manifest, data=data, media=media, encrypted=encrypted)


def check_restorable(backup: Backup) -> list[dict]:
    """Raise BackupError if this backup cannot safely be restored here; return the parsed records."""
    loader = MigrationLoader(None, ignore_no_migrations=True)
    known = {(app, name) for (app, name) in loader.disk_migrations}
    for app, names in (backup.manifest.get("migrations") or {}).items():
        unknown = [n for n in names if (app, n) not in known]
        if unknown:
            raise BackupError(
                "This backup was made by a newer version of the CBT app "
                f"({backup.manifest.get('app_version', '?')}). Update this app first, then restore."
            )
    try:
        records = json.loads(backup.data)
    except json.JSONDecodeError:
        raise BackupError("The backup data is damaged.") from None
    has_admin = any(
        r.get("model") == "accounts.user"
        and r.get("fields", {}).get("role") == "ADMIN"
        and r.get("fields", {}).get("is_active", True)
        for r in records
    )
    if not has_admin:
        raise BackupError("This backup has no active admin account, so nobody could sign in after restoring it.")
    return records


def exam_running() -> bool:
    from exams.models import AttemptStatus, StudentAttempt

    return StudentAttempt.objects.filter(status=AttemptStatus.IN_PROGRESS, exam__is_active=True).exists()


# ---------------------------------------------------------------------------
# Restoring
# ---------------------------------------------------------------------------
def _models_to_clear():
    models = []
    for label in OUR_APPS:
        models += list(apps.get_app_config(label).get_models())
    return models + [Group]


def restore_backup(backup: Backup, *, by: str = "") -> Path:
    """
    Replace ALL current data with the backup. Saves a safety copy first and returns its path.
    Everyone (including the admin doing this) is signed out afterwards.
    """
    check_restorable(backup)
    safety = save_backup(create_backup(by=by or "system"), kind="before-restore")

    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="cbt_restore_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(backup.data)
        with transaction.atomic():
            for model in _models_to_clear():
                model._base_manager.all().delete()
            call_command("loaddata", tmp, verbosity=0)
            from accounts.models import User

            User.objects.update(active_session_key="")
            Session.objects.all().delete()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    media_root = Path(settings.MEDIA_ROOT)
    for rel, content in backup.media:
        target = (media_root / rel).resolve()
        if media_root.resolve() not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    cache.clear()
    logger.warning("Backup restored by %s (backup from %s, safety copy %s)",
                   by, backup.manifest.get("created_at"), safety.name)
    return safety


# ---------------------------------------------------------------------------
# Copies kept on this computer
# ---------------------------------------------------------------------------
def backup_dir() -> Path:
    d = Path(settings.CBT_BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def save_backup(content: bytes, *, kind: str) -> Path:
    """Keep a copy in the backup folder (never served over the web)."""
    assert kind in KINDS
    d = backup_dir()
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    path, n = d / f"cbt_backup_{stamp}_{kind}{EXTENSION}", 1
    while path.exists():  # two copies in the same second
        path, n = d / f"cbt_backup_{stamp}-{n}_{kind}{EXTENSION}", n + 1
    path.write_bytes(content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    prune()
    return path


def saved_path(name: str) -> Path:
    """Path of a saved copy, or BackupError. Only plain names made by this app are accepted."""
    if not NAME_RE.match(name or ""):
        raise BackupError("Unknown backup file.")
    path = backup_dir() / name
    if not path.is_file():
        raise BackupError("That backup file no longer exists.")
    return path


def list_saved() -> list[dict]:
    rows = []
    for p in backup_dir().glob(f"cbt_backup_*{EXTENSION}"):
        m = NAME_RE.match(p.name)
        if not m:
            continue
        stat = p.stat()
        with p.open("rb") as fh:
            head = fh.read(len(MAGIC))
        kind = m.group("kind")
        rows.append({
            "name": p.name,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone()),
            "encrypted": is_encrypted(head),
            "kind": kind,
            "kind_label": KINDS.get(kind, kind),
        })
    rows.sort(key=lambda r: r["name"], reverse=True)
    return rows


def prune(keep: int | None = None) -> int:
    """Delete the oldest saved copies beyond the limit. Safety copies count like the others."""
    keep = settings.CBT_BACKUP_KEEP if keep is None else keep
    rows = list_saved()
    removed = 0
    for row in rows[keep:]:
        try:
            (backup_dir() / row["name"]).unlink()
            removed += 1
        except OSError:
            pass
    return removed
