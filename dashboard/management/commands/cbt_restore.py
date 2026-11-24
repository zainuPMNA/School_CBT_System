"""Restore a .cbtbackup file from the command line (when the web page cannot be reached).

    python manage.py cbt_restore path/to/file.cbtbackup [--yes]
A password-protected file asks for its password (or reads CBT_BACKUP_PASSWORD).
"""
import getpass
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from dashboard import backup as bk


class Command(BaseCommand):
    help = "Replace ALL current data with the data in a .cbtbackup file (a safety copy is saved first)."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    def handle(self, *args, **opts):
        path = Path(opts["file"])
        if not path.is_file():
            raise CommandError(f"No such file: {path}")
        blob = path.read_bytes()
        password = ""
        if bk.is_encrypted(blob):
            password = os.environ.get("CBT_BACKUP_PASSWORD") or getpass.getpass("Backup password: ")
        try:
            backup = bk.read_backup(blob, password)
            bk.check_restorable(backup)
        except bk.BackupError as exc:
            raise CommandError(str(exc)) from None
        s = backup.summary
        self.stdout.write(f"Backup of {s['school']} made {s['created_at']}: "
                          f"{s['counts'].get('students', 0)} students, {s['counts'].get('exams', 0)} exams, "
                          f"{s['counts'].get('attempts', 0)} attempts.")
        if bk.exam_running():
            raise CommandError("Students are writing a test right now. Restore after the test has ended.")
        if not opts["yes"] and input("This REPLACES all current data. Type RESTORE to continue: ").strip() != "RESTORE":
            raise CommandError("Cancelled. Nothing was changed.")
        safety = bk.restore_backup(backup, by="command line")
        self.stdout.write(self.style.SUCCESS(f"Restored. The previous data was kept as {safety}"))
