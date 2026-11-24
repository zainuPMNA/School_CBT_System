"""Admin "Backup" page: download a backup, keep copies, upload one and restore it."""
import logging
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from . import backup as bk
from .forms import BackupCreateForm, BackupRestoreForm, BackupUploadForm
from .mixins import AdminRequiredMixin
from .models import SiteSettings

logger = logging.getLogger(__name__)
PREVIEW_KEY = "cbt_backup_previews"


def _download_name() -> str:
    school = re.sub(r"[^A-Za-z0-9]+", "_", SiteSettings.load().institution_name).strip("_")[:40] or "School"
    return f"CBT_Backup_{school}_{timezone.localtime():%Y-%m-%d_%H%M}{bk.EXTENSION}"


def _remember_preview(request, name: str, summary: dict) -> None:
    previews = request.session.get(PREVIEW_KEY, {})
    previews[name] = summary
    request.session[PREVIEW_KEY] = dict(list(previews.items())[-10:])


def _preview(request, name: str, path) -> dict | None:
    """Details of a saved copy: read it directly, or from the upload check when it is encrypted."""
    known = request.session.get(PREVIEW_KEY, {}).get(name)
    if known:
        return known
    with path.open("rb") as fh:
        if bk.is_encrypted(fh.read(len(bk.MAGIC))):
            return None
    try:
        return bk.read_backup(path.read_bytes()).summary
    except bk.BackupError:
        return None


class BackupPageMixin(AdminRequiredMixin):
    nav_section = "backup"

    def page(self, request, *, create_form=None, upload_form=None, status=200):
        return render(request, "dashboard/backup.html", {
            "nav_section": self.nav_section,
            "create_form": create_form or BackupCreateForm(),
            "upload_form": upload_form or BackupUploadForm(),
            "saved": bk.list_saved(),
            "backup_dir": bk.backup_dir(),
            "keep": settings.CBT_BACKUP_KEEP,
            "max_upload_mb": settings.CBT_BACKUP_MAX_UPLOAD_MB,
            "exam_running": bk.exam_running(),
        }, status=status)


class BackupView(BackupPageMixin, View):
    def get(self, request):
        return self.page(request)


class BackupCreateView(BackupPageMixin, View):
    def post(self, request):
        form = BackupCreateForm(request.POST)
        if not form.is_valid():
            return self.page(request, create_form=form, status=400)
        password = form.cleaned_data["password"]
        content = bk.create_backup(by=request.user.username, password=password)
        if form.cleaned_data["keep_copy"]:
            saved = bk.save_backup(content, kind="manual")
            messages.success(request, f"Backup made and a copy kept here as {saved.name}.")
        logger.info("Backup downloaded by %s (encrypted=%s, %d bytes)", request.user.username, bool(password), len(content))
        response = HttpResponse(content, content_type="application/octet-stream")
        response["Content-Disposition"] = f'attachment; filename="{_download_name()}"'
        response["Cache-Control"] = "no-store"
        return response


class BackupDownloadView(AdminRequiredMixin, View):
    def get(self, request, name):
        try:
            path = bk.saved_path(name)
        except bk.BackupError as exc:
            messages.error(request, str(exc))
            return redirect("dashboard:backup")
        response = FileResponse(path.open("rb"), as_attachment=True, filename=name,
                                content_type="application/octet-stream")
        response["Cache-Control"] = "no-store"
        return response


class BackupDeleteView(AdminRequiredMixin, View):
    def post(self, request, name):
        try:
            bk.saved_path(name).unlink()
            messages.success(request, f"Deleted {name}.")
            logger.info("Saved backup %s deleted by %s", name, request.user.username)
        except (bk.BackupError, OSError) as exc:
            messages.error(request, str(exc) or "Could not delete that file.")
        return redirect("dashboard:backup")


class BackupUploadView(BackupPageMixin, View):
    def post(self, request):
        form = BackupUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.page(request, upload_form=form, status=400)
        blob = form.cleaned_data["file"].read()
        try:
            backup = bk.read_backup(blob, form.cleaned_data["password"])
            bk.check_restorable(backup)
        except bk.BackupError as exc:
            form.add_error(None, str(exc))
            return self.page(request, upload_form=form, status=400)
        saved = bk.save_backup(blob, kind="uploaded")  # kept exactly as uploaded (still encrypted)
        _remember_preview(request, saved.name, backup.summary)
        messages.success(request, "The backup file was checked and is complete. Nothing has been changed yet.")
        return redirect("dashboard:backup_restore", name=saved.name)


class BackupRestoreView(AdminRequiredMixin, View):
    nav_section = "backup"

    def _context(self, request, name, path, form):
        return {
            "nav_section": self.nav_section,
            "name": name,
            "preview": _preview(request, name, path),
            "form": form,
            "encrypted": form.encrypted,
            "exam_running": bk.exam_running(),
        }

    def _load(self, request, name):
        path = bk.saved_path(name)
        with path.open("rb") as fh:
            encrypted = bk.is_encrypted(fh.read(len(bk.MAGIC)))
        return path, encrypted

    def get(self, request, name):
        try:
            path, encrypted = self._load(request, name)
        except bk.BackupError as exc:
            messages.error(request, str(exc))
            return redirect("dashboard:backup")
        form = BackupRestoreForm(user=request.user, encrypted=encrypted)
        return render(request, "dashboard/backup_restore.html", self._context(request, name, path, form))

    def post(self, request, name):
        try:
            path, encrypted = self._load(request, name)
        except bk.BackupError as exc:
            messages.error(request, str(exc))
            return redirect("dashboard:backup")
        form = BackupRestoreForm(request.POST, user=request.user, encrypted=encrypted)
        if form.is_valid():
            if bk.exam_running():
                form.add_error(None, "Students are writing a test right now. Restore after the test has ended.")
            else:
                try:
                    backup = bk.read_backup(path.read_bytes(), form.cleaned_data.get("backup_password", ""))
                    safety = bk.restore_backup(backup, by=request.user.username)
                except bk.BackupError as exc:
                    form.add_error(None, str(exc))
                except Exception:  # noqa: BLE001 — the transaction rolled back; tell the admin plainly
                    logger.exception("Restore of %s failed", name)
                    form.add_error(None, "The restore failed and was cancelled. Your data was not changed. "
                                         "See the server log for details.")
                else:
                    logout(request)
                    messages.success(
                        request,
                        f"Restore complete: data from {backup.summary['created_display']} is back. "
                        f"Everyone has been signed out — please sign in again. "
                        f"(The data from before the restore was kept as {safety.name}.)",
                    )
                    return redirect("accounts:login")
        return render(request, "dashboard/backup_restore.html",
                      self._context(request, name, path, form), status=400)
