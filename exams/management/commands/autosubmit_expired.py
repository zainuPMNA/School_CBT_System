"""
Auto-submit attempts whose server deadline has passed but whose browser never
came back (PC switched off, student walked away). Safe to run every minute:

    python manage.py autosubmit_expired

Phase 5 installs a systemd timer for it. Attempts are also finalised lazily on
any request from the student and when results are published.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from exams.models import StudentAttempt


class Command(BaseCommand):
    help = "Auto-submit IN_PROGRESS attempts that are past their deadline (+grace)."

    def handle(self, *args, **options):
        now = timezone.now()
        finalized = 0
        for attempt in StudentAttempt.objects.in_progress().select_related("exam"):
            if attempt.is_time_over(now) and attempt.submit(auto=True, now=now):
                finalized += 1
        self.stdout.write(f"{finalized} expired attempt(s) auto-submitted.")
