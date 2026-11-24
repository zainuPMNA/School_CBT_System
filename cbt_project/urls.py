"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.utils.functional import lazy

from .views import healthz


def _admin_header() -> str:
    from dashboard.models import institution_name  # imported late: models load after urls

    return f"{institution_name()} — CBT Administration"


# Evaluated on every admin page, so a name changed on the Settings page shows at once.
admin.site.site_header = lazy(_admin_header, str)()
admin.site.site_title = "CBT Admin"
admin.site.index_title = "Exam Control Panel"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("dashboard/", include("dashboard.urls")),
    path("exam/", include("exams.urls")),
    path("", include("accounts.urls")),
]

if settings.DEBUG:
    # In production Nginx serves /media/ directly.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
