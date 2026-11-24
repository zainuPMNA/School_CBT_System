"""
Django settings for the offline-first LAN Computer-Based Test (CBT) platform.

Design rules enforced here:
  * Every setting comes from environment variables (optionally loaded from a
    `.env` file in the project root), so the same code runs on a developer
    laptop and on the Ubuntu lab server.
  * Nothing references an external host. All CSS/JS/fonts (Tailwind build,
    Alpine.js, HTMX, KaTeX) live under `static/vendor/` and are served by
    Nginx (production) or WhiteNoise (fallback) — the app works 100% offline.
  * PostgreSQL is mandatory in production. SQLite is accepted only with
    DJANGO_DEBUG=True for quick local experiments.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Environment helpers (no third-party dependency needed)
# ---------------------------------------------------------------------------
def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file without overriding real env vars."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(f"Environment variable {name} must be an integer.") from exc


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is False.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Project apps
    "accounts.apps.AccountsConfig",
    "exams.apps.ExamsConfig",
    "dashboard.apps.DashboardConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.SingleSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "cbt_project.urls"
WSGI_APPLICATION = "cbt_project.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.cbt_settings",
            ],
        },
    },
]

AUTH_USER_MODEL = "accounts.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"


# ---------------------------------------------------------------------------
# Database — PostgreSQL tuned for ~60-100 concurrent exam takers
# ---------------------------------------------------------------------------
DB_ENGINE = (env("DB_ENGINE", "postgresql") or "postgresql").lower()

if DB_ENGINE == "sqlite":
    if not DEBUG:
        raise ImproperlyConfigured(
            "SQLite is only allowed for local development (DJANGO_DEBUG=True). "
            "Use PostgreSQL in production."
        )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "dev.sqlite3",
            # SQLite has one writer at a time: take the write lock up front and
            # wait for it instead of failing with "database is locked".
            "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 30, "init_command": "PRAGMA journal_mode=WAL;"},
        }
    }
elif DB_ENGINE in {"postgresql", "postgres"}:
    _use_pool = env_bool("DB_POOL", True)
    _pg_options: dict = {
        "connect_timeout": 5,
        "application_name": "cbt_lan",
    }
    if _use_pool:
        # Django 5.1+ native psycopg pool: one small pool per Gunicorn worker
        # process. Total server connections = workers x DB_POOL_MAX.
        _pg_options["pool"] = {
            "min_size": env_int("DB_POOL_MIN", 2),
            "max_size": env_int("DB_POOL_MAX", 6),
            "timeout": 10,
        }
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME", "cbt_db"),
            "USER": env("DB_USER", "cbt_user"),
            "PASSWORD": env("DB_PASSWORD", ""),
            "HOST": env("DB_HOST", "127.0.0.1"),
            "PORT": env("DB_PORT", "5432"),
            # Persistent connections and the pool are mutually exclusive.
            "CONN_MAX_AGE": 0 if _use_pool else 60,
            "CONN_HEALTH_CHECKS": not _use_pool,
            # Auto-save endpoints manage their own short transactions.
            "ATOMIC_REQUESTS": False,
            "OPTIONS": _pg_options,
        }
    }
else:
    raise ImproperlyConfigured(f"Unsupported DB_ENGINE '{DB_ENGINE}'. Use 'postgresql'.")


# ---------------------------------------------------------------------------
# Authentication & sessions
# ---------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    # Admission numbers are matched case-insensitively ("adm001" == "ADM001").
    "accounts.backends.CaseInsensitiveModelBackend",
]

# Django's default PBKDF2 (1,000,000 iterations) costs ~0.5-1 s of CPU per
# login. With 60-75 students logging in within the same minute on a 4-core
# server that queues logins for 10-20 s. The first hasher below is identical
# PBKDF2-SHA256 with a configurable, LAN-appropriate iteration count. Existing
# hashes from the other hashers are verified and transparently re-hashed.
PASSWORD_HASHERS = [
    "accounts.hashers.LabPBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
CBT_PASSWORD_HASH_ITERATIONS = env_int("CBT_PASSWORD_HASH_ITERATIONS", 100_000)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 6},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# Database-backed sessions are REQUIRED: single-device lock deletes the
# previous Session row, which must be visible to every Gunicorn worker.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "cbt_sessionid"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 6 * 60 * 60)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_NAME = "cbt_csrftoken"
CSRF_COOKIE_SAMESITE = "Lax"
# A stale form token (login page open in another tab, Back button) gets a
# friendly recovery instead of Django's technical 403 page.
CSRF_FAILURE_VIEW = "accounts.views.csrf_failure"

USE_HTTPS = env_bool("USE_HTTPS", False)
SESSION_COOKIE_SECURE = USE_HTTPS
CSRF_COOKIE_SECURE = USE_HTTPS
if USE_HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# ---------------------------------------------------------------------------
# Security headers (all LAN-safe; no external resources allowed)
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"


# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static & media files — 100% local, zero CDN
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"          # `collectstatic` target, served by Nginx
STATICFILES_DIRS = [BASE_DIR / "static"]       # vendor/ (KaTeX, Alpine, HTMX) + css/ + js/

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

# Bulk Excel/CSV uploads and question images
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000          # admin inline with 100+ questions
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755


# ---------------------------------------------------------------------------
# Cache (per-process; nothing correctness-critical is cached)
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cbt-default",
    }
}


# ---------------------------------------------------------------------------
# CBT application settings
# ---------------------------------------------------------------------------
CBT_SCHOOL_NAME = env("CBT_SCHOOL_NAME", "School CBT Portal")
CBT_SERVER_URL = env("CBT_SERVER_URL", "http://192.168.1.10/")

CBT_SINGLE_SESSION_MODE = (env("CBT_SINGLE_SESSION_MODE", "BLOCK") or "BLOCK").upper()
if CBT_SINGLE_SESSION_MODE not in {"BLOCK", "REPLACE"}:
    raise ImproperlyConfigured("CBT_SINGLE_SESSION_MODE must be BLOCK or REPLACE.")

CBT_TIMER_GRACE_SECONDS = env_int("CBT_TIMER_GRACE_SECONDS", 5)
CBT_TIMER_SYNC_INTERVAL_SECONDS = env_int("CBT_TIMER_SYNC_INTERVAL_SECONDS", 30)
CBT_HEARTBEAT_INTERVAL_SECONDS = env_int("CBT_HEARTBEAT_INTERVAL_SECONDS", 15)
CBT_MONITOR_REFRESH_SECONDS = env_int("CBT_MONITOR_REFRESH_SECONDS", 10)
CBT_GENERATED_PASSWORD_LENGTH = env_int("CBT_GENERATED_PASSWORD_LENGTH", 10)
CBT_MAX_QUESTION_IMAGE_MB = env_int("CBT_MAX_QUESTION_IMAGE_MB", 2)
CBT_TRUST_X_REAL_IP = env_bool("CBT_TRUST_X_REAL_IP", True)
# In BLOCK mode, let a student log in again from the SAME lab PC (same IP)
# after the browser was closed/crashed, without needing an admin unlock.
CBT_ALLOW_SAME_IP_RELOGIN = env_bool("CBT_ALLOW_SAME_IP_RELOGIN", True)
CBT_MAX_UPLOAD_MB = env_int("CBT_MAX_UPLOAD_MB", 5)
# Ask for full screen on the exam page (skipped automatically inside Safe Exam Browser).
CBT_REQUIRE_FULLSCREEN = env_bool("CBT_REQUIRE_FULLSCREEN", True)
# Auto-submit an attempt after this many violations. 0 = never (log only).
CBT_MAX_VIOLATIONS = env_int("CBT_MAX_VIOLATIONS", 0)

# Backups made from the admin "Backup" page (and by `manage.py cbt_backup`).
# Kept OUTSIDE the media folder so they are never served over the web.
CBT_BACKUP_DIR = Path(env("CBT_BACKUP_DIR", str(BASE_DIR / "backups")))
CBT_BACKUP_KEEP = env_int("CBT_BACKUP_KEEP", 20)            # saved copies kept on this computer
CBT_BACKUP_MAX_UPLOAD_MB = env_int("CBT_BACKUP_MAX_UPLOAD_MB", 500)


# ---------------------------------------------------------------------------
# Logging — console (journald under systemd) + rotating file
# ---------------------------------------------------------------------------
LOG_DIR = Path(env("DJANGO_LOG_DIR", str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s (pid %(process)d): %(message)s",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "cbt.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
        "cbt": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
    },
}
