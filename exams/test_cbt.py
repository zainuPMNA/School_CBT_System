import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, StudentProfile, User

from .models import AnswerStatus, AttemptStatus, Exam, Question, StudentAttempt

PW = "kitemu47"


class CBTFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user(username="S1", password=PW, role=Role.STUDENT)
        StudentProfile.objects.create(user=cls.student, full_name="Stu One", batch_or_class="XII-A", roll_number="1")
        cls.other_class = User.objects.create_user(username="S2", password=PW, role=Role.STUDENT)
        StudentProfile.objects.create(user=cls.other_class, full_name="Other", batch_or_class="XI-B", roll_number="1")
        cls.exam = Exam.objects.create(
            title="Weekly", subject="Physics", target_class="XII-A", duration_minutes=20,
            positive_marks=Decimal("4"), negative_marks=Decimal("1"), is_active=True,
        )
        cls.questions = [
            Question.objects.create(exam=cls.exam, sort_order=i, question_text=f"Question {i} $x^{i}$",
                                    option_a=f"a{i}", option_b=f"b{i}", option_c=f"c{i}", option_d=f"d{i}",
                                    correct_option="ABCD"[i % 4])
            for i in range(1, 6)
        ]

    def login(self, user=None, ip="10.2.0.1"):
        client = Client(REMOTE_ADDR=ip)
        client.post(reverse("accounts:login"), {"username": (user or self.student).username, "password": PW})
        return client

    def start(self, client):
        return client.post(reverse("cbt:start", args=[self.exam.pk]))

    def save(self, client, payload):
        return client.post(reverse("cbt:api_save", args=[self.exam.pk]), json.dumps(payload),
                           content_type="application/json", HTTP_ACCEPT="application/json")

    def position_of(self, attempt, question, key):
        return attempt.display_index_of(question.pk, key)

    # ------------------------------------------------------------------
    def test_full_flow_shuffle_resume_save_submit(self):
        c = self.login()
        self.assertContains(c.get(reverse("accounts:student_home")), "Read instructions")
        self.assertEqual(c.get(reverse("cbt:instructions", args=[self.exam.pk])).status_code, 200)
        self.assertRedirects(self.start(c), reverse("cbt:take", args=[self.exam.pk]))

        page = c.get(reverse("cbt:take", args=[self.exam.pk]))
        self.assertEqual(page.status_code, 200)
        config = page.context["config"]
        attempt = StudentAttempt.objects.get(student=self.student)
        self.assertEqual([q["id"] for q in config["questions"]], attempt.question_order)
        # Answer key must never reach the browser.
        self.assertNotIn("correct_option", page.content.decode())

        # Answer every question correctly except the last (wrong) via on-screen positions.
        by_id = {q.pk: q for q in self.questions}
        for n, qid in enumerate(attempt.question_order):
            q = by_id[qid]
            key = q.correct_option if n < 4 else ("A" if q.correct_option != "A" else "B")
            r = self.save(c, {"question_id": qid, "selected": self.position_of(attempt, q, key),
                              "status": "ANSWERED", "current_index": n})
            self.assertEqual(r.status_code, 200, r.content)
            self.assertGreater(r.json()["remaining"], 1100)

        # Mark the first one; it stays evaluated (ANSWERED_MARKED).
        first = attempt.question_order[0]
        self.save(c, {"question_id": first, "selected": self.position_of(attempt, by_id[first], by_id[first].correct_option),
                      "status": "MARKED_REVIEW", "current_index": 0})

        # Resume on "another PC": same order, same selections, same position.
        attempt.refresh_from_db()
        self.assertEqual(attempt.last_question_index, 0)
        resumed = c.get(reverse("cbt:take", args=[self.exam.pk])).context["config"]
        self.assertEqual([q["id"] for q in resumed["questions"]], attempt.question_order)
        self.assertEqual(resumed["questions"][0]["status"], AnswerStatus.ANSWERED_MARKED)
        self.assertTrue(all(q["selected"] is not None for q in resumed["questions"]))

        r = c.post(reverse("cbt:submit", args=[self.exam.pk]), json.dumps({"auto": False, "answers": []}),
                   content_type="application/json")
        self.assertEqual(r.json()["redirect"], reverse("cbt:submitted", args=[self.exam.pk]))
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, AttemptStatus.SUBMITTED)
        self.assertEqual((attempt.correct_count, attempt.wrong_count), (4, 1))
        self.assertEqual(attempt.total_score, Decimal("15.00"))

        # Everything after submit is refused; the score is not shown before publishing.
        self.assertEqual(self.save(c, {"question_id": first, "selected": 0, "status": "ANSWERED"}).status_code, 409)
        done = c.get(reverse("cbt:submitted", args=[self.exam.pk]))
        self.assertContains(done, "Test Submitted Successfully")
        self.assertNotContains(done, "15.00")
        self.assertRedirects(self.start(c), reverse("cbt:submitted", args=[self.exam.pk]))

    def test_submit_applies_unsaved_answers(self):
        c = self.login()
        self.start(c)
        attempt = StudentAttempt.objects.get(student=self.student)
        q = self.questions[0]
        c.post(reverse("cbt:submit", args=[self.exam.pk]), json.dumps({"answers": [
            {"question_id": q.pk, "selected": self.position_of(attempt, q, q.correct_option), "status": "ANSWERED"},
            {"question_id": 999999, "selected": 0, "status": "ANSWERED"},
        ]}), content_type="application/json")
        attempt.refresh_from_db()
        self.assertEqual(attempt.correct_count, 1)

    def test_bad_payloads_rejected(self):
        c = self.login()
        self.start(c)
        qid = self.questions[0].pk
        for payload in (
            {"question_id": qid, "selected": 7, "status": "ANSWERED"},
            {"question_id": qid, "selected": 0, "status": "HACKED"},
            {"question_id": 424242, "selected": 0, "status": "ANSWERED"},
        ):
            self.assertEqual(self.save(c, payload).status_code, 400)

    def test_wrong_class_cannot_start(self):
        c = self.login(self.other_class, ip="10.2.0.9")
        self.assertRedirects(c.get(reverse("cbt:instructions", args=[self.exam.pk])), reverse("accounts:student_home"))
        self.start(c)
        self.assertFalse(StudentAttempt.objects.filter(student=self.other_class).exists())

    def test_server_timer_auto_submits_and_blocks_late_saves(self):
        c = self.login()
        self.start(c)
        StudentAttempt.objects.filter(student=self.student).update(started_at=timezone.now() - timedelta(minutes=21))
        r = self.save(c, {"question_id": self.questions[0].pk, "selected": 0, "status": "ANSWERED"})
        self.assertEqual(r.status_code, 409)
        attempt = StudentAttempt.objects.get(student=self.student)
        self.assertEqual(attempt.status, AttemptStatus.AUTO_SUBMITTED)
        self.assertEqual(attempt.time_taken_seconds, 20 * 60)

    def test_sync_reports_time_pause_and_extension(self):
        c = self.login()
        self.start(c)
        sync = c.get(reverse("cbt:api_sync", args=[self.exam.pk]) + "?pos=3").json()
        self.assertTrue(sync["exam_active"])
        self.assertGreater(sync["remaining"], 1190)
        attempt = StudentAttempt.objects.get(student=self.student)
        self.assertEqual(attempt.last_question_index, 3)

        attempt.add_extra_time(10)
        self.assertGreater(c.get(reverse("cbt:api_sync", args=[self.exam.pk])).json()["remaining"], 1790)

        Exam.objects.filter(pk=self.exam.pk).update(is_active=False)
        self.assertFalse(c.get(reverse("cbt:api_sync", args=[self.exam.pk])).json()["exam_active"])
        self.assertEqual(self.save(c, {"question_id": self.questions[0].pk, "selected": 0, "status": "ANSWERED"}).status_code, 423)

    def test_violations_logged_and_optional_limit(self):
        c = self.login()
        self.start(c)
        url = reverse("cbt:api_violation", args=[self.exam.pk])
        r = c.post(url, json.dumps({"type": "TAB_SWITCH", "details": "hidden"}), content_type="application/json")
        self.assertEqual(r.json()["violations"], 1)
        with override_settings(CBT_MAX_VIOLATIONS=2):
            r = c.post(url, json.dumps({"type": "FULLSCREEN_EXIT"}), content_type="application/json")
            self.assertEqual(r.status_code, 409)
        self.assertEqual(StudentAttempt.objects.get(student=self.student).status, AttemptStatus.AUTO_SUBMITTED)

    def test_concurrent_start_creates_one_attempt(self):
        c = self.login()
        self.start(c)
        self.start(c)
        self.assertEqual(StudentAttempt.objects.filter(student=self.student).count(), 1)

    def test_api_requires_csrf_header(self):
        c = Client(REMOTE_ADDR="10.2.0.5", enforce_csrf_checks=True)
        login_page = c.get(reverse("accounts:login"))
        token = login_page.cookies["cbt_csrftoken"].value
        c.post(reverse("accounts:login"), {"username": "S1", "password": PW, "csrfmiddlewaretoken": token})
        c.post(reverse("cbt:start", args=[self.exam.pk]), {"csrfmiddlewaretoken": c.cookies["cbt_csrftoken"].value})
        payload = json.dumps({"question_id": self.questions[0].pk, "selected": 0, "status": "ANSWERED"})
        url = reverse("cbt:api_save", args=[self.exam.pk])
        self.assertEqual(c.post(url, payload, content_type="application/json").status_code, 403)
        ok = c.post(url, payload, content_type="application/json", HTTP_X_CSRFTOKEN=c.cookies["cbt_csrftoken"].value)
        self.assertEqual(ok.status_code, 200)

    def test_staff_and_anonymous_blocked_from_api(self):
        self.assertEqual(Client().get(reverse("cbt:api_sync", args=[self.exam.pk])).status_code, 401)
        self.assertEqual(Client().get(reverse("cbt:take", args=[self.exam.pk])).status_code, 302)

    def test_autosubmit_command(self):
        c = self.login()
        self.start(c)
        StudentAttempt.objects.filter(student=self.student).update(started_at=timezone.now() - timedelta(hours=1))
        out = StringIO()
        call_command("autosubmit_expired", stdout=out)
        self.assertIn("1 expired", out.getvalue())
