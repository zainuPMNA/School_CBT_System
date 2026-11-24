"""
Live Lab Monitor: one row per student of the exam's class, refreshed by HTMX
every CBT_MONITOR_REFRESH_SECONDS. Actions (unlock, extend time, force submit)
return the refreshed table when called through HTMX.
"""

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from accounts.models import Role, User
from exams.models import (
    ANSWERED_STATUSES,
    AnswerStatus,
    AttemptStatus,
    Exam,
    StudentAttempt,
)

from .mixins import StaffRequiredMixin

MAX_EXTEND_MINUTES = 120


def _roll_sort_key(user):
    """Natural order for roll numbers: '2' before '10'."""
    profile = getattr(user, "student_profile", None)
    roll = profile.roll_number if profile else ""
    return (len(roll), roll, user.username)


def build_monitor(exam: Exam) -> dict:
    now = timezone.now()
    offline_after = max(30, settings.CBT_HEARTBEAT_INTERVAL_SECONDS * 3)
    attempts = {
        a.student_id: a
        for a in StudentAttempt.objects.filter(exam=exam)
        .select_related("exam")
        .annotate(
            answered=Count("answers", filter=Q(answers__status__in=ANSWERED_STATUSES)),
            marked=Count("answers", filter=Q(answers__status__in=[AnswerStatus.MARKED_REVIEW, AnswerStatus.ANSWERED_MARKED])),
            total=Count("answers"),
        )
    }
    students = list(
        User.objects.students()
        .filter(Q(student_profile__batch_or_class__in=exam.target_classes) | Q(pk__in=list(attempts)))
        .select_related("student_profile")
    )
    students.sort(key=_roll_sort_key)

    rows = []
    summary = {"total": len(students), "signed_in": 0, "writing": 0, "submitted": 0, "not_started": 0, "offline": 0, "violations": 0}
    for user in students:
        attempt = attempts.get(user.pk)
        signed_in = bool(user.active_session_key)
        summary["signed_in"] += signed_in
        row = {"student": user, "attempt": attempt, "signed_in": signed_in, "state": "not_started"}
        if attempt is None:
            summary["not_started"] += 1
        else:
            summary["violations"] += attempt.violation_count
            if attempt.is_finished:
                row["state"] = "submitted"
                summary["submitted"] += 1
            else:
                row["state"] = "writing"
                summary["writing"] += 1
                row["remaining"] = attempt.remaining_seconds(now)
                seen_ago = int((now - attempt.last_seen_at).total_seconds()) if attempt.last_seen_at else None
                row["seen_ago"] = seen_ago
                row["offline"] = seen_ago is None or seen_ago > offline_after
                row["time_over"] = attempt.is_time_over(now, grace_seconds=0)
                summary["offline"] += row["offline"]
        rows.append(row)
    return {"exam": exam, "rows": rows, "summary": summary, "now": now}


def _render_table(request, exam, flash=None):
    ctx = build_monitor(exam)
    ctx["flash"] = flash
    return render(request, "dashboard/monitor/_table.html", ctx)


def _respond(request, exam, flash, level=messages.SUCCESS):
    if request.headers.get("HX-Request"):
        return _render_table(request, exam, flash)
    messages.add_message(request, level, flash)
    return HttpResponseRedirect(reverse("dashboard:monitor_exam", args=[exam.pk]))


class MonitorIndexView(StaffRequiredMixin, TemplateView):
    template_name = "dashboard/monitor/index.html"
    nav_section = "monitor"

    def get(self, request, *args, **kwargs):
        active = list(Exam.objects.active().with_counts().order_by("-created_at"))
        if len(active) == 1:
            return redirect("dashboard:monitor_exam", pk=active[0].pk)
        return self.render_to_response(self.get_context_data(active_exams=active))


class MonitorExamView(StaffRequiredMixin, TemplateView):
    template_name = "dashboard/monitor/exam.html"
    nav_section = "monitor"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        exam = get_object_or_404(Exam, pk=self.kwargs["pk"])
        ctx.update(build_monitor(exam))
        ctx["refresh_seconds"] = settings.CBT_MONITOR_REFRESH_SECONDS
        return ctx


class MonitorTableView(StaffRequiredMixin, View):
    def get(self, request, pk):
        return _render_table(request, get_object_or_404(Exam, pk=pk))


class MonitorUnlockView(StaffRequiredMixin, View):
    def post(self, request, pk, student_pk):
        exam = get_object_or_404(Exam, pk=pk)
        student = get_object_or_404(User, pk=student_pk, role=Role.STUDENT)
        student.unlock_session(performed_by=request.user, reason=f"Unlocked from live monitor ({exam.title}).")
        return _respond(request, exam, f"{student.username} unlocked — they can sign in on any PC and resume.")


def _parse_minutes(request) -> int | None:
    try:
        minutes = int(request.POST.get("minutes", "5"))
    except ValueError:
        return None
    return minutes if 1 <= minutes <= MAX_EXTEND_MINUTES else None


class MonitorExtendView(StaffRequiredMixin, View):
    def post(self, request, pk, attempt_pk):
        exam = get_object_or_404(Exam, pk=pk)
        attempt = get_object_or_404(StudentAttempt.objects.select_related("student"), pk=attempt_pk, exam=exam)
        minutes = _parse_minutes(request)
        if minutes is None:
            return _respond(request, exam, f"Enter 1–{MAX_EXTEND_MINUTES} minutes.", messages.ERROR)
        if attempt.status != AttemptStatus.IN_PROGRESS:
            return _respond(request, exam, f"{attempt.student.username} has already submitted.", messages.ERROR)
        attempt.add_extra_time(minutes)
        return _respond(request, exam, f"+{minutes} min for {attempt.student.username}.")


class MonitorExtendAllView(StaffRequiredMixin, View):
    def post(self, request, pk):
        exam = get_object_or_404(Exam, pk=pk)
        minutes = _parse_minutes(request)
        if minutes is None:
            return _respond(request, exam, f"Enter 1–{MAX_EXTEND_MINUTES} minutes.", messages.ERROR)
        count = 0
        for attempt in StudentAttempt.objects.in_progress().filter(exam=exam):
            attempt.add_extra_time(minutes)
            count += 1
        return _respond(request, exam, f"+{minutes} min for all {count} student(s) still writing.")


class MonitorForceSubmitView(StaffRequiredMixin, View):
    def post(self, request, pk, attempt_pk):
        exam = get_object_or_404(Exam, pk=pk)
        attempt = get_object_or_404(StudentAttempt.objects.select_related("student", "exam"), pk=attempt_pk, exam=exam)
        if attempt.submit(auto=False, by=request.user):
            return _respond(request, exam, f"{attempt.student.username}'s test was force-submitted.")
        return _respond(request, exam, f"{attempt.student.username} had already submitted.", messages.INFO)


class MonitorForceSubmitAllView(StaffRequiredMixin, View):
    def post(self, request, pk):
        exam = get_object_or_404(Exam, pk=pk)
        count = exam.finalize_open_attempts(by=request.user)
        return _respond(request, exam, f"{count} attempt(s) submitted. End of exam.")


class MonitorViolationsView(StaffRequiredMixin, View):
    def get(self, request, pk, attempt_pk):
        attempt = get_object_or_404(StudentAttempt, pk=attempt_pk, exam_id=pk)
        return render(request, "dashboard/monitor/_violations.html", {"violations": attempt.violations.all()[:50]})
