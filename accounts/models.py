"""
User, StudentProfile and LoginEvent models.

Single-device lock design
-------------------------
`User.active_session_key` stores the Django session key of the ONE browser a
student is allowed to use. Phase 2 middleware compares every request's
session key against it; the login view either refuses a second login
(BLOCK mode) or deletes the old Session row (REPLACE mode). Admins can clear
the lock with `User.unlock_session()` when a lab PC crashes.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.sessions.models import Session
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from .utils import normalize_class_name


# Like Django's default, but also allows "/" which many admission numbers use
# (e.g. "2024/015").
cbt_username_validator = RegexValidator(
    regex=r"^[\w.@+\-/]+\Z",
    message="Use only letters, digits and the characters @ . + - _ /",
)


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Administrator"
    TEACHER = "TEACHER", "Teacher"
    STUDENT = "STUDENT", "Student"


class CBTUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)

    def students(self):
        return self.filter(role=Role.STUDENT)

    def staff_members(self):
        return self.filter(role__in=[Role.ADMIN, Role.TEACHER])


class User(AbstractUser):
    """
    Custom user. For students `username` is the Admission Number.
    """

    username_validator = cbt_username_validator
    username = models.CharField(
        "username",
        max_length=150,
        unique=True,
        validators=[cbt_username_validator],
        help_text="Students: Admission Number. Letters, digits and @ . + - _ / only.",
        error_messages={"unique": "A user with that username already exists."},
    )
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.STUDENT,
        db_index=True,
    )
    active_session_key = models.CharField(
        max_length=40,
        blank=True,
        default="",
        db_index=True,
        help_text="Session key of the single browser this user is currently bound to.",
    )
    session_started_at = models.DateTimeField(null=True, blank=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    theme = models.CharField(
        "screen theme",
        max_length=8,
        choices=[("light", "Light"), ("dark", "Dark"), ("warm", "Warm")],
        default="light",
        help_text="Each person's own screen theme, kept with their account (not with the PC).",
    )

    objects = CBTUserManager()

    class Meta:
        ordering = ["username"]
        indexes = [
            models.Index(fields=["role", "is_active"], name="user_role_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.username} ({self.display_name})"

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = Role.ADMIN
        # Admins/teachers may use the Django admin; students never can.
        self.is_staff = self.role in (Role.ADMIN, Role.TEACHER)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "role" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"is_staff"}
        super().save(*args, **kwargs)

    # ----- role helpers -------------------------------------------------
    @property
    def is_admin_role(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_teacher_role(self) -> bool:
        return self.role == Role.TEACHER

    @property
    def is_student_role(self) -> bool:
        return self.role == Role.STUDENT

    @property
    def is_staff_member(self) -> bool:
        return self.role in (Role.ADMIN, Role.TEACHER)

    @property
    def display_name(self) -> str:
        if self.is_student_role:
            profile = getattr(self, "student_profile", None)
            if profile is not None and profile.full_name:
                return profile.full_name
        return self.get_full_name() or self.username

    # ----- single-session helpers ----------------------------------------
    def has_live_session(self) -> bool:
        """True if the bound session still exists and has not expired."""
        if not self.active_session_key:
            return False
        return Session.objects.filter(
            session_key=self.active_session_key,
            expire_date__gt=timezone.now(),
        ).exists()

    def bind_session(self, session_key: str, ip_address: str | None = None) -> None:
        """Record `session_key` as the only valid session for this user."""
        now = timezone.now()
        type(self).objects.filter(pk=self.pk).update(
            active_session_key=session_key,
            session_started_at=now,
            last_login_ip=ip_address,
        )
        self.active_session_key = session_key
        self.session_started_at = now
        self.last_login_ip = ip_address

    def clear_session(self, *, delete_session_row: bool = True) -> str:
        """Drop the bound session (and its DB row). Returns the old key."""
        old_key = self.active_session_key
        if delete_session_row and old_key:
            Session.objects.filter(session_key=old_key).delete()
        type(self).objects.filter(pk=self.pk).update(
            active_session_key="",
            session_started_at=None,
        )
        self.active_session_key = ""
        self.session_started_at = None
        return old_key

    @transaction.atomic
    def unlock_session(self, performed_by=None, reason: str = "") -> str:
        """
        Admin action: free the student to log in on another PC.

        The old Session row is kept on purpose: SingleSessionMiddleware sees
        that it no longer matches active_session_key and logs that PC out with
        a clear "session ended" message (and a JSON 401 for exam API calls).
        """
        old_key = self.clear_session(delete_session_row=False)
        LoginEvent.objects.create(
            user=self,
            event_type=LoginEvent.EventType.ADMIN_UNLOCK,
            session_key=old_key,
            performed_by=performed_by,
            details=reason or "Session reset by administrator.",
        )
        return old_key


class StudentProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
        limit_choices_to={"role": Role.STUDENT},
    )
    full_name = models.CharField(max_length=150)
    batch_or_class = models.CharField(
        "class / batch",
        max_length=50,
        db_index=True,
        help_text="Stored upper-case, e.g. 'XII-A'. Must match Exam.target_class.",
    )
    roll_number = models.CharField(max_length=20, blank=True, default="")
    initial_password = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Plain-text password printed on login slips. Visible to admins only. "
            "Clear it after slips are distributed if your policy requires."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["batch_or_class", "roll_number", "full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch_or_class", "roll_number"],
                condition=~Q(roll_number=""),
                name="uniq_roll_number_per_class",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} [{self.batch_or_class} / {self.roll_number or '-'}]"

    def save(self, *args, **kwargs):
        self.batch_or_class = normalize_class_name(self.batch_or_class)
        self.full_name = " ".join(self.full_name.split())
        self.roll_number = (self.roll_number or "").strip()
        super().save(*args, **kwargs)

    @property
    def admission_number(self) -> str:
        return self.user.username


class LoginEvent(models.Model):
    """Audit trail for logins, blocked duplicates, replacements and unlocks."""

    class EventType(models.TextChoices):
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed login"
        LOGIN_BLOCKED = "LOGIN_BLOCKED", "Blocked: already logged in elsewhere"
        SESSION_REPLACED = "SESSION_REPLACED", "Previous session replaced"
        ADMIN_UNLOCK = "ADMIN_UNLOCK", "Unlocked by admin"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="login_events",
        null=True,
        blank=True,
    )
    username_attempted = models.CharField(max_length=150, blank=True, default="")
    event_type = models.CharField(max_length=20, choices=EventType.choices, db_index=True)
    session_key = models.CharField(max_length=40, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    details = models.CharField(max_length=255, blank=True, default="")
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["user", "-timestamp"], name="loginevent_user_ts_idx"),
        ]

    def __str__(self) -> str:
        who = self.user.username if self.user_id else self.username_attempted or "?"
        return f"{self.get_event_type_display()} — {who} @ {self.timestamp:%Y-%m-%d %H:%M:%S}"
