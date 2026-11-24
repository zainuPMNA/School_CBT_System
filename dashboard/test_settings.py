import io
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from accounts.models import Role, StudentProfile, User
from exams.models import Exam, Question, StudentAttempt

from .models import SiteSettings, institution_name

PW = "kitemu47"


@override_settings(CBT_SCHOOL_NAME="Env School", CBT_SERVER_URL="http://10.0.0.5/")
class SiteSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username="admin1", password=PW, role=Role.ADMIN)
        self.teacher = User.objects.create_user(username="teacher1", password=PW, role=Role.TEACHER)

    def client_for(self, user):
        c = Client(REMOTE_ADDR="10.3.0.1")
        c.post(reverse("accounts:login"), {"username": user.username, "password": PW})
        return c

    def test_defaults_come_from_env(self):
        site = SiteSettings.load()
        self.assertEqual(site.display_name, "Env School")
        self.assertEqual(site.server_url, "http://10.0.0.5/")
        self.assertContains(Client().get(reverse("accounts:login")), "Env School")

    def test_admin_changes_name_everywhere(self):
        c = self.client_for(self.admin)
        self.assertEqual(c.get(reverse("dashboard:settings")).status_code, 200)
        r = c.post(reverse("dashboard:settings"), {
            "institution_name": "  St. Mary's  School & College ",
            "tagline": "Weekly Tests",
            "login_slip_url": "192.168.5.20",
        })
        self.assertRedirects(r, reverse("dashboard:settings"))
        site = SiteSettings.load()
        self.assertEqual(site.institution_name, "St. Mary's School & College")
        self.assertEqual(site.login_slip_url, "http://192.168.5.20/")
        self.assertEqual(site.updated_by, self.admin)
        self.assertEqual(SiteSettings.objects.count(), 1)

        login_page = Client().get(reverse("accounts:login"))
        self.assertContains(login_page, "St. Mary&#x27;s School &amp; College")
        self.assertContains(login_page, "Weekly Tests")
        self.assertContains(c.get(reverse("dashboard:home")), "http://192.168.5.20/")
        self.assertEqual(institution_name(), "St. Mary's School & College")
        admin_page = c.get(reverse("admin:index"))
        self.assertContains(admin_page, "St. Mary&#x27;s School &amp; College — CBT Administration")

    def test_slips_and_exports_use_new_name(self):
        SiteSettings.load()
        site = SiteSettings.objects.get(pk=1)
        site.institution_name = "Greenfield <Academy> & Co"
        site.login_slip_url = "http://192.168.9.9/"
        site.save()

        student = User.objects.create_user(username="S1", password=PW, role=Role.STUDENT)
        StudentProfile.objects.create(user=student, full_name="Stu", batch_or_class="X", roll_number="1", initial_password="abc12345")
        exam = Exam.objects.create(title="T & <Q>", subject="Sci & Tech", target_class="X", duration_minutes=10,
                                   positive_marks=Decimal("4"), negative_marks=Decimal("1"), is_active=True)
        Question.objects.create(exam=exam, question_text="q", option_a="a", option_b="b", option_c="c", option_d="d",
                                correct_option="A", topic_tag="Work, Energy & <Power>")
        attempt, _ = StudentAttempt.start_or_resume(student, exam)
        attempt.submit()

        c = self.client_for(self.admin)
        slips = c.get(reverse("dashboard:login_slips") + "?class=X")
        self.assertContains(slips, "Greenfield &lt;Academy&gt; &amp; Co · CBT Login")
        self.assertContains(slips, "http://192.168.9.9/")

        xlsx = c.get(reverse("dashboard:export_results", args=[exam.pk, "xlsx"]))
        title = load_workbook(io.BytesIO(xlsx.content))["Rank List"]["A1"].value
        self.assertTrue(title.startswith("Greenfield <Academy> & Co"))

        # Names and topics with & and < must not break the PDF.
        pdf = c.get(reverse("dashboard:scorecards_pdf", args=[exam.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_blank_name_rejected_and_teacher_forbidden(self):
        c = self.client_for(self.admin)
        r = c.post(reverse("dashboard:settings"), {"institution_name": "   ", "tagline": "", "login_slip_url": ""})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "field-error")
        self.assertEqual(self.client_for(self.teacher).get(reverse("dashboard:settings")).status_code, 403)

    def test_blank_slip_url_falls_back_to_env(self):
        site = SiteSettings.load()
        site.login_slip_url = ""
        site.save()
        self.assertEqual(SiteSettings.load().server_url, "http://10.0.0.5/")
