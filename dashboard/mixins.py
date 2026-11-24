"""
Access rules
------------
* StaffRequiredMixin  — ADMIN or TEACHER: exams, questions, student list,
  session unlock (teachers invigilate the labs).
* AdminRequiredMixin  — ADMIN only: creating/importing students, password
  resets and login slips (they show passwords), deleting exams.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache


@method_decorator(never_cache, name="dispatch")
class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    nav_section = ""

    def test_func(self):
        return getattr(self.request.user, "is_staff_member", False)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["nav_section"] = self.nav_section
        return ctx


class AdminRequiredMixin(StaffRequiredMixin):
    def test_func(self):
        return getattr(self.request.user, "is_admin_role", False)


def safe_next_url(request, default: str) -> str:
    """Return ?next= / POST next if it points back to this site, else default."""
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return default
