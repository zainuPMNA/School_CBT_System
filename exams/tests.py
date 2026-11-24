from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Role, StudentProfile, User

from .models import (
    AnswerStatus,
    AttemptStatus,
    Exam,
    ExamNotReadyError,
    Question,
    StudentAnswer,
    StudentAttempt,
    ViolationLog,
    ViolationType,
)


class AttemptModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user(username="ADM001", password="kite4821", role=Role.STUDENT)
        StudentProfile.objects.create(user=cls.student, full_name="Asha  Rao", batch_or_class="xii a", roll_number="1")
        cls.exam = Exam.objects.create(
            title="Weekly Physics 1", subject="Physics", target_class="XII A",
            duration_minutes=30, positive_marks=Decimal("4"), negative_marks=Decimal("1"), is_active=True,
        )
        cls.questions = [
            Question.objects.create(
                exam=cls.exam, sort_order=i, question_text=f"Q{i} $E=mc^2$",
                option_a="a", option_b="b", option_c="c", option_d="d", correct_option="A",
            )
            for i in range(1, 6)
        ]

    def test_class_normalisation_and_availability(self):
        self.assertEqual(self.student.student_profile.batch_or_class, "XII A")
        self.assertIn(self.exam, Exam.objects.available_to(self.student))
        self.assertFalse(self.student.is_staff)

    def test_start_is_idempotent_and_persists_shuffle(self):
        a1, created1 = StudentAttempt.start_or_resume(self.student, self.exam)
        a2, created2 = StudentAttempt.start_or_resume(self.student, self.exam)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(a1.pk, a2.pk)
        self.assertEqual(a1.question_order, a2.question_order)
        self.assertEqual(sorted(a1.question_order), sorted(q.id for q in self.questions))
        for q in self.questions:
            self.assertEqual(sorted(a1.option_orders[str(q.id)]), ["A", "B", "C", "D"])
        self.assertEqual(a1.answers.count(), 5)
        self.assertTrue(all(a.status == AnswerStatus.NOT_VISITED for a in a1.answers.all()))

    def test_inactive_exam_cannot_start(self):
        self.exam.is_active = False
        self.exam.save()
        with self.assertRaises(ExamNotReadyError):
            StudentAttempt.start_or_resume(self.student, self.exam)

    def test_answer_status_normalisation(self):
        attempt, _ = StudentAttempt.start_or_resume(self.student, self.exam)
        ans = attempt.answers.first()
        ans.selected_option = "b"
        ans.status = AnswerStatus.MARKED_REVIEW
        ans.save()
        self.assertEqual((ans.selected_option, ans.status), ("B", AnswerStatus.ANSWERED_MARKED))
        ans.selected_option = None
        ans.save(update_fields=["selected_option"])
        ans.refresh_from_db()
        self.assertEqual(ans.status, AnswerStatus.MARKED_REVIEW)

    def test_scoring_and_single_submit(self):
        attempt, _ = StudentAttempt.start_or_resume(self.student, self.exam)
        answers = {a.question_id: a for a in attempt.answers.all()}
        picks = ["A", "A", "C", None, "D"]  # 2 correct, 2 wrong, 1 skipped
        for q, pick in zip(self.questions, picks):
            a = answers[q.id]
            a.selected_option = pick
            a.status = AnswerStatus.ANSWERED if pick else AnswerStatus.NOT_ANSWERED
            a.save()
        self.assertTrue(attempt.submit())
        self.assertFalse(attempt.submit())  # idempotent
        self.assertEqual(attempt.status, AttemptStatus.SUBMITTED)
        self.assertEqual((attempt.correct_count, attempt.wrong_count, attempt.attempted_count), (2, 2, 4))
        self.assertEqual(attempt.total_score, Decimal("6.00"))
        self.assertEqual(attempt.accuracy_percent, Decimal("50.00"))

    def test_timer_and_extra_time(self):
        attempt, _ = StudentAttempt.start_or_resume(self.student, self.exam)
        now = attempt.started_at + timedelta(minutes=10)
        self.assertEqual(attempt.remaining_seconds(now), 20 * 60)
        attempt.add_extra_time(5)
        self.assertEqual(attempt.remaining_seconds(now), 25 * 60)
        self.assertTrue(attempt.is_time_over(attempt.started_at + timedelta(minutes=36)))

    def test_violation_counter(self):
        attempt, _ = StudentAttempt.start_or_resume(self.student, self.exam)
        ViolationLog.record(attempt, ViolationType.TAB_SWITCH, "hidden")
        ViolationLog.record(attempt, "BOGUS", "x")
        attempt.refresh_from_db()
        self.assertEqual(attempt.violation_count, 2)
        self.assertEqual(attempt.violations.filter(violation_type=ViolationType.OTHER).count(), 1)

    def test_publish_auto_submits_open_attempts(self):
        attempt, _ = StudentAttempt.start_or_resume(self.student, self.exam)
        finalized = self.exam.publish_results()
        attempt.refresh_from_db()
        self.exam.refresh_from_db()
        self.assertEqual(finalized, 1)
        self.assertEqual(attempt.status, AttemptStatus.AUTO_SUBMITTED)
        self.assertTrue(self.exam.results_published)
        self.assertFalse(self.exam.is_active)


class SessionLockTests(TestCase):
    def test_unlock_clears_key_and_logs(self):
        from django.contrib.sessions.backends.db import SessionStore

        user = User.objects.create_user(username="ADM002", password="x", role=Role.STUDENT)
        store = SessionStore()
        store["k"] = "v"
        store.create()
        user.bind_session(store.session_key, "10.0.0.5")
        self.assertTrue(user.has_live_session())
        admin_user = User.objects.create_superuser("boss", password="pw")
        self.assertEqual(admin_user.role, Role.ADMIN)
        user.unlock_session(performed_by=admin_user)
        user.refresh_from_db()
        self.assertEqual(user.active_session_key, "")
        self.assertFalse(user.has_live_session())
        self.assertEqual(user.login_events.get().event_type, "ADMIN_UNLOCK")
