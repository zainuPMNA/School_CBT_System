"""
Institution settings editable from the dashboard (Settings page).

One row only (pk=1). Values from .env (CBT_SCHOOL_NAME, CBT_SERVER_URL) are
used as the starting values the first time, and as the fallback when a field
is left blank. Read through `SiteSettings.load()`, which caches the row for a
few seconds per process so every page render does not hit the database.
"""

import os
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

CACHE_KEY = "cbt:site-settings:v1"
CACHE_SECONDS = 15
LOGO_MAX_MB = 2


def logo_upload_to(instance, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower() or ".png"
    return f"branding/logo_{uuid.uuid4().hex[:12]}{ext}"


def validate_logo_size(file) -> None:
    if file and getattr(file, "size", 0) > LOGO_MAX_MB * 1024 * 1024:
        raise ValidationError(f"The logo is larger than {LOGO_MAX_MB} MB. Please use a smaller image.")


class SiteSettings(models.Model):
    institution_name = models.CharField(
        "institution name",
        max_length=150,
        help_text="Shown on the login page, page headers, login slips, PDF scorecards and Excel exports.",
    )
    tagline = models.CharField(
        max_length=150,
        blank=True,
        default="Computer-Based Test Portal",
        help_text="Small line under the name on the login page. Leave blank to hide it.",
    )
    login_slip_url = models.CharField(
        "address printed on login slips",
        max_length=200,
        blank=True,
        default="",
        help_text="The address students type, e.g. http://192.168.1.10/ . "
        "Leave blank to use the server's configured address.",
    )
    logo = models.ImageField(
        "school logo",
        upload_to=logo_upload_to,
        blank=True,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp", "gif"]), validate_logo_size],
        help_text="PNG or JPG, up to 2 MB. Shown on the login page, page headers, login slips and PDF scorecards.",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self) -> str:
        return self.institution_name

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        self.institution_name = " ".join(self.institution_name.split())
        self.tagline = " ".join(self.tagline.split())
        self.login_slip_url = self.login_slip_url.strip()
        super().save(*args, **kwargs)
        cache.delete(CACHE_KEY)

    def delete(self, *args, **kwargs):  # the single row is never deleted
        return None

    # ----- values with fallbacks ------------------------------------------
    @property
    def display_name(self) -> str:
        return self.institution_name or settings.CBT_SCHOOL_NAME

    @property
    def server_url(self) -> str:
        return self.login_slip_url or settings.CBT_SERVER_URL

    @property
    def logo_url(self) -> str:
        return self.logo.url if self.logo else ""

    @property
    def logo_path(self) -> str:
        """Filesystem path of the logo (for PDFs), or '' if none / missing."""
        try:
            return self.logo.path if self.logo and os.path.exists(self.logo.path) else ""
        except (ValueError, NotImplementedError):
            return ""

    @classmethod
    def load(cls) -> "SiteSettings":
        obj = cache.get(CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1, defaults={"institution_name": settings.CBT_SCHOOL_NAME})
            cache.set(CACHE_KEY, obj, CACHE_SECONDS)
        return obj


def institution_name() -> str:
    """The current institution name, safe to call anywhere (falls back to .env if the DB is unavailable)."""
    try:
        return SiteSettings.load().display_name
    except Exception:  # noqa: BLE001 - e.g. before migrations have run
        return settings.CBT_SCHOOL_NAME
