"""
Prepare a clean load test: N test students + a fresh active exam.

    python manage.py prepare_loadtest --students 75 --questions 30

Creates (or reuses) students LOAD001..LOADnnn in class LOADTEST with the
password below, deletes the previous "Load Test" exam and its attempts,
creates a new active exam, unlocks the test students' sessions, and prints
the exam id for locustfile.py.

Only touches the LOADTEST class and its own exam — real classes are never
modified. Remove everything afterwards with --cleanup.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Role, StudentProfile, User
from exams.models import Exam, Question

LOAD_CLASS = "LOADTEST"
LOAD_PREFIX = "LOAD"
LOAD_PASSWORD = "loadtest123"
EXAM_TITLE = "Load Test Exam"


class Command(BaseCommand):
    help = "Create load-test students and a fresh active exam (class LOADTEST)."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=75)
        parser.add_argument("--questions", type=int, default=30)
        parser.add_argument("--duration", type=int, default=30, help="Exam minutes")
        parser.add_argument("--cleanup", action="store_true", help="Delete all load-test data and exit")

    @transaction.atomic
    def handle(self, *args, **opts):
        Exam.objects.filter(title=EXAM_TITLE, target_class=LOAD_CLASS).delete()
        if opts["cleanup"]:
            deleted, _ = User.objects.filter(role=Role.STUDENT, student_profile__batch_or_class=LOAD_CLASS).delete()
            self.stdout.write(self.style.SUCCESS(f"Load-test data removed ({deleted} rows)."))
            return

        for i in range(1, opts["students"] + 1):
            username = f"{LOAD_PREFIX}{i:03d}"
            user, created = User.objects.get_or_create(username=username, defaults={"role": Role.STUDENT})
            if created:
                user.set_password(LOAD_PASSWORD)
                user.save()
            else:
                user.clear_session(delete_session_row=True)
            StudentProfile.objects.update_or_create(
                user=user,
                defaults={"full_name": f"Load Tester {i}", "batch_or_class": LOAD_CLASS,
                          "roll_number": str(i), "initial_password": LOAD_PASSWORD},
            )

        exam = Exam.objects.create(
            title=EXAM_TITLE, subject="Load testing", target_class=LOAD_CLASS,
            duration_minutes=opts["duration"], positive_marks=Decimal("4"), negative_marks=Decimal("1"),
        )
        Question.objects.bulk_create([
            Question(exam=exam, sort_order=n, topic_tag=f"Topic {n % 5 + 1}",
                     question_text=rf"Load question {n}: evaluate $\int_0^{{{n}}} x\,dx$.",
                     option_a=f"${n * n / 2:g}$", option_b=f"${n * n:g}$", option_c=f"${n:g}$", option_d=f"${2 * n:g}$",
                     correct_option="A")
            for n in range(1, opts["questions"] + 1)
        ])
        exam.is_active = True
        exam.save()

        self.stdout.write(self.style.SUCCESS(
            f"Ready: exam id {exam.pk}, {opts['questions']} questions, students "
            f"{LOAD_PREFIX}001..{LOAD_PREFIX}{opts['students']:03d} (password: {LOAD_PASSWORD})."
        ))
        self.stdout.write(
            f"Run:  locust -f loadtest/locustfile.py --host http://SERVER-IP --headless "
            f"--users {opts['students']} --spawn-rate 15 --run-time 6m  (CBT_EXAM_ID={exam.pk})"
        )
