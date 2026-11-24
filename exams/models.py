"""
Exam, Question, StudentAttempt, StudentAnswer and ViolationLog models.

Key invariants
--------------
* One StudentAttempt per (student, exam) — enforced by a DB unique constraint,
  so a refresh, a double-click on "Start" or a move to a spare PC always
  resumes the same attempt.
* `question_order` / `option_orders` are generated ONCE (cryptographic RNG) at
  attempt creation and never change.
* `StudentAnswer.selected_option` always stores the CANONICAL key (A/B/C/D as
  authored), never the on-screen position, so scoring and analytics ignore
  shuffling entirely.
* Remaining time is derived only from server values:
      started_at + duration + extra_time - server_now
"""

import os
import random
import re
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Count, F, Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from accounts.models import Role
from accounts.utils import normalize_class_name, parse_class_list

OPTION_KEYS: tuple[str, ...] = ("A", "B", "C", "D")
TWO_PLACES = Decimal("0.01")


class ExamNotReadyError(Exception):
    """Raised when a student tries to start an exam that cannot be started."""


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------
class OptionKey(models.TextChoices):
    A = "A", "Option A"
    B = "B", "Option B"
    C = "C", "Option C"
    D = "D", "Option D"


class AttemptStatus(models.TextChoices):
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    SUBMITTED = "SUBMITTED", "Submitted"
    AUTO_SUBMITTED = "AUTO_SUBMITTED", "Auto-submitted"


FINISHED_STATUSES = (AttemptStatus.SUBMITTED, AttemptStatus.AUTO_SUBMITTED)


class AnswerStatus(models.TextChoices):
    NOT_VISITED = "NOT_VISITED", "Not visited"
    NOT_ANSWERED = "NOT_ANSWERED", "Not answered"
    ANSWERED = "ANSWERED", "Answered"
    MARKED_REVIEW = "MARKED_REVIEW", "Marked for review"
    ANSWERED_MARKED = "ANSWERED_MARKED", "Answered & marked for review"


ANSWERED_STATUSES = (AnswerStatus.ANSWERED, AnswerStatus.ANSWERED_MARKED)
MARKED_STATUSES = (AnswerStatus.MARKED_REVIEW, AnswerStatus.ANSWERED_MARKED)


class ViolationType(models.TextChoices):
    TAB_SWITCH = "TAB_SWITCH", "Tab switch / page hidden"
    FULLSCREEN_EXIT = "FULLSCREEN_EXIT", "Exited full screen"
    WINDOW_BLUR = "WINDOW_BLUR", "Window lost focus"
    OTHER = "OTHER", "Other"


# ---------------------------------------------------------------------------
# Upload helpers / validators (module-level so migrations can import them)
# ---------------------------------------------------------------------------
def question_image_upload_to(instance, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower() or ".png"
    exam_part = f"exam_{instance.exam_id}" if instance.exam_id else "unassigned"
    return f"questions/{exam_part}/{uuid.uuid4().hex}{ext}"


def validate_image_size(file) -> None:
    limit_mb = getattr(settings, "CBT_MAX_QUESTION_IMAGE_MB", 2)
    if file and getattr(file, "size", 0) > limit_mb * 1024 * 1024:
        raise ValidationError(f"Image is larger than {limit_mb} MB. Please compress it.")


# ---------------------------------------------------------------------------
# Exam
# ---------------------------------------------------------------------------
class ExamQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def for_class(self, class_name: str):
        """Exams whose target classes include `class_name` (an exam may target several)."""
        name = normalize_class_name(class_name)
        if not name:
            return self.none()
        # target_class is stored as "X-A, X-B, XI" - match one whole entry, not a prefix.
        return self.filter(target_class__regex=rf"(^|, ){re.escape(name)}(,|$)")

    def available_to(self, student):
        """Active exams for the student's class."""
        profile = getattr(student, "student_profile", None)
        if profile is None:
            return self.none()
        return self.active().for_class(profile.batch_or_class)

    def with_counts(self):
        return self.annotate(
            num_questions=Count("questions", distinct=True),
            num_attempts=Count("attempts", distinct=True),
        )


class Exam(models.Model):
    title = models.CharField(max_length=200)
    subject = models.CharField(max_length=100, db_index=True)
    target_class = models.CharField(
        "target class(es)",
        max_length=255,
        db_index=True,
        help_text="One or more classes, separated by commas, e.g. 'X-A, X-B'. "
        "Each must match the students' class exactly (stored upper-case).",
    )
    instructions = models.TextField(
        blank=True,
        help_text="Shown on the pre-exam instructions screen. LaTeX allowed.",
    )
    duration_minutes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(600)],
    )
    positive_marks = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("4.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Marks awarded per correct answer.",
    )
    negative_marks = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Marks DEDUCTED per wrong answer. Enter a positive number (1 means −1). 0 = no negative marking.",
    )
    shuffle_questions = models.BooleanField(default=True)
    shuffle_options = models.BooleanField(default=True)
    is_active = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Start/Stop switch. Students can only start or continue an active exam.",
    )
    results_published = models.BooleanField(default=False)
    results_published_at = models.DateTimeField(null=True, blank=True)
    allow_review_after_publish = models.BooleanField(
        default=False,
        help_text="After publishing, let students see correct answers and explanations.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_exams",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ExamQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_active", "target_class"], name="exam_active_class_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.title} — {self.subject} [{self.target_class}]"

    def save(self, *args, **kwargs):
        self.target_class = ", ".join(parse_class_list(self.target_class))
        super().save(*args, **kwargs)

    @property
    def target_classes(self) -> list[str]:
        """The classes this exam is for, e.g. ['X-A', 'X-B']."""
        return parse_class_list(self.target_class)

    def is_for_class(self, class_name: str) -> bool:
        return normalize_class_name(class_name) in self.target_classes

    @property
    def question_count(self) -> int:
        annotated = getattr(self, "num_questions", None)
        return annotated if annotated is not None else self.questions.count()

    @property
    def max_marks(self) -> Decimal:
        return (self.positive_marks * self.question_count).quantize(TWO_PLACES)

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.duration_minutes)

    def finalize_open_attempts(self, by=None) -> int:
        """Auto-submit every attempt still IN_PROGRESS. Returns how many."""
        count = 0
        for attempt in self.attempts.filter(status=AttemptStatus.IN_PROGRESS).select_related("exam"):
            if attempt.submit(auto=True, by=by):
                count += 1
        return count

    def recalculate_scores(self) -> int:
        """Re-grade all finished attempts (e.g. after fixing an answer key)."""
        attempts = list(self.attempts.filter(status__in=FINISHED_STATUSES).select_related("exam"))
        for attempt in attempts:
            attempt.calculate_score()
        StudentAttempt.objects.bulk_update(
            attempts,
            ["total_score", "correct_count", "wrong_count", "attempted_count"],
            batch_size=200,
        )
        return len(attempts)

    @transaction.atomic
    def publish_results(self, by=None) -> int:
        """
        Close the exam, auto-submit stragglers, re-grade everything and make
        results visible. Returns the number of attempts auto-submitted.
        """
        locked = type(self).objects.select_for_update().get(pk=self.pk)
        locked.is_active = False
        locked.save(update_fields=["is_active", "updated_at"])
        finalized = locked.finalize_open_attempts(by=by)
        locked.recalculate_scores()
        locked.results_published = True
        locked.results_published_at = timezone.now()
        locked.save(update_fields=["results_published", "results_published_at", "updated_at"])
        self.refresh_from_db()
        return finalized

    def unpublish_results(self) -> None:
        self.results_published = False
        self.results_published_at = None
        self.save(update_fields=["results_published", "results_published_at", "updated_at"])


# ---------------------------------------------------------------------------
# Question
# ---------------------------------------------------------------------------
class Question(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text="Authoring order (used when question shuffling is off).",
    )
    question_text = models.TextField(
        help_text=r"LaTeX supported: inline $...$ or \(...\), display $$...$$ or \[...\].",
    )
    question_image = models.ImageField(
        upload_to=question_image_upload_to,
        blank=True,
        validators=[validate_image_size],
    )
    option_a = models.TextField("option A")
    option_b = models.TextField("option B")
    option_c = models.TextField("option C")
    option_d = models.TextField("option D")
    correct_option = models.CharField(max_length=1, choices=OptionKey.choices)
    explanation = models.TextField(blank=True)
    topic_tag = models.CharField(max_length=100, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["exam", "sort_order", "id"]
        indexes = [
            models.Index(fields=["exam", "sort_order"], name="question_exam_order_idx"),
        ]

    def __str__(self) -> str:
        text = " ".join(self.question_text.split())
        return f"Q{self.sort_order or self.pk}: {text[:70]}{'…' if len(text) > 70 else ''}"

    def save(self, *args, **kwargs):
        self.correct_option = (self.correct_option or "").strip().upper()
        self.topic_tag = (self.topic_tag or "").strip()
        super().save(*args, **kwargs)

    def get_option_text(self, key: str) -> str:
        return getattr(self, f"option_{key.lower()}")

    def options_dict(self) -> dict[str, str]:
        return {key: self.get_option_text(key) for key in OPTION_KEYS}


@receiver(post_delete, sender=Question)
def _delete_question_image_file(sender, instance: Question, **kwargs) -> None:
    """Remove the image file from disk once the DB delete has committed."""
    if instance.question_image:
        storage = instance.question_image.storage
        name = instance.question_image.name
        transaction.on_commit(lambda: storage.delete(name))


# ---------------------------------------------------------------------------
# StudentAttempt
# ---------------------------------------------------------------------------
class StudentAttemptQuerySet(models.QuerySet):
    def in_progress(self):
        return self.filter(status=AttemptStatus.IN_PROGRESS)

    def finished(self):
        return self.filter(status__in=FINISHED_STATUSES)


class StudentAttempt(models.Model):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attempts",
        limit_choices_to={"role": Role.STUDENT},
    )
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="attempts")
    status = models.CharField(
        max_length=15,
        choices=AttemptStatus.choices,
        default=AttemptStatus.IN_PROGRESS,
        db_index=True,
    )
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    extra_time_minutes = models.PositiveSmallIntegerField(
        default=0,
        help_text="Additional minutes granted by an admin from the Live Monitor.",
    )

    # Persistent shuffle state
    question_order = models.JSONField(
        default=list,
        help_text="Exact shuffled list of Question IDs shown to this student.",
    )
    option_orders = models.JSONField(
        default=dict,
        help_text='Per-question display order of canonical keys, e.g. {"12": ["C","A","D","B"]}.',
    )
    last_question_index = models.PositiveSmallIntegerField(
        default=0,
        help_text="Position in question_order the student was last viewing (for resume).",
    )

    # Results (filled on submit / re-grade)
    total_score = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"))
    correct_count = models.PositiveIntegerField(default=0)
    wrong_count = models.PositiveIntegerField(default=0)
    attempted_count = models.PositiveIntegerField(default=0)
    time_taken_seconds = models.PositiveIntegerField(null=True, blank=True)

    # Proctoring / monitoring
    violation_count = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True, help_text="Last heartbeat/auto-save.")
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    force_submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    objects = StudentAttemptQuerySet.as_manager()

    class Meta:
        ordering = ["exam", "student__username"]
        constraints = [
            models.UniqueConstraint(fields=["student", "exam"], name="uniq_attempt_student_exam"),
        ]
        indexes = [
            models.Index(fields=["exam", "status"], name="attempt_exam_status_idx"),
            models.Index(fields=["exam", "-total_score"], name="attempt_exam_score_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student.username} → {self.exam.title} ({self.get_status_display()})"

    # ----- creation / resume --------------------------------------------
    @classmethod
    def start_or_resume(cls, student, exam: Exam, ip_address: str | None = None):
        """
        Return (attempt, created). Safe under concurrent double-clicks and
        multi-PC races: the unique constraint guarantees a single attempt, and
        the shuffle is generated only for the row that actually gets inserted.
        """
        if not exam.is_active:
            raise ExamNotReadyError("This exam is not active.")
        question_ids = list(
            exam.questions.order_by("sort_order", "id").values_list("id", flat=True)
        )
        if not question_ids:
            raise ExamNotReadyError("This exam has no questions yet.")

        rng = random.SystemRandom()
        order = list(question_ids)
        if exam.shuffle_questions:
            rng.shuffle(order)
        option_orders: dict[str, list[str]] = {}
        for qid in question_ids:
            keys = list(OPTION_KEYS)
            if exam.shuffle_options:
                rng.shuffle(keys)
            option_orders[str(qid)] = keys

        with transaction.atomic():
            attempt, created = cls.objects.get_or_create(
                student=student,
                exam=exam,
                defaults={
                    "question_order": order,
                    "option_orders": option_orders,
                    "started_at": timezone.now(),
                    "client_ip": ip_address,
                    "last_seen_at": timezone.now(),
                },
            )
            if created:
                StudentAnswer.objects.bulk_create(
                    [StudentAnswer(attempt=attempt, question_id=qid) for qid in order],
                    ignore_conflicts=True,
                )
        return attempt, created

    # ----- timing ---------------------------------------------------------
    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATUSES

    @property
    def allowed_duration(self) -> timedelta:
        return timedelta(minutes=self.exam.duration_minutes + self.extra_time_minutes)

    @property
    def deadline(self):
        return self.started_at + self.allowed_duration

    def remaining_seconds(self, now=None) -> int:
        if self.is_finished:
            return 0
        now = now or timezone.now()
        return max(0, int((self.deadline - now).total_seconds()))

    def is_time_over(self, now=None, grace_seconds: int | None = None) -> bool:
        """True once server time passes the deadline plus a network grace."""
        now = now or timezone.now()
        grace = settings.CBT_TIMER_GRACE_SECONDS if grace_seconds is None else grace_seconds
        return now >= self.deadline + timedelta(seconds=grace)

    def add_extra_time(self, minutes: int) -> None:
        if minutes <= 0:
            raise ValueError("Extra time must be a positive number of minutes.")
        type(self).objects.filter(pk=self.pk).update(
            extra_time_minutes=F("extra_time_minutes") + minutes
        )
        self.refresh_from_db(fields=["extra_time_minutes"])

    def touch(self, now=None) -> None:
        """Cheap heartbeat update used by auto-save and the timer sync API."""
        now = now or timezone.now()
        type(self).objects.filter(pk=self.pk).update(last_seen_at=now)
        self.last_seen_at = now

    # ----- shuffle helpers -------------------------------------------------
    def get_option_order(self, question_id: int) -> list[str]:
        return list(self.option_orders.get(str(question_id), OPTION_KEYS))

    def canonical_key_at(self, question_id: int, display_index: int) -> str:
        """Map an on-screen position (0-3) to the canonical option key."""
        return self.get_option_order(question_id)[display_index]

    def display_index_of(self, question_id: int, canonical_key: str) -> int:
        return self.get_option_order(question_id).index(canonical_key)

    # ----- scoring -----------------------------------------------------------
    def calculate_score(self) -> Decimal:
        """Compute counts and score from saved answers (does NOT save)."""
        agg = self.answers.filter(
            selected_option__isnull=False,
            question__exam_id=self.exam_id,
        ).aggregate(
            attempted=Count("id"),
            correct=Count("id", filter=Q(selected_option=F("question__correct_option"))),
        )
        attempted = agg["attempted"] or 0
        correct = agg["correct"] or 0
        wrong = attempted - correct
        exam = self.exam
        score = exam.positive_marks * correct - exam.negative_marks * wrong
        self.attempted_count = attempted
        self.correct_count = correct
        self.wrong_count = wrong
        self.total_score = Decimal(score).quantize(TWO_PLACES)
        return self.total_score

    @property
    def accuracy_percent(self) -> Decimal:
        if not self.attempted_count:
            return Decimal("0.00")
        return (Decimal(self.correct_count) * 100 / Decimal(self.attempted_count)).quantize(TWO_PLACES)

    def submit(self, *, auto: bool = False, by=None, now=None) -> bool:
        """
        Finalise the attempt exactly once (row-locked, idempotent).
        Returns True if this call performed the submission.
        """
        with transaction.atomic():
            locked = (
                type(self).objects.select_for_update().select_related("exam").get(pk=self.pk)
            )
            if locked.is_finished:
                performed = False
            else:
                now = now or timezone.now()
                effective_end = min(now, locked.deadline)
                locked.status = AttemptStatus.AUTO_SUBMITTED if auto else AttemptStatus.SUBMITTED
                locked.submitted_at = now
                locked.time_taken_seconds = max(
                    0, int((effective_end - locked.started_at).total_seconds())
                )
                if by is not None and getattr(by, "pk", None) != locked.student_id:
                    locked.force_submitted_by = by
                locked.calculate_score()
                locked.save(
                    update_fields=[
                        "status",
                        "submitted_at",
                        "time_taken_seconds",
                        "force_submitted_by",
                        "total_score",
                        "correct_count",
                        "wrong_count",
                        "attempted_count",
                    ]
                )
                performed = True
        self.refresh_from_db()
        return performed


# ---------------------------------------------------------------------------
# StudentAnswer
# ---------------------------------------------------------------------------
class StudentAnswer(models.Model):
    attempt = models.ForeignKey(StudentAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="student_answers")
    selected_option = models.CharField(
        max_length=1,
        choices=OptionKey.choices,
        null=True,
        blank=True,
        help_text="Canonical (authored) option key, independent of on-screen shuffle.",
    )
    status = models.CharField(
        max_length=16,
        choices=AnswerStatus.choices,
        default=AnswerStatus.NOT_VISITED,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["attempt", "id"]
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="uniq_answer_attempt_question"),
        ]
        indexes = [
            models.Index(fields=["attempt", "status"], name="answer_attempt_status_idx"),
            models.Index(fields=["question", "selected_option"], name="answer_question_option_idx"),
        ]

    def __str__(self) -> str:
        return f"Attempt {self.attempt_id} / Q{self.question_id}: {self.selected_option or '-'} ({self.status})"

    @staticmethod
    def resolve_status(selected_option: str | None, requested_status: str) -> str:
        """
        Keep status consistent with whether an option is selected:
          option + (marked)  -> ANSWERED_MARKED, option otherwise -> ANSWERED
          no option + marked -> MARKED_REVIEW,  no option + ANSWERED -> NOT_ANSWERED
        """
        if selected_option:
            if requested_status in MARKED_STATUSES:
                return AnswerStatus.ANSWERED_MARKED
            return AnswerStatus.ANSWERED
        if requested_status in MARKED_STATUSES:
            return AnswerStatus.MARKED_REVIEW
        if requested_status == AnswerStatus.ANSWERED:
            return AnswerStatus.NOT_ANSWERED
        return requested_status

    def save(self, *args, **kwargs):
        if self.selected_option:
            self.selected_option = self.selected_option.strip().upper()
            if self.selected_option not in OPTION_KEYS:
                raise ValidationError({"selected_option": "Option must be A, B, C or D."})
        else:
            self.selected_option = None
        self.status = self.resolve_status(self.selected_option, self.status)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"status", "updated_at"}
        super().save(*args, **kwargs)

    @property
    def is_answered(self) -> bool:
        return self.selected_option is not None

    @property
    def is_correct(self) -> bool | None:
        if self.selected_option is None:
            return None
        return self.selected_option == self.question.correct_option


# ---------------------------------------------------------------------------
# ViolationLog
# ---------------------------------------------------------------------------
class ViolationLog(models.Model):
    attempt = models.ForeignKey(StudentAttempt, on_delete=models.CASCADE, related_name="violations")
    violation_type = models.CharField(max_length=20, choices=ViolationType.choices, db_index=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    details = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["attempt", "timestamp"], name="violation_attempt_ts_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_violation_type_display()} — attempt {self.attempt_id} @ {self.timestamp:%H:%M:%S}"

    @classmethod
    def record(cls, attempt: StudentAttempt, violation_type: str, details: str = "") -> "ViolationLog":
        """Insert a log row and atomically bump the attempt's counter."""
        if violation_type not in ViolationType.values:
            violation_type = ViolationType.OTHER
        with transaction.atomic():
            log = cls.objects.create(
                attempt=attempt,
                violation_type=violation_type,
                details=(details or "")[:500],
            )
            StudentAttempt.objects.filter(pk=attempt.pk).update(
                violation_count=F("violation_count") + 1
            )
        return log
