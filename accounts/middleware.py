"""
Single-device session enforcement.

Every authenticated STUDENT request must carry the session key recorded in
`User.active_session_key`. If it does not (the student logged in on another
PC in REPLACE mode, or an admin reset the session), this browser is logged
out immediately:
  * normal page loads  -> redirect to /login/?reason=session_ended
  * AJAX / fetch calls -> HTTP 401 JSON {"error": "session_invalid", ...}
  * HTMX requests      -> HTTP 401 with an HX-Redirect header
"""

import logging

from django.contrib.auth import logout
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse

logger = logging.getLogger("cbt")

EXEMPT_PREFIXES = ("/static/", "/media/", "/healthz/", "/favicon.ico")


def wants_json(request) -> bool:
    accept = request.headers.get("Accept", "")
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in accept
        or request.content_type == "application/json"
    )


class SingleSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(EXEMPT_PREFIXES):
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated and getattr(user, "is_student_role", False):
                current_key = request.session.session_key
                if not current_key or current_key != user.active_session_key:
                    return self._reject(request, user)
        return self.get_response(request)

    def _reject(self, request, user):
        logger.info("Ending stale session for %s (bound key differs).", user.username)
        logout(request)
        login_url = f"{reverse('accounts:login')}?reason=session_ended"
        if request.headers.get("HX-Request"):
            response = JsonResponse({"error": "session_invalid", "redirect": login_url}, status=401)
            response["HX-Redirect"] = login_url
            return response
        if wants_json(request):
            return JsonResponse(
                {
                    "error": "session_invalid",
                    "message": "You were logged out because this account was used on another computer "
                    "or your session was reset by the invigilator.",
                    "redirect": login_url,
                },
                status=401,
            )
        return HttpResponseRedirect(login_url)
