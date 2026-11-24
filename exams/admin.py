from django import forms
from django.contrib import admin, messages
from django.db import models
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    AnswerStatus,
    AttemptStatus,
    Exam,
    Question,
    StudentAnswer,
    StudentAttempt,
    ViolationLog,
)


# ---------------------------------------------------------------------------
# Exam + Questions
# ---------------------------------------------------------------------------
class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ("sort_order", "question_text", "correct_option", "topic_tag", "question_image")
    show_change_link = True
    formfield_overrides = {
        models.TextField: {"widget": forms.Textarea(attrs={"rows": 2, "cols": 70})},
    }


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "subject",
        "target_class",
        "duration_minutes",
        "marking_col",
        "questions_col",
        "attempts_col",
        "is_active",
        "results_published",
        "created_at",
    )
    list_filter = ("is_active", "results_published", "subject", "target_class")
    search_fields = ("title", "subject", "target_class")
    readonly_fields = ("created_by", "created_at", "updated_at", "results_published_at")
    date_hierarchy = "created_at"
    inlines = [QuestionInline]
    fieldsets = (
        (None, {"fields": ("title", "subject", "target_class", "instructions")}),
        ("Timing & marking", {"fields": ("duration_minutes", "positive_marks", "negative_marks")}),
        ("Randomisation", {"fields": ("shuffle_questions", "shuffle_options")}),
        (
            "Status",
            {"fields": ("is_active", "results_published", "results_published_at", "allow_review_after_publish")},
        ),
        ("Audit", {"classes": ("collapse",), "fields": ("created_by", "created_at", "updated_at")}),
    )
    actions = [
        "activate_exams",
        "deactivate_exams",
        "publish_results",
        "unpublish_results",
        "recalculate_scores",
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).with_counts()

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        if "results_published" in form.changed_data:
            obj.results_published_at = timezone.now() if obj.results_published else None
        super().save_model(request, obj, form, change)

    @admin.display(description="Marks (+/−)")
    def marking_col(self, obj):
        return f"+{obj.positive_marks} / −{obj.negative_marks}"

    @admin.display(description="Questions", ordering="num_questions")
    def questions_col(self, obj):
        return obj.num_questions

    @admin.display(description="Attempts", ordering="num_attempts")
    def attempts_col(self, obj):
        return obj.num_attempts

    @admin.action(description="▶ Activate (start) selected exams")
    def activate_exams(self, request, queryset):
        empty = [e.title for e in queryset if e.num_questions == 0]
        if empty:
            self.message_user(request, f"Cannot activate exams with no questions: {', '.join(empty)}", messages.ERROR)
            return
        updated = Exam.objects.filter(pk__in=[e.pk for e in queryset]).update(
            is_active=True, updated_at=timezone.now()
        )
        self.message_user(request, f"{updated} exam(s) activated.", messages.SUCCESS)

    @admin.action(description="■ Deactivate (stop) selected exams")
    def deactivate_exams(self, request, queryset):
        updated = Exam.objects.filter(pk__in=[e.pk for e in queryset]).update(
            is_active=False, updated_at=timezone.now()
        )
        self.message_user(request, f"{updated} exam(s) deactivated.", messages.WARNING)

    @admin.action(description="Publish results (auto-submits open attempts)")
    def publish_results(self, request, queryset):
        for exam in queryset:
            finalized = exam.publish_results(by=request.user)
            self.message_user(
                request,
                f"Results published for '{exam.title}'. {finalized} open attempt(s) auto-submitted.",
                messages.SUCCESS,
            )

    @admin.action(description="Hide (unpublish) results")
    def unpublish_results(self, request, queryset):
        for exam in queryset:
            exam.unpublish_results()
        self.message_user(request, f"Results hidden for {queryset.count()} exam(s).", messages.WARNING)

    @admin.action(description="Re-grade submitted attempts (after answer-key change)")
    def recalculate_scores(self, request, queryset):
        total = sum(exam.recalculate_scores() for exam in queryset)
        self.message_user(request, f"{total} attempt(s) re-graded.", messages.SUCCESS)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "exam", "sort_order", "short_text", "correct_option", "topic_tag", "has_image")
    list_filter = ("exam", "topic_tag", "correct_option")
    search_fields = ("question_text", "topic_tag", "exam__title")
    list_select_related = ("exam",)
    autocomplete_fields = ("exam",)
    readonly_fields = ("image_preview", "created_at", "updated_at")
    list_per_page = 100
    fieldsets = (
        (None, {"fields": ("exam", "sort_order", "topic_tag")}),
        ("Question", {"fields": ("question_text", "question_image", "image_preview")}),
        ("Options", {"fields": ("option_a", "option_b", "option_c", "option_d", "correct_option")}),
        ("Explanation", {"fields": ("explanation",)}),
        ("Audit", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )
    formfield_overrides = {
        models.TextField: {"widget": forms.Textarea(attrs={"rows": 3, "cols": 90})},
    }

    @admin.display(description="Question")
    def short_text(self, obj):
        text = " ".join(obj.question_text.split())
        return text[:90] + ("…" if len(text) > 90 else "")

    @admin.display(description="Image", boolean=True)
    def has_image(self, obj):
        return bool(obj.question_image)

    @admin.display(description="Preview")
    def image_preview(self, obj):
        if not obj.question_image:
            return "—"
        return format_html(
            '<img src="{}" style="max-width:480px;max-height:320px;border:1px solid #ccc;" alt="question image">',
            obj.question_image.url,
        )


# ---------------------------------------------------------------------------
# Attempts, answers, violations (read-mostly, for audit)
# ---------------------------------------------------------------------------
class StudentAnswerInline(admin.TabularInline):
    model = StudentAnswer
    extra = 0
    can_delete = False
    fields = ("question", "selected_option", "status", "correct_col", "updated_at")
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("question")

    @admin.display(description="Correct?", boolean=True)
    def correct_col(self, obj):
        return obj.is_correct

    def has_add_permission(self, request, obj=None):
        return False


class ViolationLogInline(admin.TabularInline):
    model = ViolationLog
    extra = 0
    can_delete = False
    fields = ("timestamp", "violation_type", "details")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StudentAttempt)
class StudentAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "student_name",
        "exam",
        "status",
        "started_at",
        "submitted_at",
        "remaining_col",
        "answered_col",
        "total_score",
        "correct_count",
        "wrong_count",
        "violation_count",
    )
    list_filter = ("status", "exam", "exam__target_class")
    search_fields = ("student__username", "student__student_profile__full_name", "exam__title")
    list_select_related = ("student", "student__student_profile", "exam")
    readonly_fields = (
        "student",
        "exam",
        "status",
        "started_at",
        "submitted_at",
        "question_order",
        "option_orders",
        "last_question_index",
        "total_score",
        "correct_count",
        "wrong_count",
        "attempted_count",
        "time_taken_seconds",
        "violation_count",
        "last_seen_at",
        "client_ip",
        "force_submitted_by",
    )
    fields = ("extra_time_minutes",) + readonly_fields
    inlines = [StudentAnswerInline, ViolationLogInline]
    actions = ["force_submit", "extend_5", "extend_10", "regrade"]
    list_per_page = 100

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            num_answered=models.Count(
                "answers",
                filter=models.Q(answers__status__in=[AnswerStatus.ANSWERED, AnswerStatus.ANSWERED_MARKED]),
            )
        )

    def has_add_permission(self, request):
        return False

    @admin.display(description="Name", ordering="student__student_profile__full_name")
    def student_name(self, obj):
        return obj.student.display_name

    @admin.display(description="Answered", ordering="num_answered")
    def answered_col(self, obj):
        return obj.num_answered

    @admin.display(description="Time left")
    def remaining_col(self, obj):
        if obj.is_finished:
            return "—"
        secs = obj.remaining_seconds()
        return f"{secs // 60:02d}:{secs % 60:02d}"

    @admin.action(description="Force submit selected attempts")
    def force_submit(self, request, queryset):
        count = sum(
            1
            for attempt in queryset.filter(status=AttemptStatus.IN_PROGRESS).select_related("exam")
            if attempt.submit(auto=False, by=request.user)
        )
        self.message_user(request, f"{count} attempt(s) force-submitted.", messages.SUCCESS)

    def _extend(self, request, queryset, minutes):
        count = 0
        for attempt in queryset.filter(status=AttemptStatus.IN_PROGRESS):
            attempt.add_extra_time(minutes)
            count += 1
        self.message_user(request, f"Added {minutes} min to {count} attempt(s).", messages.SUCCESS)

    @admin.action(description="Extend time by 5 minutes")
    def extend_5(self, request, queryset):
        self._extend(request, queryset, 5)

    @admin.action(description="Extend time by 10 minutes")
    def extend_10(self, request, queryset):
        self._extend(request, queryset, 10)

    @admin.action(description="Re-grade selected (finished) attempts")
    def regrade(self, request, queryset):
        attempts = list(queryset.finished().select_related("exam"))
        for attempt in attempts:
            attempt.calculate_score()
        StudentAttempt.objects.bulk_update(
            attempts, ["total_score", "correct_count", "wrong_count", "attempted_count"]
        )
        self.message_user(request, f"{len(attempts)} attempt(s) re-graded.", messages.SUCCESS)


@admin.register(ViolationLog)
class ViolationLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "violation_type", "attempt", "details")
    list_filter = ("violation_type", "attempt__exam")
    search_fields = ("attempt__student__username", "details")
    list_select_related = ("attempt", "attempt__student", "attempt__exam")
    date_hierarchy = "timestamp"
    list_per_page = 200

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
