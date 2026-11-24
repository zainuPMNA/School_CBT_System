"""Tests for v1.1: unpredictable passwords, About page, school logo."""
import io
import os
import re
import shutil
import tempfile
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import Role, StudentProfile, User
from accounts.utils import generate_readable_password, student_password_problem
from exams.models import Exam, Question, StudentAttempt

from .models import SiteSettings

PW = "Kp7wQm2xRt"
MEDIA = tempfile.mkdtemp(prefix="cbt-test-logo-")


def png_bytes(w=120, h=60, colour="navy"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, format="PNG")
    return buf.getvalue()


class PasswordTests(TestCase):
    def test_generated_passwords_are_mixed_random_and_unique(self):
        pws = {generate_readable_password() for _ in range(500)}
        self.assertEqual(len(pws), 500)  # no repeats
        for pw in list(pws)[:100]:
            self.assertEqual(len(pw), 10)
            self.assertRegex(pw, r"[a-z]")
            self.assertRegex(pw, r"[A-Z]")
            self.assertRegex(pw, r"[2-9]")
            self.assertNotRegex(pw, r"[01OoIl]")  # no look-alike characters
            self.assertIsNone(student_password_problem(pw, "2024001", "12", "Asha Rao"))
        # Not the old fixed "letters then digits" pattern.
        self.assertLess(sum(bool(re.fullmatch(r"[a-zA-Z]+[0-9]+", p)) for p in pws), 50)

    def test_predictable_passwords_rejected(self):
        cases = {
            "12345": "at least 6",
            "15082009": "only numbers",      # date of birth
            "9876543210": "only numbers",    # phone number
            "x2024001y": "admission number",
            "roll17abc": "roll number",
            "asharao99": "name",
            "aaaaaa": "repetitive",
            "abcdefgh": "sequence",
            "qwertyui": "sequence",
            "password1": "common",
        }
        for pw, expected in cases.items():
            with self.subTest(pw=pw):
                problem = student_password_problem(pw, "2024001", "17", "Asha Rao")
                self.assertIsNotNone(problem)
                self.assertIn(expected, problem)

    def test_upload_and_form_refuse_weak_passwords(self):
        User.objects.create_user(username="admin1", password=PW, role=Role.ADMIN)
        c = Client(REMOTE_ADDR="10.7.0.1")
        c.post(reverse("accounts:login"), {"username": "admin1", "password": PW})
        csv_data = "admission_no,full_name,class_name,roll_no,password\nA77,Ravi Kumar,X,1,123456\nA78,Sita Devi,X,2,\n"
        r = c.post(reverse("dashboard:student_upload"), {"file": SimpleUploadedFile("s.csv", csv_data.encode())})
        self.assertContains(r, "only numbers")
        self.assertFalse(User.objects.filter(username__in=["A77", "A78"]).exists())  # all-or-nothing
        r = c.post(reverse("dashboard:student_create"), {"admission_no": "A79", "full_name": "Ravi Kumar",
                                                         "class_name": "X", "password": "ravi2024"})
        self.assertContains(r, "must not contain the student")
        c.post(reverse("dashboard:student_create"), {"admission_no": "A80", "full_name": "Ravi Kumar", "class_name": "X"})
        self.assertEqual(len(StudentProfile.objects.get(user__username="A80").initial_password), 10)


class AboutPageTests(TestCase):
    def test_about_is_public_and_shows_developer(self):
        r = Client().get(reverse("accounts:about"))
        self.assertEqual(r.status_code, 200)
        for text in ("Zainulabid", "Perinthalmanna", "Academic Coordinator",
                     "YES P.A. Inamdar English School and Junior College", "mailto:zainulabidpmna04@gmail.com"):
            self.assertContains(r, text)
        self.assertContains(Client().get(reverse("accounts:login")), reverse("accounts:about"))


@override_settings(MEDIA_ROOT=MEDIA)
class LogoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        User.objects.create_user(username="admin1", password=PW, role=Role.ADMIN)
        self.c = Client(REMOTE_ADDR="10.7.0.2")
        self.c.post(reverse("accounts:login"), {"username": "admin1", "password": PW})

    def save(self, **extra):
        data = {"institution_name": "P.A. Inamdar School", "tagline": "", "login_slip_url": "", **extra}
        return self.c.post(reverse("dashboard:settings"), data)

    def test_upload_shows_everywhere_then_replace_and_remove(self):
        r = self.save(logo=SimpleUploadedFile("logo.png", png_bytes(), "image/png"))
        self.assertRedirects(r, reverse("dashboard:settings"))
        site = SiteSettings.load()
        self.assertTrue(site.logo.name.startswith("branding/logo_"))
        first_path = site.logo.path
        self.assertContains(Client().get(reverse("accounts:login")), site.logo.url)
        self.assertContains(self.c.get(reverse("dashboard:home")), site.logo.url)

        self.save()  # saving other fields keeps the logo
        self.assertEqual(SiteSettings.load().logo.name, site.logo.name)

        with self.captureOnCommitCallbacks(execute=True):  # replacing deletes the old file
            self.save(logo=SimpleUploadedFile("new.png", png_bytes(colour="red"), "image/png"))
        self.assertNotEqual(SiteSettings.load().logo.name, site.logo.name)
        self.assertFalse(os.path.exists(first_path))

        with self.captureOnCommitCallbacks(execute=True):  # removing falls back to the CBT badge
            self.save(remove_logo="on")
        self.assertFalse(SiteSettings.load().logo)
        self.assertNotContains(Client().get(reverse("accounts:login")), "branding/logo_")

    def test_rejects_non_images(self):
        r = self.save(logo=SimpleUploadedFile("logo.png", b"not really a picture", "image/png"))
        self.assertContains(r, "field-error")
        r = self.save(logo=SimpleUploadedFile("logo.svg", b"<svg></svg>", "image/svg+xml"))
        self.assertContains(r, "field-error")
        self.assertFalse(SiteSettings.load().logo)

    def test_scorecard_pdf_with_logo(self):
        self.save(logo=SimpleUploadedFile("logo.png", png_bytes(300, 300), "image/png"))
        student = User.objects.create_user(username="S1", password=PW, role=Role.STUDENT)
        StudentProfile.objects.create(user=student, full_name="Stu", batch_or_class="X", roll_number="1")
        exam = Exam.objects.create(title="T", subject="S", target_class="X", duration_minutes=10,
                                   positive_marks=Decimal("1"), negative_marks=Decimal("0"), is_active=True)
        Question.objects.create(exam=exam, question_text="q", option_a="a", option_b="b", option_c="c",
                                option_d="d", correct_option="A")
        attempt, _ = StudentAttempt.start_or_resume(student, exam)
        attempt.submit()
        pdf = self.c.get(reverse("dashboard:scorecards_pdf", args=[exam.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn(b"/Image", pdf.content)  # the logo is embedded
