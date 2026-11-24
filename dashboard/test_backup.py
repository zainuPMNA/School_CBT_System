"""Backup & restore: file format, encryption, safety checks and the admin pages."""
import shutil
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, StudentProfile, User
from exams.models import Exam, Question, StudentAttempt

from . import backup as bk
from .models import SiteSettings

ADMIN_PW = "Admin-Pass-2026"
BACKUP_PW = "pendrive-secret-1"


class BackupTestBase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbt_bk_test_"))
        (self.tmp / "media" / "branding").mkdir(parents=True)
        (self.tmp / "media" / "branding" / "logo.png").write_bytes(b"\x89PNG fake logo")
        self.override = override_settings(CBT_BACKUP_DIR=self.tmp / "backups", MEDIA_ROOT=self.tmp / "media")
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.admin = User.objects.create_user("bk_admin", password=ADMIN_PW, role=Role.ADMIN)
        self.teacher = User.objects.create_user("bk_teacher", password="Teach-Pass-2026", role=Role.TEACHER)
        self.student = User.objects.create_user("S001", password="Kp7wQm2xRt", role=Role.STUDENT)
        StudentProfile.objects.create(user=self.student, full_name="Asha Rao", batch_or_class="X-A", roll_number="1")
        self.exam = Exam.objects.create(title="Unit test 1", subject="Maths", target_class="X-A", duration_minutes=10,
                                        positive_marks=Decimal("4"), negative_marks=Decimal("1"))
        Question.objects.create(exam=self.exam, question_text="2+2?", option_a="3", option_b="4", option_c="5",
                                option_d="6", correct_option="B")


class BackupFileTests(BackupTestBase):
    def test_round_trip_with_password(self):
        blob = bk.create_backup(by="bk_admin", password=BACKUP_PW)
        self.assertTrue(bk.is_encrypted(blob))
        self.assertNotIn(b"Asha Rao", blob)  # encrypted: student names are not readable
        backup = bk.read_backup(blob, BACKUP_PW)
        self.assertEqual(backup.summary["counts"]["students"], 1)
        self.assertEqual(backup.summary["counts"]["exams"], 1)
        self.assertIn(("branding/logo.png", b"\x89PNG fake logo"), backup.media)

    def test_wrong_password_missing_password_and_tampering(self):
        blob = bk.create_backup(password=BACKUP_PW)
        with self.assertRaisesMessage(bk.BackupError, "Wrong backup password"):
            bk.read_backup(blob, "not-the-password")
        with self.assertRaisesMessage(bk.BackupError, "password-protected"):
            bk.read_backup(blob, "")
        tampered = blob[:-20] + bytes(20)
        with self.assertRaises(bk.BackupError):
            bk.read_backup(tampered, BACKUP_PW)

    def test_not_a_backup(self):
        with self.assertRaisesMessage(bk.BackupError, "not a CBT backup"):
            bk.read_backup(b"hello world")

    def test_restore_replaces_everything_and_keeps_safety_copy(self):
        blob = bk.create_backup(by="bk_admin")
        # Things change after the backup...
        self.student.delete()
        Exam.objects.create(title="Made later", subject="GK", target_class="X-A", duration_minutes=5)
        (self.tmp / "media" / "branding" / "logo.png").unlink()
        self.client.force_login(self.admin)
        self.assertTrue(Session.objects.exists())

        safety = bk.restore_backup(bk.read_backup(blob), by="bk_admin")

        self.assertTrue(User.objects.filter(username="S001", student_profile__full_name="Asha Rao").exists())
        self.assertEqual(list(Exam.objects.values_list("title", flat=True)), ["Unit test 1"])
        self.assertEqual(Question.objects.get().correct_option, "B")
        self.assertTrue((self.tmp / "media" / "branding" / "logo.png").exists())
        self.assertFalse(Session.objects.exists())  # everyone signs in again
        self.assertTrue(safety.exists())
        # The safety copy holds the data from just before the restore.
        before = bk.read_backup(safety.read_bytes())
        self.assertEqual(before.summary["counts"]["students"], 0)
        self.assertEqual(before.summary["counts"]["exams"], 2)
        # Passwords still work after a restore.
        self.assertTrue(User.objects.get(username="bk_admin").check_password(ADMIN_PW))

    def test_backup_without_admin_is_refused(self):
        User.objects.filter(role=Role.ADMIN).update(is_active=False)
        blob = bk.create_backup()
        User.objects.filter(role=Role.ADMIN).update(is_active=True)
        with self.assertRaisesMessage(bk.BackupError, "no active admin"):
            bk.check_restorable(bk.read_backup(blob))

    def test_backup_from_newer_app_is_refused(self):
        backup = bk.read_backup(bk.create_backup())
        backup.manifest["migrations"]["exams"].append("9999_from_the_future")
        with self.assertRaisesMessage(bk.BackupError, "newer version"):
            bk.check_restorable(backup)

    def test_unsafe_names_rejected(self):
        for bad in ("../settings.py", "..\\x.cbtbackup", "cbt_backup_x.cbtbackup", "/etc/passwd"):
            with self.assertRaises(bk.BackupError):
                bk.saved_path(bad)
        self.assertIsNone(bk._safe_media_name("media/../../evil.py"))
        self.assertEqual(bk._safe_media_name("media/questions/a.png"), "questions/a.png")

    @override_settings(CBT_BACKUP_KEEP=2)
    def test_old_copies_pruned(self):
        for _ in range(4):
            bk.save_backup(b"PK fake", kind="manual")
        self.assertEqual(len(bk.list_saved()), 2)

    def test_management_command(self):
        out = StringIO()
        call_command("cbt_backup", stdout=out)
        self.assertIn("Backup saved", out.getvalue())
        self.assertEqual(bk.list_saved()[0]["kind"], "auto")


class BackupPageTests(BackupTestBase):
    def test_admin_only(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse("dashboard:backup")).status_code, 403)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("dashboard:backup"))
        self.assertContains(r, "Download backup now")
        self.assertContains(r, 'href="/dashboard/backup/"')

    def test_download_with_password_and_keep_copy(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("dashboard:backup_create"),
                             {"password": BACKUP_PW, "password2": BACKUP_PW, "keep_copy": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment;", r["Content-Disposition"])
        self.assertIn(".cbtbackup", r["Content-Disposition"])
        self.assertTrue(bk.is_encrypted(r.content))
        saved = bk.list_saved()
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0]["encrypted"])

    def test_mismatched_passwords(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("dashboard:backup_create"), {"password": BACKUP_PW, "password2": "other-one-1"})
        self.assertContains(r, "do not match", status_code=400)
        self.assertEqual(bk.list_saved(), [])

    def test_upload_then_restore(self):
        blob = bk.create_backup(by="bk_admin", password=BACKUP_PW)
        Exam.objects.create(title="Made later", subject="GK", target_class="X-A", duration_minutes=5)
        self.client.force_login(self.admin)

        # Wrong password on upload: rejected, nothing kept.
        r = self.client.post(reverse("dashboard:backup_upload"),
                             {"file": SimpleUploadedFile("b.cbtbackup", blob), "password": "nope-nope"})
        self.assertContains(r, "Wrong backup password", status_code=400)
        self.assertEqual(bk.list_saved(), [])

        r = self.client.post(reverse("dashboard:backup_upload"),
                             {"file": SimpleUploadedFile("b.cbtbackup", blob), "password": BACKUP_PW})
        name = bk.list_saved()[0]["name"]
        restore_url = reverse("dashboard:backup_restore", args=[name])
        self.assertRedirects(r, restore_url)
        page = self.client.get(restore_url)
        self.assertContains(page, "password-protected")  # preview shown from the upload check
        self.assertContains(page, "bk_admin")
        self.assertEqual(Exam.objects.count(), 2)  # nothing changed yet

        # Wrong admin password: refused, data unchanged.
        r = self.client.post(restore_url, {"backup_password": BACKUP_PW, "admin_password": "wrong", "confirm": "RESTORE"})
        self.assertContains(r, "not your password", status_code=400)
        self.assertEqual(Exam.objects.count(), 2)

        r = self.client.post(restore_url, {"backup_password": BACKUP_PW, "admin_password": ADMIN_PW, "confirm": "RESTORE"})
        self.assertRedirects(r, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertEqual(list(Exam.objects.values_list("title", flat=True)), ["Unit test 1"])
        self.assertNotIn("_auth_user_id", self.client.session)  # signed out

    def test_restore_blocked_while_students_write(self):
        blob = bk.create_backup()
        name = bk.save_backup(blob, kind="manual").name
        Exam.objects.filter(pk=self.exam.pk).update(is_active=True)
        StudentAttempt.start_or_resume(self.student, Exam.objects.get(pk=self.exam.pk))
        self.client.force_login(self.admin)
        r = self.client.post(reverse("dashboard:backup_restore", args=[name]),
                             {"admin_password": ADMIN_PW, "confirm": "RESTORE"})
        self.assertContains(r, "writing a test right now", status_code=400)
        self.assertTrue(StudentAttempt.objects.exists())

    def test_delete_saved_copy(self):
        name = bk.save_backup(bk.create_backup(), kind="manual").name
        self.client.force_login(self.admin)
        self.client.post(reverse("dashboard:backup_delete", args=[name]))
        self.assertEqual(bk.list_saved(), [])

    def test_school_settings_survive(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"institution_name": "Original School"})
        blob = bk.create_backup()
        SiteSettings.objects.filter(pk=1).update(institution_name="Changed")
        bk.restore_backup(bk.read_backup(blob))
        self.assertEqual(SiteSettings.load().institution_name, "Original School")
