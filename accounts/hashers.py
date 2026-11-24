from django.conf import settings
from django.contrib.auth.hashers import PBKDF2PasswordHasher


class LabPBKDF2PasswordHasher(PBKDF2PasswordHasher):
    """
    Standard PBKDF2-SHA256 with an iteration count tuned for a burst of
    60-100 simultaneous logins on a small lab server
    (settings.CBT_PASSWORD_HASH_ITERATIONS, default 100,000).

    A distinct algorithm name keeps it separate from Django's own hasher, so
    hashes created by either are verified correctly and upgraded on login.
    """

    algorithm = "lab_pbkdf2_sha256"

    @property
    def iterations(self) -> int:  # type: ignore[override]
        return int(getattr(settings, "CBT_PASSWORD_HASH_ITERATIONS", 100_000))
