from django.conf import settings

from dashboard.models import SiteSettings


def cbt_settings(request):
    """Expose institution settings (editable on the dashboard Settings page) to every template."""
    try:
        site = SiteSettings.load()
        name, tagline, server_url, logo_url = site.display_name, site.tagline, site.server_url, site.logo_url
    except Exception:  # noqa: BLE001 - DB not migrated yet: fall back to .env values
        name, tagline, server_url, logo_url = (
            settings.CBT_SCHOOL_NAME, "Computer-Based Test Portal", settings.CBT_SERVER_URL, "",
        )
    return {
        "CBT_SCHOOL_NAME": name,
        "CBT_TAGLINE": tagline,
        "CBT_SERVER_URL": server_url,
        "CBT_LOGO_URL": logo_url,
        "CBT_SINGLE_SESSION_MODE": settings.CBT_SINGLE_SESSION_MODE,
        # Signed-in users: their saved theme. Visitors: "" -> the browser's last choice is used.
        "CBT_THEME": _user_theme(request),
    }


def _user_theme(request) -> str:
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return getattr(user, "theme", "") or "light"
    return ""
