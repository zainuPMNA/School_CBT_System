"""An exam can target several classes, e.g. 'X-A, X-B'."""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Role, StudentProfile, User
from accounts.utils import parse_class_list

from .analytics import ranked_results
from .models import Exam, Question, StudentAttempt

PW = "Kp7wQm2xRt"


def student(username, cls, roll):
    u = User.objects.create_user(username=username, password=PW, role=Role.STUDENT)
    StudentProfile.objects.create(user=u, full_name=f"Student {username}", batch_or_class=cls, roll_number=str(roll))
    return u


class MultiClassExamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = student("A1", "X-A", 1)
        cls.b = student("B1", "X-B", 1)
        cls.c = student("C1", "X-C", 1)
        cls.ab = student("AB1", "X-AB", 1)   # must NOT match "X-A"
        cls.exam = Exam.objects.create(title="Joint test", subject="GK", target_class=" x-a, X-B ; x-a ",
                                       duration_minutes=10, positive_marks=Decimal("1"), negative_marks=Decimal("0"),
                                       is_active=True)
        Question.objects.create(exam=cls.exam, question_text="q", option_a="a", option_b="b", option_c="c",
                                option_d="d", correct_option="A")

    def test_stored_normalised(self):
        self.assertEqual(self.exam.target_class, "X-A, X-B")
        self.assertEqual(self.exam.target_classes, ["X-A", "X-B"])
        self.assertEqual(parse_class_list("xi, XI;xii\nX-A"), ["XI", "XII", "X-A"])

    def test_who_can_see_and_start(self):
        self.assertIn(self.exam, Exam.objects.available_to(self.a))
        self.assertIn(self.exam, Exam.objects.available_to(self.b))
        self.assertNotIn(self.exam, Exam.objects.available_to(self.c))
        self.assertNotIn(self.exam, Exam.objects.available_to(self.ab))  # no prefix matching
        for user, allowed in ((self.a, True), (self.b, True), (self.c, False)):
            c = Client(REMOTE_ADDR=f"10.8.0.{user.pk}")
            c.post(reverse("accounts:login"), {"username": user.username, "password": PW})
            c.post(reverse("cbt:start", args=[self.exam.pk]))
            self.assertEqual(StudentAttempt.objects.filter(student=user).exists(), allowed, user.username)

    def test_single_class_exams_still_work(self):
        single = Exam.objects.create(title="Solo", subject="S", target_class="x-c", duration_minutes=5, is_active=True)
        self.assertEqual(single.target_class, "X-C")
        self.assertEqual(list(Exam.objects.available_to(self.c)), [single])

    def test_results_and_monitor_cover_all_target_classes(self):
        absent = {r["username"] for r in ranked_results(self.exam).absent}
        self.assertEqual(absent, {"A1", "B1"})
        admin = User.objects.create_user(username="admin1", password=PW, role=Role.ADMIN)
        c = Client(REMOTE_ADDR="10.8.1.1")
        c.post(reverse("accounts:login"), {"username": admin.username, "password": PW})
        rows = c.get(reverse("dashboard:monitor_exam", args=[self.exam.pk])).context["rows"]
        self.assertEqual({r["student"].username for r in rows}, {"A1", "B1"})

    def test_form_accepts_several_classes_and_list_filter(self):
        admin = User.objects.create_user(username="admin2", password=PW, role=Role.ADMIN)
        c = Client(REMOTE_ADDR="10.8.1.2")
        c.post(reverse("accounts:login"), {"username": admin.username, "password": PW})
        page = c.get(reverse("dashboard:exam_create"))
        self.assertContains(page, "classPicker")
        for cls in ("X-A", "X-B", "X-C"):
            self.assertContains(page, cls)
        c.post(reverse("dashboard:exam_create"), {"title": "Weekly", "subject": "Maths", "target_class": "x-b,x-c",
                                                  "duration_minutes": 30, "positive_marks": "4", "negative_marks": "1"})
        self.assertEqual(Exam.objects.get(title="Weekly").target_class, "X-B, X-C")
        r = c.post(reverse("dashboard:exam_create"), {"title": "Empty", "subject": "M", "target_class": " , ;",
                                                      "duration_minutes": 30, "positive_marks": "4", "negative_marks": "1"})
        self.assertContains(r, "at least one class")
        listed = c.get(reverse("dashboard:exam_list") + "?class=X-C").context["exams"]
        self.assertEqual({e.title for e in listed}, {"Weekly"})
