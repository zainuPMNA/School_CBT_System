"""WSGI entry point used by Gunicorn: `gunicorn cbt_project.wsgi:application`."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cbt_project.settings")

application = get_wsgi_application()
