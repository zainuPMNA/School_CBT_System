from django import forms
from django.contrib.auth.forms import AuthenticationForm


class StyledFormMixin:
    """Attach the shared Tailwind component classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                css = "checkbox"
            elif isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                css = "radio"
            elif isinstance(widget, forms.FileInput):
                css = "file-input"
            else:
                css = "input"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class CBTLoginForm(StyledFormMixin, AuthenticationForm):
    error_messages = {
        "invalid_login": "Incorrect username or password. Check your login slip and try again.",
        "inactive": "This account is disabled. Please contact the invigilator.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields["username"].label = "Username / Admission No."
        self.fields["username"].widget.attrs.update(
            {
                "autofocus": True,
                "autocomplete": "off",
                "autocapitalize": "none",
                "spellcheck": "false",
                "placeholder": "e.g. 2024001",
            }
        )
        self.fields["password"].widget.attrs.update({"autocomplete": "off", "placeholder": "Password"})

    def clean_username(self):
        return (self.cleaned_data.get("username") or "").strip()
