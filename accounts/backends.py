from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    """Authenticate with a case-insensitive, whitespace-trimmed username."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        if username is None:
            username = kwargs.get(user_model.USERNAME_FIELD)
        if username is None or password is None:
            return None
        username = str(username).strip()

        candidates = list(user_model._default_manager.filter(username__iexact=username)[:2])
        if len(candidates) == 1:
            user = candidates[0]
        else:
            # Two accounts differing only by case: require the exact spelling.
            user = next((c for c in candidates if c.username == username), None)

        if user is None:
            # Run the hasher once to reduce timing differences (as ModelBackend does).
            user_model().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
