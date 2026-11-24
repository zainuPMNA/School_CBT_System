"""
Create demo data for local testing and load tests.

    python manage.py seed_demo            # idempotent
    python manage.py seed_demo --students 75

Demo accounts (development / load-test ONLY — never run on the real lab server
with real students):
    admin    / demo-admin-123   (ADMIN)
    teacher  / demo-teach-123   (TEACHER)
    DEMO001.. / demo1234        (STUDENTS, class DEMO-XII)
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Role, StudentProfile, User
from dashboard.sample_files import QUESTION_EXAMPLES
from exams.models import Exam, Question

ADMIN_PASSWORD = "demo-admin-123"
TEACHER_PASSWORD = "demo-teach-123"
STUDENT_PASSWORD = "demo1234"
DEMO_CLASS = "DEMO-XII"

FIRST = ["Asha", "Rahul", "Fatima", "Arjun", "Meera", "Kabir", "Ishita", "Rohan", "Sana", "Vikram"]
LAST = ["Rao", "Verma", "Shaikh", "Iyer", "Nair", "Singh", "Das", "Menon", "Khan", "Patel"]


class Command(BaseCommand):
    help = "Create demo admin/teacher/students and an active sample exam."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=10)

    @transaction.atomic
    def handle(self, *args, **options):
        admin, _ = User.objects.get_or_create(username="admin", defaults={"role": Role.ADMIN, "is_superuser": True})
        admin.is_superuser = True
        admin.set_password(ADMIN_PASSWORD)
        admin.save()

        teacher, _ = User.objects.get_or_create(username="teacher", defaults={"role": Role.TEACHER, "first_name": "Demo", "last_name": "Teacher"})
        teacher.set_password(TEACHER_PASSWORD)
        teacher.save()

        for i in range(1, options["students"] + 1):
            username = f"DEMO{i:03d}"
            user, created = User.objects.get_or_create(username=username, defaults={"role": Role.STUDENT})
            if created:
                user.set_password(STUDENT_PASSWORD)
                user.save()
            StudentProfile.objects.update_or_create(
                user=user,
                defaults={
                    "full_name": f"{FIRST[(i - 1) % 10]} {LAST[((i - 1) // 10) % 10]}",
                    "batch_or_class": DEMO_CLASS,
                    "roll_number": str(i),
                    "initial_password": STUDENT_PASSWORD,
                },
            )

        exam, created = Exam.objects.get_or_create(
            title="Demo Weekly Test — PCM",
            defaults={
                "subject": "Physics / Chemistry / Maths",
                "target_class": DEMO_CLASS,
                "duration_minutes": 30,
                "instructions": "Each question carries +4 marks; −1 for a wrong answer.\n"
                "Use Save & Next to move on. Your answers are saved automatically.",
                "created_by": teacher,
            },
        )
        if created:
            for row in QUESTION_EXAMPLES:
                order, text, a, b, c, d, correct, explanation, topic = row
                Question.objects.create(
                    exam=exam, sort_order=int(order), question_text=text, option_a=a, option_b=b,
                    option_c=c, option_d=d, correct_option=correct, explanation=explanation, topic_tag=topic,
                )
            exam.is_active = True
            exam.save()

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready: admin, teacher, {options['students']} students in {DEMO_CLASS}, exam #{exam.pk}. "
            "Passwords are listed in dashboard/management/commands/seed_demo.py."
        ))
