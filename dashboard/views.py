"""
Admin & Teacher dashboard views (Phase 2):
overview, student management, bulk uploads, sample templates, login slips,
exam CRUD with Start/Stop switch, and question management with KaTeX preview.
"""

import logging

from django.conf import settings

from django.contrib import messages
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Length
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView, UpdateView

from accounts.models import LoginEvent, Role, StudentProfile, User
from accounts.utils import generate_readable_password, normalize_class_name, parse_class_list
from exams.models import AttemptStatus, Exam, Question, StudentAttempt

from .forms import ExamForm, QuestionForm, QuestionUploadForm, SiteSettingsForm, StudentCreateForm, StudentUploadForm
from .importers import ImportFileError, import_questions, import_students, read_rows
from .mixins import AdminRequiredMixin, StaffRequiredMixin, safe_next_url
from .models import SiteSettings
from .sample_files import question_template_response, student_template_response

logger = logging.getLogger("cbt")


def _class_choices() -> list[str]:
    return list(
        StudentProfile.objects.order_by("batch_or_class").values_list("batch_or_class", flat=True).distinct()
    )


def _students_queryset():
    return (
        User.objects.students()
        .select_related("student_profile")
        .annotate(roll_len=Length("student_profile__roll_number"))
        .order_by("student_profile__batch_or_class", "roll_len", "student_profile__roll_number", "username")
    )


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
class DashboardHomeView(StaffRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"
    nav_section = "home"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["student_count"] = User.objects.students().count()
        ctx["class_count"] = len(_class_choices())
        ctx["exam_count"] = Exam.objects.count()
        ctx["active_exams"] = (
            Exam.objects.active()
            .with_counts()
            .annotate(
                in_progress=Count("attempts", filter=Q(attempts__status=AttemptStatus.IN_PROGRESS), distinct=True)
            )
            .order_by("-created_at")
        )
        ctx["in_progress_count"] = StudentAttempt.objects.in_progress().count()
        ctx["locked_count"] = User.objects.students().exclude(active_session_key="").count()
        ctx["recent_events"] = LoginEvent.objects.select_related("user", "performed_by")[:15]
        return ctx


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
class StudentListView(StaffRequiredMixin, ListView):
    template_name = "dashboard/students/list.html"
    context_object_name = "students"
    paginate_by = 50
    nav_section = "students"

    def get_queryset(self):
        qs = _students_queryset()
        self.q = self.request.GET.get("q", "").strip()
        self.selected_class = normalize_class_name(self.request.GET.get("class", ""))
        self.locked_only = self.request.GET.get("locked") == "1"
        if self.q:
            qs = qs.filter(
                Q(username__icontains=self.q)
                | Q(student_profile__full_name__icontains=self.q)
                | Q(student_profile__roll_number=self.q)
            )
        if self.selected_class:
            qs = qs.filter(student_profile__batch_or_class=self.selected_class)
        if self.locked_only:
            qs = qs.exclude(active_session_key="")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(
            classes=_class_choices(),
            q=self.q,
            selected_class=self.selected_class,
            locked_only=self.locked_only,
            querystring=params.urlencode(),
            current_url=self.request.get_full_path(),
        )
        return ctx


class StudentCreateView(AdminRequiredMixin, FormView):
    template_name = "dashboard/students/form.html"
    form_class = StudentCreateForm
    nav_section = "students"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["classes"] = _class_choices()
        return ctx

    def form_valid(self, form):
        data = form.cleaned_data
        password = data["password"] or generate_readable_password()
        with transaction.atomic():
            user = User(username=data["admission_no"], role=Role.STUDENT)
            user.set_password(password)
            user.save()
            StudentProfile.objects.create(
                user=user,
                full_name=data["full_name"],
                batch_or_class=data["class_name"],
                roll_number=data["roll_no"],
                initial_password=password,
            )
        messages.success(self.request, f"Student {user.username} created. Password: {password}")
        return redirect(f"{reverse('dashboard:student_list')}?q={user.username}")


class StudentUploadView(AdminRequiredMixin, FormView):
    template_name = "dashboard/students/upload.html"
    form_class = StudentUploadForm
    nav_section = "students"

    def form_valid(self, form):
        try:
            headers, rows = read_rows(form.cleaned_data["file"])
            report = import_students(headers, rows, update_existing=form.cleaned_data["update_existing"])
        except ImportFileError as exc:
            form.add_error("file", str(exc))
            return self.form_invalid(form)

        if not report.ok:
            messages.error(self.request, f"Nothing was imported: {len(report.errors)} row(s) need fixing.")
        else:
            logger.info(
                "Student import by %s: %d created, %d updated, %d skipped.",
                self.request.user.username, report.created, report.updated, report.skipped,
            )
            messages.success(
                self.request,
                f"Import complete: {report.created} created, {report.updated} updated, {report.skipped} skipped.",
            )
            form = self.get_form_class()()
        return self.render_to_response(self.get_context_data(form=form, report=report))


class StudentTemplateDownloadView(StaffRequiredMixin, View):
    def get(self, request, fmt):
        return student_template_response(fmt)


class StudentUnlockView(StaffRequiredMixin, View):
    """POST: clear the single-device lock so the student can log in on another PC."""

    def post(self, request, pk):
        student = get_object_or_404(User, pk=pk, role=Role.STUDENT)
        had_lock = bool(student.active_session_key)
        student.unlock_session(performed_by=request.user, reason="Unlocked from dashboard.")
        if had_lock:
            messages.success(request, f"{student.display_name} ({student.username}) can now log in on any PC.")
        else:
            messages.info(request, f"{student.username} had no active session.")
        return HttpResponseRedirect(safe_next_url(request, reverse("dashboard:student_list")))


class StudentResetPasswordView(AdminRequiredMixin, View):
    def post(self, request, pk):
        student = get_object_or_404(User.objects.select_related("student_profile"), pk=pk, role=Role.STUDENT)
        password = generate_readable_password()
        with transaction.atomic():
            student.set_password(password)
            student.save(update_fields=["password"])
            profile, _ = StudentProfile.objects.get_or_create(
                user=student, defaults={"full_name": student.username, "batch_or_class": "UNASSIGNED"}
            )
            profile.initial_password = password
            profile.save(update_fields=["initial_password", "updated_at"])
            student.clear_session(delete_session_row=True)
        messages.success(request, f"New password for {student.username}: {password}")
        return HttpResponseRedirect(safe_next_url(request, reverse("dashboard:student_list")))


class ClassPasswordRegenerateView(AdminRequiredMixin, View):
    """POST: give every student of one class a fresh password (for reprinting slips)."""

    def post(self, request):
        class_name = normalize_class_name(request.POST.get("class_name", ""))
        if not class_name or request.POST.get("confirm") != "yes":
            messages.error(request, "Choose a class and confirm to regenerate passwords.")
            return redirect("dashboard:login_slips")
        students = list(
            User.objects.students().filter(student_profile__batch_or_class=class_name).select_related("student_profile")
        )
        with transaction.atomic():
            profiles, old_keys = [], []
            for student in students:
                password = generate_readable_password()
                student.set_password(password)
                student.student_profile.initial_password = password
                profiles.append(student.student_profile)
                if student.active_session_key:
                    old_keys.append(student.active_session_key)
                student.active_session_key = ""
            User.objects.bulk_update(students, ["password", "active_session_key"], batch_size=200)
            StudentProfile.objects.bulk_update(profiles, ["initial_password"], batch_size=200)
            Session.objects.filter(session_key__in=old_keys).delete()
        messages.success(request, f"New passwords generated for {len(students)} student(s) in {class_name}.")
        return redirect(f"{reverse('dashboard:login_slips')}?class={class_name}")


class LoginSlipsView(AdminRequiredMixin, TemplateView):
    template_name = "dashboard/students/login_slips.html"
    nav_section = "students"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        selected = [normalize_class_name(c) for c in self.request.GET.getlist("class") if c.strip()]
        q = self.request.GET.get("q", "").strip()
        qs = _students_queryset()
        if selected:
            qs = qs.filter(student_profile__batch_or_class__in=selected)
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(student_profile__full_name__icontains=q))
        ctx.update(students=qs, classes=_class_choices(), selected_classes=selected, q=q)
        return ctx


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------
class ExamListView(StaffRequiredMixin, ListView):
    template_name = "dashboard/exams/list.html"
    context_object_name = "exams"
    paginate_by = 30
    nav_section = "exams"

    def get_queryset(self):
        qs = Exam.objects.with_counts().annotate(
            in_progress=Count("attempts", filter=Q(attempts__status=AttemptStatus.IN_PROGRESS), distinct=True)
        )
        self.status = self.request.GET.get("status", "")
        self.selected_class = normalize_class_name(self.request.GET.get("class", ""))
        self.q = self.request.GET.get("q", "").strip()
        if self.status == "active":
            qs = qs.filter(is_active=True)
        elif self.status == "inactive":
            qs = qs.filter(is_active=False)
        elif self.status == "published":
            qs = qs.filter(results_published=True)
        if self.selected_class:
            qs = qs.for_class(self.selected_class)
        if self.q:
            qs = qs.filter(Q(title__icontains=self.q) | Q(subject__icontains=self.q))
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(
            status=self.status,
            selected_class=self.selected_class,
            q=self.q,
            classes=sorted({c for t in Exam.objects.values_list("target_class", flat=True) for c in parse_class_list(t)}
                           | set(_class_choices())),
            querystring=params.urlencode(),
        )
        return ctx


class ExamFormMixin:
    form_class = ExamForm
    template_name = "dashboard/exams/form.html"
    nav_section = "exams"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["classes"] = _class_choices()
        return ctx

    def get_success_url(self):
        return reverse("dashboard:exam_detail", args=[self.object.pk])


class ExamCreateView(StaffRequiredMixin, ExamFormMixin, CreateView):
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Exam created. Now add questions (manually or by bulk upload).")
        return super().form_valid(form)


class ExamUpdateView(StaffRequiredMixin, ExamFormMixin, UpdateView):
    model = Exam

    def form_valid(self, form):
        if self.object.attempts.exists() and {"duration_minutes", "positive_marks", "negative_marks"} & set(form.changed_data):
            messages.warning(
                self.request,
                "Students have already attempted this exam. Duration changes apply to running attempts; "
                "use 'Re-grade' after changing marks.",
            )
        messages.success(self.request, "Exam saved.")
        return super().form_valid(form)


class ExamDetailView(StaffRequiredMixin, DetailView):
    model = Exam
    template_name = "dashboard/exams/detail.html"
    context_object_name = "exam"
    nav_section = "exams"

    def get_queryset(self):
        return Exam.objects.with_counts()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        exam = self.object
        status_counts = dict(
            exam.attempts.values_list("status").annotate(n=Count("id")).values_list("status", "n")
        )
        ctx.update(
            questions=exam.questions.order_by("sort_order", "id"),
            has_attempts=bool(exam.num_attempts),
            in_progress=status_counts.get(AttemptStatus.IN_PROGRESS, 0),
            submitted=status_counts.get(AttemptStatus.SUBMITTED, 0) + status_counts.get(AttemptStatus.AUTO_SUBMITTED, 0),
            topics=exam.questions.exclude(topic_tag="").values("topic_tag").annotate(n=Count("id")).order_by("topic_tag"),
        )
        return ctx


class ExamToggleActiveView(StaffRequiredMixin, View):
    """POST: the Start/Stop switch."""

    def post(self, request, pk):
        exam = get_object_or_404(Exam, pk=pk)
        if exam.is_active:
            exam.is_active = False
            exam.save(update_fields=["is_active", "updated_at"])
            messages.warning(
                request,
                f"'{exam.title}' stopped. Students can no longer start or continue it until it is activated again.",
            )
        elif exam.results_published:
            messages.error(request, "Results for this exam are published. Unpublish them before reactivating.")
        elif not exam.questions.exists():
            messages.error(request, "Add at least one question before activating this exam.")
        else:
            exam.is_active = True
            exam.save(update_fields=["is_active", "updated_at"])
            classes = exam.target_classes
            label = f"class {classes[0]}" if len(classes) == 1 else f"classes {', '.join(classes)}"
            messages.success(request, f"'{exam.title}' is now ACTIVE for {label}.")
        return HttpResponseRedirect(safe_next_url(request, reverse("dashboard:exam_detail", args=[exam.pk])))


class ExamDeleteView(AdminRequiredMixin, DeleteView):
    model = Exam
    template_name = "dashboard/exams/confirm_delete.html"
    context_object_name = "exam"
    success_url = reverse_lazy("dashboard:exam_list")
    nav_section = "exams"

    def get_queryset(self):
        return Exam.objects.with_counts()

    def form_valid(self, form):
        title = self.object.title
        response = super().form_valid(form)
        messages.success(self.request, f"Exam '{title}' and all its questions and attempts were deleted.")
        return response


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
STRUCTURE_LOCKED_MSG = (
    "Students have already started this exam, so questions cannot be added or removed "
    "(it would break their saved question order). You can still edit text, images and answer keys."
)


class ExamStructureLockMixin:
    """Load self.exam and refuse if any attempt exists. Place AFTER the permission mixin."""

    def dispatch(self, request, *args, **kwargs):
        self.exam = get_object_or_404(Exam, pk=kwargs["exam_pk"])
        if self.exam.attempts.exists():
            messages.error(request, STRUCTURE_LOCKED_MSG)
            return redirect("dashboard:exam_detail", pk=self.exam.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["exam"] = self.exam
        return ctx


class QuestionCreateView(StaffRequiredMixin, ExamStructureLockMixin, CreateView):
    model = Question
    form_class = QuestionForm
    template_name = "dashboard/questions/form.html"
    nav_section = "exams"

    def get_initial(self):
        last = self.exam.questions.order_by("-sort_order").values_list("sort_order", flat=True).first()
        return {"sort_order": (last or 0) + 1}

    def form_valid(self, form):
        form.instance.exam = self.exam
        self.object = form.save()
        messages.success(self.request, f"Question {self.object.sort_order} added.")
        if "_addanother" in self.request.POST:
            return redirect("dashboard:question_create", exam_pk=self.exam.pk)
        return redirect(f"{reverse('dashboard:exam_detail', args=[self.exam.pk])}#q-{self.object.pk}")


class QuestionUpdateView(StaffRequiredMixin, UpdateView):
    model = Question
    form_class = QuestionForm
    template_name = "dashboard/questions/form.html"
    context_object_name = "question"
    nav_section = "exams"

    def get_queryset(self):
        return Question.objects.select_related("exam")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["exam"] = self.object.exam
        return ctx

    def form_valid(self, form):
        previous = Question.objects.get(pk=self.object.pk)
        old_image_name = previous.question_image.name if previous.question_image else ""
        old_correct = previous.correct_option
        self.object = form.save()

        new_image_name = self.object.question_image.name if self.object.question_image else ""
        if old_image_name and old_image_name != new_image_name:
            storage = previous.question_image.storage
            transaction.on_commit(lambda: storage.delete(old_image_name))

        exam = self.object.exam
        if old_correct != self.object.correct_option:
            regraded = exam.recalculate_scores()
            if regraded:
                messages.info(self.request, f"Answer key changed — {regraded} submitted attempt(s) re-graded.")
        messages.success(self.request, "Question saved.")
        return redirect(f"{reverse('dashboard:exam_detail', args=[exam.pk])}#q-{self.object.pk}")


class QuestionDeleteView(StaffRequiredMixin, View):
    def post(self, request, pk):
        question = get_object_or_404(Question.objects.select_related("exam"), pk=pk)
        exam = question.exam
        if exam.attempts.exists():
            messages.error(request, STRUCTURE_LOCKED_MSG)
        else:
            question.delete()
            messages.success(request, "Question deleted.")
        return redirect("dashboard:exam_detail", pk=exam.pk)


class QuestionUploadView(StaffRequiredMixin, ExamStructureLockMixin, FormView):
    form_class = QuestionUploadForm
    template_name = "dashboard/questions/upload.html"
    nav_section = "exams"

    def form_valid(self, form):
        replace = form.cleaned_data["mode"] == QuestionUploadForm.MODE_REPLACE
        try:
            headers, rows = read_rows(form.cleaned_data["file"])
            report = import_questions(self.exam, headers, rows, replace=replace)
        except ImportFileError as exc:
            form.add_error("file", str(exc))
            return self.form_invalid(form)
        if not report.ok:
            messages.error(self.request, f"Nothing was imported: {len(report.errors)} row(s) need fixing.")
            return self.render_to_response(self.get_context_data(form=form, report=report))
        messages.success(
            self.request,
            f"{report.created} question(s) imported{' (previous questions replaced)' if replace else ''}. "
            "Check the KaTeX preview below and attach images where needed.",
        )
        return redirect("dashboard:exam_detail", pk=self.exam.pk)


class QuestionTemplateDownloadView(StaffRequiredMixin, View):
    def get(self, request, fmt):
        return question_template_response(fmt)


# ---------------------------------------------------------------------------
# Institution settings
# ---------------------------------------------------------------------------
class SiteSettingsView(AdminRequiredMixin, UpdateView):
    form_class = SiteSettingsForm
    template_name = "dashboard/settings.html"
    nav_section = "settings"

    def get_object(self, queryset=None):
        return SiteSettings.load()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["env_school_name"] = settings.CBT_SCHOOL_NAME
        ctx["env_server_url"] = settings.CBT_SERVER_URL
        return ctx

    def form_valid(self, form):
        previous = SiteSettings.objects.filter(pk=1).first()
        old_logo = previous.logo.name if previous and previous.logo else ""
        form.instance.updated_by = self.request.user
        form.save()
        new_logo = form.instance.logo.name if form.instance.logo else ""
        if old_logo and old_logo != new_logo:  # replaced or removed: delete the old file
            storage = form.instance.logo.storage
            transaction.on_commit(lambda: storage.delete(old_logo))
        logger.info("Site settings changed by %s: name=%r logo=%r", self.request.user.username,
                    form.instance.institution_name, new_logo)
        messages.success(self.request, "Settings saved. The name and logo now appear on every page, login slip and report.")
        return redirect("dashboard:settings")
