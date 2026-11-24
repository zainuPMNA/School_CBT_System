"""Save a full backup into CBT_BACKUP_DIR (used by the nightly job; also handy by hand).

    python manage.py cbt_backup                 # plain copy, kind "auto"
    python manage.py cbt_backup --password-env CBT_BACKUP_PASSWORD
"""
import os

from django.core.management.base import BaseCommand, CommandError

from dashboard import backup as bk


class Command(BaseCommand):
    help = "Save a complete CBT backup (.cbtbackup) in the backup folder."

    def add_arguments(self, parser):
        parser.add_argument("--password-env", default="",
                            help="Name of an environment variable holding a password to encrypt the file with.")
        parser.add_argument("--kind", default="auto", choices=sorted(bk.KINDS))

    def handle(self, *args, **opts):
        password = ""
        if opts["password_env"]:
            password = os.environ.get(opts["password_env"], "")
            if not password:
                raise CommandError(f"Environment variable {opts['password_env']} is empty.")
        path = bk.save_backup(bk.create_backup(by="system", password=password), kind=opts["kind"])
        self.stdout.write(self.style.SUCCESS(f"Backup saved: {path} ({path.stat().st_size // 1024} KB)"))
