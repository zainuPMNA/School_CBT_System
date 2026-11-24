"""ASGI entry point (not used in the default Gunicorn/WSGI deployment)."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cbt_project.settings")

application = get_asgi_application()
