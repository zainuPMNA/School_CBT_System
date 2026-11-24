import os

from django import forms
from django.conf import settings

from accounts.forms import StyledFormMixin
from accounts.models import StudentProfile, User, cbt_username_validator
from accounts.utils import normalize_class_name, parse_class_list, student_password_problem
from exams.models import Exam, OptionKey, Question

from .models import SiteSettings

ALLOWED_TABULAR_EXTENSIONS = (".xlsx", ".csv")


def validate_tabular_upload(uploaded):
    ext = os.path.splitext(uploaded.name)[1].lower()
    if ext not in ALLOWED_TABULAR_EXTENSIONS:
        raise forms.ValidationError("Upload an Excel (.xlsx) or CSV (.csv) file.")
    limit = settings.CBT_MAX_UPLOAD_MB * 1024 * 1024
    if uploaded.size > limit:
        raise forms.ValidationError(f"File is larger than {settings.CBT_MAX_UPLOAD_MB} MB.")
    return uploaded


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
class StudentCreateForm(StyledFormMixin, forms.Form):
    admission_no = forms.CharField(
        label="Admission No. (username)", max_length=150, validators=[cbt_username_validator]
    )
    full_name = forms.CharField(max_length=150)
    class_name = forms.CharField(
        label="Class / Batch",
        max_length=50,
        help_text="e.g. XII-A",
        widget=forms.TextInput(attrs={"list": "class-options", "autocomplete": "off"}),
    )
    roll_no = forms.CharField(label="Roll No.", max_length=20, required=False)
    password = forms.CharField(
        max_length=64,
        required=False,
        help_text="Leave blank (recommended) to auto-generate a strong, unpredictable password.",
    )

    def clean_admission_no(self):
        value = self.cleaned_data["admission_no"].strip()
        if User.objects.filter(username__iexact=value).exists():
            raise forms.ValidationError("A user with this admission number already exists.")
        return value

    def clean_class_name(self):
        return normalize_class_name(self.cleaned_data["class_name"])

    def clean(self):
        cleaned = super().clean()
        roll = (cleaned.get("roll_no") or "").strip()
        cls = cleaned.get("class_name")
        if roll and cls and StudentProfile.objects.filter(batch_or_class=cls, roll_number=roll).exists():
            self.add_error("roll_no", f"Roll number {roll} is already used in {cls}.")
        cleaned["roll_no"] = roll
        password = cleaned.get("password") or ""
        if password:
            weak = student_password_problem(password, cleaned.get("admission_no", ""), roll, cleaned.get("full_name", ""))
            if weak:
                self.add_error("password", f"This password {weak}. Leave it blank to auto-generate a strong one.")
        return cleaned


class StudentUploadForm(StyledFormMixin, forms.Form):
    file = forms.FileField(
        label="Student list (.xlsx or .csv)",
        validators=[validate_tabular_upload],
        help_text="Columns: admission_no, full_name, class_name, roll_no, password",
    )
    update_existing = forms.BooleanField(
        required=False,
        label="Update students that already exist",
        help_text="Updates name/class/roll; replaces the password only when the row has one. "
        "When unticked, existing admission numbers are skipped.",
    )


# ---------------------------------------------------------------------------
# Exams & questions
# ---------------------------------------------------------------------------
class ExamForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Exam
        fields = [
            "title",
            "subject",
            "target_class",
            "duration_minutes",
            "positive_marks",
            "negative_marks",
            "shuffle_questions",
            "shuffle_options",
            "allow_review_after_publish",
            "instructions",
        ]
        widgets = {
            "target_class": forms.TextInput(attrs={"autocomplete": "off"}),
            "instructions": forms.Textarea(attrs={"rows": 5}),
        }

    def clean_target_class(self):
        classes = parse_class_list(self.cleaned_data["target_class"])
        if not classes:
            raise forms.ValidationError("Choose or type at least one class.")
        value = ", ".join(classes)
        if len(value) > 255:
            raise forms.ValidationError("Too many classes for one exam (the list is limited to 255 characters).")
        return value


class QuestionForm(StyledFormMixin, forms.ModelForm):
    correct_option = forms.ChoiceField(choices=OptionKey.choices, widget=forms.RadioSelect)

    class Meta:
        model = Question
        fields = [
            "sort_order",
            "topic_tag",
            "question_text",
            "question_image",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct_option",
            "explanation",
        ]
        widgets = {
            "question_text": forms.Textarea(attrs={"rows": 5}),
            "option_a": forms.Textarea(attrs={"rows": 2}),
            "option_b": forms.Textarea(attrs={"rows": 2}),
            "option_c": forms.Textarea(attrs={"rows": 2}),
            "option_d": forms.Textarea(attrs={"rows": 2}),
            "explanation": forms.Textarea(attrs={"rows": 3}),
            "question_image": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/gif,image/webp"}),
        }


class QuestionUploadForm(StyledFormMixin, forms.Form):
    MODE_APPEND = "APPEND"
    MODE_REPLACE = "REPLACE"

    file = forms.FileField(
        label="Question sheet (.xlsx or .csv)",
        validators=[validate_tabular_upload],
        help_text="Columns: sort_order, question_text, option_a, option_b, option_c, option_d, "
        "correct_option, explanation, topic_tag",
    )
    mode = forms.ChoiceField(
        choices=[
            (MODE_APPEND, "Add to the existing questions"),
            (MODE_REPLACE, "Replace ALL existing questions of this exam"),
        ],
        initial=MODE_APPEND,
        widget=forms.RadioSelect,
    )


# ---------------------------------------------------------------------------
# Institution settings
# ---------------------------------------------------------------------------
class SiteSettingsForm(StyledFormMixin, forms.ModelForm):
    remove_logo = forms.BooleanField(required=False, label="Remove the current logo")

    class Meta:
        model = SiteSettings
        fields = ["institution_name", "tagline", "login_slip_url", "logo"]
        widgets = {
            "institution_name": forms.TextInput(attrs={"placeholder": "e.g. Springfield Public School"}),
            "login_slip_url": forms.TextInput(attrs={"placeholder": "http://192.168.1.10/"}),
            "logo": forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}),
        }

    def clean(self):
        cleaned = super().clean()
        # A newly chosen file wins over the "remove" tick.
        if cleaned.get("remove_logo") and not self.files.get("logo"):
            cleaned["logo"] = None
            self.instance.logo = None
        return cleaned

    def clean_institution_name(self):
        value = " ".join(self.cleaned_data["institution_name"].split())
        if not value:
            raise forms.ValidationError("Enter the institution name.")
        return value

    def clean_login_slip_url(self):
        value = self.cleaned_data["login_slip_url"].strip()
        if value and not value.lower().startswith(("http://", "https://")):
            value = "http://" + value
        if value and not value.endswith("/"):
            value += "/"
        return value


# ---------------------------------------------------------------------------
# Backup & restore
# ---------------------------------------------------------------------------
class BackupCreateForm(StyledFormMixin, forms.Form):
    password = forms.CharField(
        label="Backup password (recommended)", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Locks the file with AES-256 encryption. Without this password nobody can open "
                  "or restore it, so write it down and keep it safe. Leave blank for no password.",
    )
    password2 = forms.CharField(
        label="Type the password again", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    keep_copy = forms.BooleanField(
        label="Also keep a copy on this computer", required=False, initial=True,
    )

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password", ""), data.get("password2", "")
        if p1 or p2:
            if p1 != p2:
                self.add_error("password2", "The two passwords do not match.")
            elif len(p1) < 8:
                self.add_error("password", "Use at least 8 characters.")
        return data


def validate_backup_upload(uploaded):
    if not uploaded.name.lower().endswith(".cbtbackup"):
        raise forms.ValidationError("Choose a CBT backup file (it ends in .cbtbackup).")
    limit = settings.CBT_BACKUP_MAX_UPLOAD_MB * 1024 * 1024
    if uploaded.size > limit:
        raise forms.ValidationError(f"File is larger than {settings.CBT_BACKUP_MAX_UPLOAD_MB} MB.")
    return uploaded


class BackupUploadForm(StyledFormMixin, forms.Form):
    file = forms.FileField(label="Backup file (.cbtbackup)", validators=[validate_backup_upload])
    password = forms.CharField(
        label="Backup password", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
        help_text="Only if the backup was made with a password.",
    )


class BackupRestoreForm(StyledFormMixin, forms.Form):
    backup_password = forms.CharField(
        label="Backup password", required=False, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
    )
    admin_password = forms.CharField(
        label="Your own admin password", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text="Confirms that it is really you.",
    )
    confirm = forms.CharField(label='Type RESTORE to confirm', max_length=20)

    def __init__(self, *args, user=None, encrypted=False, **kwargs):
        self.user, self.encrypted = user, encrypted
        super().__init__(*args, **kwargs)
        if not encrypted:
            del self.fields["backup_password"]

    def clean_admin_password(self):
        pw = self.cleaned_data["admin_password"]
        if not self.user or not self.user.check_password(pw):
            raise forms.ValidationError("That is not your password.")
        return pw

    def clean_confirm(self):
        value = self.cleaned_data["confirm"].strip()
        if value != "RESTORE":
            raise forms.ValidationError("Type the word RESTORE in capital letters.")
        return value

    def clean_backup_password(self):
        pw = self.cleaned_data.get("backup_password", "")
        if self.encrypted and not pw:
            raise forms.ValidationError("This backup is password-protected. Enter its password.")
        return pw
