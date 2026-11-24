"""
Login / logout with single-device enforcement, role-based home redirect and
the student landing page.
"""

import logging

from django.conf import settings
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from exams.models import Exam, StudentAttempt

from .forms import CBTLoginForm
from .models import LoginEvent, User
from .utils import get_client_ip

logger = logging.getLogger("cbt")

LOGIN_REASONS = {
    "session_ended": (
        "warning",
        "You were logged out because your account was opened on another computer "
        "or the invigilator reset your session.",
    ),
    "logged_out": ("info", "You have been logged out."),
    "submitted": ("success", "Your test was submitted successfully."),
    "expired": (
        "warning",
        "That sign-in page was out of date (for example, someone signed in or out in another tab "
        "of this browser). Please enter your username and password again.",
    ),
}


def csrf_failure(request, reason=""):
    """
    Friendly replacement for Django's "403 CSRF verification failed" page.

    A stale form token is almost always harmless: a login page left open in one
    tab while someone signed in or out in another tab of the same browser, or a
    page restored with the Back button. Instead of a technical error:
      * the login form  -> a fresh login page with a short explanation
      * AJAX / exam API -> JSON 403 {"error": "csrf_failed"} (the exam screen recovers)
      * any other form  -> a simple page asking to reload
    """
    logger.warning("CSRF failure on %s %s (%s)", request.method, request.path, reason)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return JsonResponse(
            {"error": "csrf_failed", "message": "The page is out of date. Reloading it will fix this."},
            status=403,
        )
    if request.path == reverse("accounts:login"):
        return redirect(f"{reverse('accounts:login')}?reason=expired")
    back = request.META.get("HTTP_REFERER", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = ""
    return render(request, "403_csrf.html", {"back_url": back}, status=403)


def _user_agent(request) -> str:
    return (request.META.get("HTTP_USER_AGENT") or "")[:255]


class CBTLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = CBTLoginForm
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        # A stale login form was sent while this browser is already signed in
        # (another tab). Say so plainly instead of silently opening that person's pages.
        if request.GET.get("reason") == "expired" and request.user.is_authenticated:
            return render(request, "accounts/already_signed_in.html", status=200)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        reason = self.request.GET.get("reason")
        if reason in LOGIN_REASONS:
            ctx["reason_level"], ctx["reason_message"] = LOGIN_REASONS[reason]
        return ctx

    def form_invalid(self, form):
        username = (form.data.get("username") or "").strip()[:150]
        if username:
            LoginEvent.objects.create(
                user=User.objects.filter(username__iexact=username).first(),
                username_attempted=username,
                event_type=LoginEvent.EventType.LOGIN_FAILED,
                ip_address=get_client_ip(self.request),
                user_agent=_user_agent(self.request),
            )
        return super().form_invalid(form)

    def form_valid(self, form):
        user = form.get_user()
        request = self.request
        ip = get_client_ip(request)
        ua = _user_agent(request)

        if not user.is_student_role:
            auth_login(request, user)
            User.objects.filter(pk=user.pk).update(last_login_ip=ip)
            LoginEvent.objects.create(
                user=user,
                event_type=LoginEvent.EventType.LOGIN,
                session_key=request.session.session_key or "",
                ip_address=ip,
                user_agent=ua,
            )
            return HttpResponseRedirect(self.get_success_url())

        with transaction.atomic():
            # Row lock serialises two PCs logging in as the same student.
            locked = User.objects.select_for_update().get(pk=user.pk)
            old_key = locked.active_session_key
            current_key = request.session.session_key
            other_live_session = bool(old_key) and old_key != current_key and locked.has_live_session()

            if other_live_session:
                same_pc = (
                    settings.CBT_ALLOW_SAME_IP_RELOGIN
                    and ip is not None
                    and locked.last_login_ip == ip
                )
                if settings.CBT_SINGLE_SESSION_MODE == "BLOCK" and not same_pc:
                    LoginEvent.objects.create(
                        user=locked,
                        event_type=LoginEvent.EventType.LOGIN_BLOCKED,
                        session_key=old_key,
                        ip_address=ip,
                        user_agent=ua,
                        details=f"Already active from {locked.last_login_ip or 'unknown IP'}.",
                    )
                    logger.warning(
                        "Blocked duplicate login for %s from %s (active on %s).",
                        locked.username, ip, locked.last_login_ip,
                    )
                    return self.render_to_response(
                        self.get_context_data(form=form, blocked=True, blocked_ip=locked.last_login_ip)
                    )
                # The old row is left in place; binding the new key below makes
                # SingleSessionMiddleware log the old PC out on its next request.
                LoginEvent.objects.create(
                    user=locked,
                    event_type=LoginEvent.EventType.SESSION_REPLACED,
                    session_key=old_key,
                    ip_address=ip,
                    user_agent=ua,
                    details=(
                        "Re-login from the same PC." if same_pc
                        else f"Previous session on {locked.last_login_ip or 'unknown IP'} ended."
                    ),
                )

            auth_login(request, user)
            user.bind_session(request.session.session_key, ip)
            LoginEvent.objects.create(
                user=user,
                event_type=LoginEvent.EventType.LOGIN,
                session_key=request.session.session_key,
                ip_address=ip,
                user_agent=ua,
            )
        return HttpResponseRedirect(self.get_success_url())


@require_POST
def logout_view(request):
    user = request.user
    if user.is_authenticated:
        key = request.session.session_key or ""
        if user.active_session_key and user.active_session_key == key:
            user.clear_session(delete_session_row=False)
        LoginEvent.objects.create(
            user=user,
            event_type=LoginEvent.EventType.LOGOUT,
            session_key=key,
            ip_address=get_client_ip(request),
            user_agent=_user_agent(request),
        )
    auth_logout(request)
    return redirect(f"{reverse('accounts:login')}?reason=logged_out")


@never_cache
def home_redirect(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    if request.user.is_staff_member:
        return redirect("dashboard:home")
    return redirect("accounts:student_home")


@method_decorator([never_cache, login_required], name="dispatch")
class StudentHomeView(TemplateView):
    template_name = "accounts/student_home.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff_member:
            return redirect("dashboard:home")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        exams = list(Exam.objects.available_to(user).with_counts().order_by("-created_at"))
        attempts = {
            a.exam_id: a
            for a in StudentAttempt.objects.filter(student=user).select_related("exam")
        }
        ctx["exam_rows"] = [(exam, attempts.get(exam.pk)) for exam in exams]
        ctx["published_attempts"] = [
            a for a in attempts.values() if a.exam.results_published and a.is_finished
        ]
        ctx["profile"] = getattr(user, "student_profile", None)
        return ctx


class AboutView(TemplateView):
    """Public page about the app and its developer (linked from every screen)."""

    template_name = "about.html"

    def get_context_data(self, **kwargs):
        from cbt_project import __version__

        ctx = super().get_context_data(**kwargs)
        ctx["version"] = __version__
        return ctx


THEMES = ("light", "dark", "warm")


@require_POST
def set_theme(request):
    """Save the screen theme (Light / Dark / Warm) for the signed-in user.

    Anonymous visitors (the login page) keep their choice in the browser only.
    """
    theme = request.POST.get("theme", "")
    if theme not in THEMES:
        return JsonResponse({"error": "unknown theme"}, status=400)
    if request.user.is_authenticated:
        User.objects.filter(pk=request.user.pk).update(theme=theme)
    return JsonResponse({"ok": True, "theme": theme})
