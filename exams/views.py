"""
Student CBT engine: instructions -> start/resume -> exam screen -> submit.

JSON API contract (all under /exam/<pk>/api/, CSRF header required for POST):
  200  ok                      {"ok": true, "remaining": <sec>, ...}
  400  bad payload             {"error": "bad_request", "message": ...}
  401  session ended           (SingleSessionMiddleware or not logged in)
  404  no attempt              {"error": "no_attempt"}
  409  already submitted       {"error": "submitted", "redirect": <url>}
  423  exam stopped by admin   {"error": "paused", "remaining": <sec>}
"""

import json
import logging

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseNotAllowed, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.utils import get_client_ip

from dashboard.exports import pdf_response, scorecards_pdf

from .analytics import scorecard
from .models import Exam, ExamNotReadyError, StudentAttempt, ViolationLog
from .services import (
    AnswerPayloadError,
    apply_answer,
    build_exam_page,
    finalize_if_expired,
    status_summary,
    touch_attempt,
    violation_limit_reached,
)

logger = logging.getLogger("cbt")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_student(user) -> bool:
    return user.is_authenticated and getattr(user, "is_student_role", False)


def student_page(view):
    """Page views: students only; others are sent to login/home."""

    @never_cache
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        if not _is_student(request.user):
            return redirect("accounts:home")
        return view(request, *args, **kwargs)

    wrapper.__name__ = view.__name__
    return wrapper


def _json_error(error: str, status: int, **extra) -> JsonResponse:
    return JsonResponse({"error": error, **extra}, status=status)


def student_api(view):
    """
    API views: load the student's attempt (with exam), enforce timing and the
    exam's Start/Stop switch, then call view(request, attempt, now).
    """

    @never_cache
    def wrapper(request, pk, *args, **kwargs):
        if not _is_student(request.user):
            return _json_error("session_invalid", 401, redirect=reverse("accounts:login"))
        attempt = (
            StudentAttempt.objects.select_related("exam")
            .filter(student=request.user, exam_id=pk)
            .first()
        )
        if attempt is None:
            return _json_error("no_attempt", 404)
        submitted_url = reverse("cbt:submitted", args=[pk])
        now = timezone.now()
        if attempt.is_finished or finalize_if_expired(attempt, now):
            return _json_error("submitted", 409, redirect=submitted_url)
        return view(request, attempt, now, *args, **kwargs)

    wrapper.__name__ = view.__name__
    return wrapper


def _read_json(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise AnswerPayloadError("Body must be JSON.") from exc
    if not isinstance(data, dict):
        raise AnswerPayloadError("Body must be a JSON object.")
    return data


def _get_attempt_or_none(user, exam):
    return StudentAttempt.objects.select_related("exam").filter(student=user, exam=exam).first()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@student_page
def instructions(request, pk):
    exam = get_object_or_404(Exam.objects.with_counts(), pk=pk)
    attempt = _get_attempt_or_none(request.user, exam)
    if attempt and finalize_if_expired(attempt):
        return redirect("cbt:submitted", pk=exam.pk)
    profile = getattr(request.user, "student_profile", None)
    eligible = profile is not None and exam.is_for_class(profile.batch_or_class)
    if not eligible and attempt is None:
        messages.error(request, "This exam is not for your class.")
        return redirect("accounts:student_home")
    return render(
        request,
        "exams/instructions.html",
        {"exam": exam, "attempt": attempt, "profile": profile, "can_start": exam.is_active and eligible},
    )


@student_page
def start(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    exam = get_object_or_404(Exam, pk=pk)
    if not Exam.objects.available_to(request.user).filter(pk=exam.pk).exists():
        existing = _get_attempt_or_none(request.user, exam)
        if existing and existing.is_finished:
            return redirect("cbt:submitted", pk=exam.pk)
        messages.error(request, "This exam is not open right now. Please wait for the invigilator.")
        return redirect("accounts:student_home")
    try:
        attempt, created = StudentAttempt.start_or_resume(request.user, exam, ip_address=get_client_ip(request))
    except ExamNotReadyError as exc:
        messages.error(request, str(exc))
        return redirect("accounts:student_home")
    if created:
        logger.info("Attempt %s started: %s / exam %s", attempt.pk, request.user.username, exam.pk)
    if attempt.is_finished:
        return redirect("cbt:submitted", pk=exam.pk)
    return redirect("cbt:take", pk=exam.pk)


@student_page
def take(request, pk):
    exam = get_object_or_404(Exam, pk=pk)
    attempt = _get_attempt_or_none(request.user, exam)
    if attempt is None:
        return redirect("cbt:instructions", pk=exam.pk)
    now = timezone.now()
    if finalize_if_expired(attempt, now):
        return redirect("cbt:submitted", pk=exam.pk)
    if not exam.is_active:
        messages.warning(request, "The invigilator has paused this exam. Your answers are saved; wait for it to resume.")
        return redirect("accounts:student_home")

    items, state = build_exam_page(attempt)
    current = min(attempt.last_question_index, max(len(state) - 1, 0))
    touch_attempt(attempt, now, current)
    config = {
        "examId": exam.pk,
        "questions": state,
        "current": current,
        "remaining": attempt.remaining_seconds(now),
        "violations": attempt.violation_count,
        "syncInterval": settings.CBT_HEARTBEAT_INTERVAL_SECONDS,
        "requireFullscreen": settings.CBT_REQUIRE_FULLSCREEN,
        "csrf": get_token(request),
        "urls": {
            "save": reverse("cbt:api_save", args=[exam.pk]),
            "sync": reverse("cbt:api_sync", args=[exam.pk]),
            "violation": reverse("cbt:api_violation", args=[exam.pk]),
            "submit": reverse("cbt:submit", args=[exam.pk]),
            "submitted": reverse("cbt:submitted", args=[exam.pk]),
            "home": reverse("accounts:student_home"),
        },
    }
    return render(
        request,
        "exams/take.html",
        {
            "exam": exam,
            "attempt": attempt,
            "items": items,
            "config": config,
            "profile": getattr(request.user, "student_profile", None),
        },
    )


@student_page
def submitted(request, pk):
    exam = get_object_or_404(Exam, pk=pk)
    attempt = _get_attempt_or_none(request.user, exam)
    if attempt is None:
        return redirect("accounts:student_home")
    if not finalize_if_expired(attempt):
        return redirect("cbt:take", pk=exam.pk)
    return render(
        request,
        "exams/submitted.html",
        {"exam": exam, "attempt": attempt, "summary": status_summary(attempt)},
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------
@require_GET
@student_api
def api_sync(request, attempt, now):
    """Server-authoritative timer + heartbeat. ?pos=<index> stores the resume position."""
    try:
        position = int(request.GET.get("pos", ""))
    except ValueError:
        position = None
    touch_attempt(attempt, now, position)
    return JsonResponse(
        {
            "ok": True,
            "remaining": attempt.remaining_seconds(now),
            "server_time": now.isoformat(),
            "exam_active": attempt.exam.is_active,
            "violations": attempt.violation_count,
        }
    )


@require_POST
@student_api
def api_save(request, attempt, now):
    if not attempt.exam.is_active:
        return _json_error("paused", 423, remaining=attempt.remaining_seconds(now))
    try:
        data = _read_json(request)
        result = apply_answer(attempt, data.get("question_id"), data.get("selected"), data.get("status"), now)
    except AnswerPayloadError as exc:
        return _json_error("bad_request", 400, message=str(exc))
    position = data.get("current_index")
    touch_attempt(attempt, now, position if isinstance(position, int) else None)
    return JsonResponse({"ok": True, "saved": result, "remaining": attempt.remaining_seconds(now)})


@require_POST
@student_api
def api_violation(request, attempt, now):
    try:
        data = _read_json(request)
    except AnswerPayloadError as exc:
        return _json_error("bad_request", 400, message=str(exc))
    if attempt.violation_count >= 500:  # hard cap against a runaway client
        return JsonResponse({"ok": True, "violations": attempt.violation_count})
    ViolationLog.record(attempt, str(data.get("type", "")), str(data.get("details", ""))[:500])
    attempt.refresh_from_db(fields=["violation_count"])
    if violation_limit_reached(attempt):
        attempt.submit(auto=True, now=now)
        logger.warning("Attempt %s auto-submitted after %s violations.", attempt.pk, attempt.violation_count)
        return _json_error("submitted", 409, redirect=reverse("cbt:submitted", args=[attempt.exam_id]))
    return JsonResponse({"ok": True, "violations": attempt.violation_count})


@require_POST
@never_cache
def submit(request, pk):
    """
    Final submission. Accepts the browser's not-yet-saved answers so a network
    blip in the last seconds never loses work. JSON: {"auto": bool, "answers": [...]}.
    """
    if not _is_student(request.user):
        return _json_error("session_invalid", 401, redirect=reverse("accounts:login"))
    submitted_url = reverse("cbt:submitted", args=[pk])
    attempt = StudentAttempt.objects.select_related("exam").filter(student=request.user, exam_id=pk).first()
    if attempt is None:
        return _json_error("no_attempt", 404)
    if attempt.is_finished:
        return JsonResponse({"ok": True, "redirect": submitted_url})

    try:
        data = _read_json(request)
    except AnswerPayloadError:
        data = {}
    now = timezone.now()
    grace_deadline_ok = not attempt.is_time_over(now)
    if grace_deadline_ok:
        for item in data.get("answers") or []:
            if not isinstance(item, dict):
                continue
            try:
                apply_answer(attempt, item.get("question_id"), item.get("selected"), item.get("status"), now)
            except AnswerPayloadError:
                continue
    # The client's "auto" flag is trusted only when the server agrees time is (nearly) up.
    auto = bool(data.get("auto")) and attempt.remaining_seconds(now) <= settings.CBT_TIMER_GRACE_SECONDS + 5
    attempt.submit(auto=auto or not grace_deadline_ok, now=now)
    logger.info("Attempt %s submitted (%s) by %s", attempt.pk, attempt.status, request.user.username)
    return JsonResponse({"ok": True, "redirect": submitted_url})


# ---------------------------------------------------------------------------
# Published results (student side)
# ---------------------------------------------------------------------------
def _published_attempt(request, pk):
    exam = get_object_or_404(Exam, pk=pk)
    attempt = _get_attempt_or_none(request.user, exam)
    if attempt is None or not attempt.is_finished or not exam.results_published:
        return exam, None
    return exam, attempt


@student_page
def result(request, pk):
    exam, attempt = _published_attempt(request, pk)
    if attempt is None:
        messages.info(request, "Results for this test have not been published yet.")
        return redirect("accounts:student_home")
    card = scorecard(attempt)
    return render(request, "exams/result.html", {"card": card, "exam": exam, "review": exam.allow_review_after_publish})


@student_page
def result_pdf(request, pk):
    exam, attempt = _published_attempt(request, pk)
    if attempt is None:
        return redirect("accounts:student_home")
    content = scorecards_pdf([scorecard(attempt)])
    return pdf_response(content, f"{exam.title}_{request.user.username}_scorecard")
